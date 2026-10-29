"""Base entity for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from typing import Final

from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import MANUFACTURER
from .coordinator import AnovaCoordinator, AnovaData
from .protocol import TemperatureUnit

# Home Assistant unit for each unit the cooker can be set to.
HA_TEMPERATURE_UNITS: Final = {
    TemperatureUnit.CELSIUS: UnitOfTemperature.CELSIUS,
    TemperatureUnit.FAHRENHEIT: UnitOfTemperature.FAHRENHEIT,
}


class AnovaEntity(CoordinatorEntity[AnovaCoordinator]):
    """Base class for every Anova entity.

    ``key`` is both the translation key and the suffix of the unique id.
    """

    _attr_has_entity_name = True

    def __init__(self, data: AnovaData, key: str) -> None:
        """Initialize the entity and attach it to the cooker device."""
        super().__init__(data.coordinator)
        self._client = data.client
        address = data.coordinator.address
        self._attr_translation_key = key
        self._attr_unique_id = f"{address}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer=MANUFACTURER,
            model=data.device_info.model,
            name=data.coordinator.config_entry.title,
            sw_version=data.device_info.firmware_version,
        )
