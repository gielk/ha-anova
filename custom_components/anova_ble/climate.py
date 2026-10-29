"""Climate entity for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, override

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
from homeassistant.util.unit_conversion import TemperatureConverter

from .coordinator import AnovaConfigEntry, AnovaData
from .entity import HA_TEMPERATURE_UNITS, AnovaEntity

PARALLEL_UPDATES = 1

# The cooker reports "heating" until the water is this close to the target.
HEATING_MARGIN = 0.5

_MINUTE = timedelta(minutes=1)


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
    @override
    def temperature_unit(self) -> str:
        """Return the unit the cooker is set to."""
        return HA_TEMPERATURE_UNITS[self.coordinator.data.unit]

    @property
    @override
    def min_temp(self) -> float:
        """Return the lowest target temperature in the cooker unit."""
        return self.coordinator.data.unit.min_temperature

    @property
    @override
    def max_temp(self) -> float:
        """Return the highest target temperature in the cooker unit."""
        return self.coordinator.data.unit.max_temperature

    @property
    @override
    def current_temperature(self) -> float:
        """Return the water temperature."""
        return self.coordinator.data.current_temperature

    @property
    @override
    def target_temperature(self) -> float:
        """Return the target temperature."""
        return self.coordinator.data.target_temperature

    @property
    @override
    def hvac_mode(self) -> HVACMode:
        """Return heat while the cooker is cooking, else off."""
        return HVACMode.HEAT if self.coordinator.data.is_cooking else HVACMode.OFF

    @property
    @override
    def hvac_action(self) -> HVACAction:
        """Return what the heater is doing."""
        data = self.coordinator.data
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
            await self.coordinator.async_execute(
                self._client.set_target_temperature(
                    temperature, unit=self.coordinator.data.unit
                )
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
        await self.coordinator.async_execute(self._client.start())

    @override
    async def async_turn_off(self) -> None:
        """Stop heating. The cooker also stops the timer."""
        await self.coordinator.async_execute(self._client.stop())

    async def async_start_cook(
        self, temperature: float, duration: timedelta | None = None
    ) -> None:
        """Start a cook (entity service).

        ``temperature`` is in the Home Assistant unit system. It is converted
        to the cooker unit and rounded to 0.1. ``duration`` is rounded up to
        whole minutes.
        """
        unit = self.coordinator.data.unit
        cooker_temperature = round(
            TemperatureConverter.convert(
                temperature,
                self.hass.config.units.temperature_unit,
                HA_TEMPERATURE_UNITS[unit],
            ),
            1,
        )
        minutes = None if duration is None else -((-duration) // _MINUTE)
        await self.coordinator.async_execute(
            self._client.start_cook(cooker_temperature, minutes=minutes, unit=unit)
        )

    async def async_stop_cook(self) -> None:
        """Stop heating and the timer (entity service)."""
        await self.coordinator.async_execute(self._client.stop())
