"""Sensors for OPPD."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OppdUtilityConfigEntry
from .const import DOMAIN
from .coordinator import OppdDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OppdUtilityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OPPD sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            OppdLatestUsageSensor(coordinator, entry),
            OppdLastRefreshSensor(coordinator, entry),
        ]
    )


class OppdBaseSensor(CoordinatorEntity[OppdDataUpdateCoordinator], SensorEntity):
    """Base OPPD sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OppdDataUpdateCoordinator,
        entry: OppdUtilityConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        account_number = coordinator.api.account_number
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_number)},
            name=f"OPPD {account_number[-4:]}",
            manufacturer="Omaha Public Power District",
            model="MyOPPD",
        )


class OppdLatestUsageSensor(OppdBaseSensor):
    """Latest completed OPPD usage interval.

    This display entity intentionally has no state class. Historical Energy
    Dashboard data is supplied by the correctly timestamped external statistic.
    """

    _attr_name = "Latest Electric Consumption"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_latest_electric_consumption"

    @property
    def native_value(self):
        latest = self.coordinator.data.get("latest")
        return latest.consumption_kwh if latest is not None else None

    @property
    def extra_state_attributes(self):
        latest = self.coordinator.data.get("latest")
        if latest is None:
            return {"statistic_id": self.coordinator.statistic_id}
        return {
            "period_start": latest.start.isoformat(),
            "period_end": latest.end.isoformat() if latest.end else None,
            "meter_number": latest.meter_number,
            "history_records": len(self.coordinator.data.get("history", [])),
            "statistic_id": self.coordinator.statistic_id,
            "periodicity": self.coordinator.data.get("periodicity"),
            "statistics_allocation": self.coordinator.data.get("statistics_allocation"),
        }


class OppdLastRefreshSensor(OppdBaseSensor):
    """Last successful OPPD refresh."""

    _attr_name = "Last Refresh"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_last_refresh"

    @property
    def native_value(self):
        return self.coordinator.data.get("retrieved_at")
