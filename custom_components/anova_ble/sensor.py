"""Sensors for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import AnovaConfigEntry, AnovaData
from .entity import HA_TEMPERATURE_UNITS, AnovaEntity
from .protocol import AnovaState, DeviceStatus

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class AnovaSensorEntityDescription(SensorEntityDescription):
    """Describe a sensor that is computed from one poll of the cooker."""

    value_fn: Callable[[AnovaState], StateType]
    # The native unit follows the unit the cooker is set to.
    uses_cooker_unit: bool = False


SENSORS: tuple[AnovaSensorEntityDescription, ...] = (
    AnovaSensorEntityDescription(
        key="current_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        uses_cooker_unit=True,
        value_fn=lambda state: state.current_temperature,
    ),
    AnovaSensorEntityDescription(
        key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        suggested_display_precision=1,
        uses_cooker_unit=True,
        value_fn=lambda state: state.target_temperature,
    ),
    AnovaSensorEntityDescription(
        key="timer_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda state: state.timer.minutes,
    ),
    AnovaSensorEntityDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[status.value for status in DeviceStatus],
        value_fn=lambda state: state.status.value if state.status else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    data = entry.runtime_data
    async_add_entities(
        [
            *(AnovaSensor(data, description) for description in SENSORS),
            AnovaRssiSensor(data),
        ]
    )


class AnovaSensor(AnovaEntity, SensorEntity):
    """A sensor with a value taken from the last poll."""

    entity_description: AnovaSensorEntityDescription

    def __init__(
        self, data: AnovaData, description: AnovaSensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data, description.key)
        self.entity_description = description

    @property
    @override
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit, which follows the cooker for temperatures."""
        if self.entity_description.uses_cooker_unit:
            return HA_TEMPERATURE_UNITS[self.coordinator.data.unit]
        return self.entity_description.native_unit_of_measurement

    @property
    @override
    def native_value(self) -> StateType:
        """Return the value of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)


class AnovaRssiSensor(AnovaEntity, SensorEntity):
    """Signal strength of the last advertisement, diagnostic."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, data: AnovaData) -> None:
        """Initialize the signal strength sensor."""
        super().__init__(data, "rssi")

    @property
    @override
    def native_value(self) -> int | None:
        """Return the RSSI, or None when no advertisement was seen yet."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self.coordinator.address, connectable=True
        )
        return service_info.rssi if service_info else None
