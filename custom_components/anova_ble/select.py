"""Temperature unit select for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from typing import Final, override

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AnovaConfigEntry, AnovaData
from .entity import AnovaEntity
from .protocol import TemperatureUnit

PARALLEL_UPDATES = 1

OPTION_BY_UNIT: Final = {
    TemperatureUnit.CELSIUS: "celsius",
    TemperatureUnit.FAHRENHEIT: "fahrenheit",
}
UNIT_BY_OPTION: Final = {option: unit for unit, option in OPTION_BY_UNIT.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select entity."""
    async_add_entities([AnovaTemperatureUnitSelect(entry.runtime_data)])


class AnovaTemperatureUnitSelect(AnovaEntity, SelectEntity):
    """Switch the cooker between Celsius and Fahrenheit."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data: AnovaData) -> None:
        """Initialize the select."""
        super().__init__(data, "temperature_unit")
        self._attr_options = list(UNIT_BY_OPTION)

    @property
    @override
    def current_option(self) -> str:
        """Return the unit the cooker is set to."""
        return OPTION_BY_UNIT[self.coordinator.data.unit]

    @override
    async def async_select_option(self, option: str) -> None:
        """Change the unit on the cooker."""
        await self.coordinator.async_execute(
            self._client.set_unit(UNIT_BY_OPTION[option])
        )
