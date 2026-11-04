"""Base entities for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from typing import Final, override

from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import MANUFACTURER
from .coordinator import AnovaCoordinator, AnovaData
from .protocol import AnovaDeviceInfo, AnovaState, TemperatureUnit

# Home Assistant unit for each unit the cooker can be set to.
HA_TEMPERATURE_UNITS: Final = {
    TemperatureUnit.CELSIUS: UnitOfTemperature.CELSIUS,
    TemperatureUnit.FAHRENHEIT: UnitOfTemperature.FAHRENHEIT,
}


class AnovaEntity(CoordinatorEntity[AnovaCoordinator]):
    """Base class for every Anova entity.

    ``key`` is both the translation key and the suffix of the unique id.
    Entities stay available while the cooker is offline and show the last
    known values; ``state`` is None only before the very first poll.
    """

    _attr_has_entity_name = True

    def __init__(self, data: AnovaData, key: str) -> None:
        """Initialize the entity and attach it to the cooker device."""
        super().__init__(data.coordinator)
        self._client = data.client
        address = data.coordinator.address
        info = data.coordinator.device_info or AnovaDeviceInfo(None, None)
        self._attr_translation_key = key
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer=MANUFACTURER,
            model=info.model,
            name=data.coordinator.config_entry.title,
            sw_version=info.firmware_version,
        )

    @property
    def state_data(self) -> AnovaState | None:
        """Return the last known poll, live or frozen."""
        return self.coordinator.data

    @property
    def cooker_unit(self) -> TemperatureUnit:
        """Return the last known cooker unit, Celsius before the first poll."""
        data = self.coordinator.data
        return data.unit if data else TemperatureUnit.CELSIUS


class AnovaControlEntity(AnovaEntity):
    """An entity that only sends commands: unavailable while offline."""

    @property
    @override
    def available(self) -> bool:
        """Return True while the cooker answers."""
        return super().available and self.coordinator.online
