"""Climate entity for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final, override

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AnovaConfigEntry, AnovaData
from .entity import HA_TEMPERATURE_UNITS, AnovaEntity

PARALLEL_UPDATES = 1

# The cooker reports "heating" until the water is this close to the target.
HEATING_MARGIN = 0.5

_MINUTE = timedelta(minutes=1)
UNIT_BY_HA: Final = {ha_unit: unit for unit, ha_unit in HA_TEMPERATURE_UNITS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the climate entity."""
    async_add_entities([AnovaClimate(entry.runtime_data)])


class AnovaClimate(AnovaEntity, ClimateEntity):
    """The cooker as a thermostat that can heat or be off."""

    _attr_name = None
    _attr_precision = PRECISION_TENTHS
    _attr_target_temperature_step = 0.1
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, data: AnovaData) -> None:
        """Initialize the climate entity."""
        super().__init__(data, "cooker")
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]

    @property
    def native_temperature_unit(self) -> str:
        """Return the unit the cooker is set to."""
        return HA_TEMPERATURE_UNITS[self.cooker_unit]

    @property
    @override
    def min_temp(self) -> float:
        """Return the lowest target temperature in the cooker unit."""
        return self.cooker_unit.min_temperature

    @property
    @override
    def max_temp(self) -> float:
        """Return the highest target temperature in the cooker unit."""
        return self.cooker_unit.max_temperature

    @property
    def native_current_temperature(self) -> float | None:
        """Return the water temperature, frozen while offline."""
        data = self.state_data
        return data.current_temperature if data else None

    @property
    def native_target_temperature(self) -> float | None:
        """Return the target temperature, frozen while offline."""
        data = self.state_data
        return data.target_temperature if data else None

    # Home Assistant 2026.11 renamed these to native_*. Older releases still
    # read the old names; when a class provides both, 2026.11 uses native_*.
    temperature_unit = native_temperature_unit
    current_temperature = native_current_temperature
    target_temperature = native_target_temperature

    @property
    @override
    def hvac_mode(self) -> HVACMode:
        """Return heat while the cooker is cooking, else off."""
        data = self.state_data
        return HVACMode.HEAT if data and data.is_cooking else HVACMode.OFF

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        """Return what the heater is doing, None while the cooker is offline."""
        data = self.state_data
        if data is None or not self.coordinator.online:
            return None
        if not data.is_cooking:
            return HVACAction.OFF
        if data.current_temperature < data.target_temperature - HEATING_MARGIN:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature, already in the cooker unit.

        The service can carry an HVAC mode as well, which is applied after the
        temperature so the cooker never heats towards the old target.
        """
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            unit = self.cooker_unit
            await self.coordinator.async_execute(
                lambda: self._client.set_target_temperature(temperature, unit=unit)
            )
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Start heating or stop the cooker."""
        if hvac_mode == HVACMode.HEAT:
            await self.async_turn_on()
        else:
            await self.async_turn_off()

    @override
    async def async_turn_on(self) -> None:
        """Start heating."""
        await self.coordinator.async_execute(self._client.start)

    @override
    async def async_turn_off(self) -> None:
        """Stop heating. The cooker also stops the timer."""
        await self.coordinator.async_execute(self._client.stop)

    async def async_start_cook(
        self, temperature: float, duration: timedelta | None = None
    ) -> None:
        """Start a cook (entity service).

        ``temperature`` is in the Home Assistant unit system; the client reads
        the cooker unit and converts. ``duration`` is rounded up to whole
        minutes.
        """
        unit = UNIT_BY_HA[self.hass.config.units.temperature_unit]
        minutes = None if duration is None else -((-duration) // _MINUTE)
        await self.coordinator.async_execute(
            lambda: self._client.start_cook(temperature, minutes=minutes, unit=unit)
        )

    async def async_stop_cook(self) -> None:
        """Stop heating and the timer (entity service)."""
        await self.coordinator.async_execute(self._client.stop)
