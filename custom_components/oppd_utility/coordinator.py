"""Data coordinator and Energy Dashboard statistics for OPPD."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import ClientError
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util.unit_conversion import EnergyConverter

from .api import (
    OppdApi,
    OppdApiError,
    OppdAuthError,
    OppdTokens,
    allocate_intervals_daily,
)
from .const import (
    CORRECTION_WINDOW_DAYS,
    DOMAIN,
    PERIODICITY_DAILY,
    STATISTIC_ID_PREFIX,
    STATISTICS_VERSION,
)

_LOGGER = logging.getLogger(__name__)
_OPPD_TIME_ZONE = ZoneInfo("America/Chicago")


class OppdDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch OPPD data and maintain stable cumulative statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: OppdApi,
        *,
        update_interval: timedelta,
        history_days: int,
        token_callback: Callable[[OppdTokens], None],
        statistics_version: int,
        statistics_version_callback: Callable[[int], None],
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.api = api
        self.history_days = history_days
        self._token_callback = token_callback
        self._statistics_version_callback = statistics_version_callback
        self._needs_statistics_migration = statistics_version < STATISTICS_VERSION
        account_key = re.sub(r"[^a-z0-9]+", "_", api.account_number.lower()).strip("_")
        self.statistic_id = f"{DOMAIN}:{STATISTIC_ID_PREFIX}_{account_key}"

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh tokens, fetch interval usage, and import statistics."""
        try:
            tokens = await self.api.async_refresh_tokens()
            self._token_callback(tokens)

            last_stat = await self._async_last_statistic()
            today = datetime.now(_OPPD_TIME_ZONE).date()
            if last_stat is None or self._needs_statistics_migration:
                start_date = today - timedelta(days=self.history_days)
            else:
                last_start = datetime.fromtimestamp(
                    float(last_stat["start"]),
                    tz=timezone.utc,
                )
                start_date = last_start.astimezone(_OPPD_TIME_ZONE).date() - timedelta(
                    days=CORRECTION_WINDOW_DAYS
                )

            usage = await self.api.async_get_electric_usage(start_date, today)
            periodicity = PERIODICITY_DAILY
            if not usage:
                usage = await self.api.async_get_electric_usage(
                    start_date,
                    today,
                    periodicity="MO",
                )
                periodicity = "MO"

            statistics_usage = usage
            allocation = "reported"
            if periodicity == "MO":
                statistics_usage = allocate_intervals_daily(usage)
                allocation = "estimated_daily"

            if self._needs_statistics_migration and usage:
                await self._async_clear_statistics()
            await self._async_import_history(statistics_usage)
            if self._needs_statistics_migration and usage:
                self._statistics_version_callback(STATISTICS_VERSION)
                self._needs_statistics_migration = False

        except OppdAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (OppdApiError, ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(str(err)) from err

        return {
            "retrieved_at": datetime.now(timezone.utc),
            "account_number": self.api.account_number,
            "meter_number": self.api.meter_number or None,
            "history": usage,
            "latest": usage[-1] if usage else None,
            "statistic_id": self.statistic_id,
            "periodicity": periodicity,
            "statistics_allocation": allocation,
        }

    async def _async_clear_statistics(self) -> None:
        """Clear legacy statistics and wait for the recorder migration."""
        done = asyncio.Event()

        @callback
        def clear_done() -> None:
            self.hass.loop.call_soon_threadsafe(done.set)

        _LOGGER.info("Migrating OPPD electric usage statistics to daily allocation")
        get_instance(self.hass).async_clear_statistics(
            [self.statistic_id],
            on_done=clear_done,
        )
        await asyncio.wait_for(done.wait(), timeout=30)

    async def _async_last_statistic(self) -> dict[str, Any] | None:
        stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics,
            self.hass,
            1,
            self.statistic_id,
            True,
            set(),
        )
        records = stats.get(self.statistic_id, [])
        return records[0] if records else None

    async def _async_import_history(self, usage) -> None:
        if not usage:
            _LOGGER.debug("OPPD returned no electric usage records")
            return

        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"OPPD {self.api.account_number[-4:]} Electric Consumption",
            source=DOMAIN,
            statistic_id=self.statistic_id,
            unit_class=EnergyConverter.UNIT_CLASS,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )

        baseline_time: float | None = None
        cumulative_sum = 0.0
        first_start = usage[0].start

        existing = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            first_start,
            None,
            {self.statistic_id},
            "hour",
            None,
            {"sum"},
        )
        existing_records = existing.get(self.statistic_id, [])
        if existing_records:
            anchor = existing_records[0]
            baseline_time = float(anchor["start"])
            cumulative_sum = float(anchor.get("sum") or 0.0)

        statistics: list[StatisticData] = []
        for record in usage:
            timestamp = record.start.timestamp()
            if baseline_time is not None and timestamp <= baseline_time:
                continue
            value = max(0.0, float(record.consumption_kwh))
            cumulative_sum += value
            statistics.append(
                StatisticData(
                    start=record.start,
                    state=value,
                    sum=cumulative_sum,
                )
            )

        if statistics:
            _LOGGER.info(
                "Importing %d OPPD electric usage records into %s",
                len(statistics),
                self.statistic_id,
            )
            async_add_external_statistics(self.hass, metadata, statistics)
