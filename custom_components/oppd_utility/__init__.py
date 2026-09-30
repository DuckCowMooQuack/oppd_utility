"""OPPD integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import OppdApi, OppdTokens
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_NUMBER,
    CONF_HISTORY_DAYS,
    CONF_METER_NUMBER,
    CONF_REFRESH_TOKEN,
    CONF_STATISTICS_VERSION,
    CONF_UPDATE_INTERVAL_HOURS,
    CONF_USER_UUID,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_UPDATE_INTERVAL_HOURS,
)
from .coordinator import OppdDataUpdateCoordinator

PLATFORMS = [Platform.SENSOR]

type OppdUtilityConfigEntry = ConfigEntry[OppdRuntimeData]


@dataclass
class OppdRuntimeData:
    """Runtime data for one OPPD account."""

    coordinator: OppdDataUpdateCoordinator
    session: ClientSession


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OppdUtilityConfigEntry,
) -> bool:
    """Set up OPPD from a config entry."""
    session = async_create_clientsession(hass)
    api = OppdApi(
        session,
        entry.data[CONF_ACCESS_TOKEN],
        entry.data[CONF_REFRESH_TOKEN],
        entry.data[CONF_USER_UUID],
        entry.data[CONF_ACCOUNT_NUMBER],
        entry.data.get(CONF_METER_NUMBER, ""),
    )

    @callback
    def save_tokens(tokens: OppdTokens) -> None:
        if tokens.access_token == entry.data.get(
            CONF_ACCESS_TOKEN
        ) and tokens.refresh_token == entry.data.get(CONF_REFRESH_TOKEN):
            return
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: tokens.access_token,
                CONF_REFRESH_TOKEN: tokens.refresh_token,
            },
        )

    @callback
    def save_statistics_version(version: int) -> None:
        if entry.data.get(CONF_STATISTICS_VERSION) == version:
            return
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_STATISTICS_VERSION: version},
        )

    coordinator = OppdDataUpdateCoordinator(
        hass,
        api,
        update_interval=timedelta(
            hours=entry.options.get(
                CONF_UPDATE_INTERVAL_HOURS,
                DEFAULT_UPDATE_INTERVAL_HOURS,
            )
        ),
        history_days=entry.options.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS),
        token_callback=save_tokens,
        statistics_version=entry.data.get(CONF_STATISTICS_VERSION, 1),
        statistics_version_callback=save_statistics_version,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        session.detach()
        raise

    entry.runtime_data = OppdRuntimeData(coordinator=coordinator, session=session)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: OppdUtilityConfigEntry,
) -> bool:
    """Unload OPPD."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant,
    entry: OppdUtilityConfigEntry,
) -> None:
    """Reload after an options update."""
    await hass.config_entries.async_reload(entry.entry_id)
