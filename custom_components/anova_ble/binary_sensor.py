"""Binary sensor for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AnovaConfigEntry, AnovaData
from .entity import AnovaEntity
from .protocol import AnovaState

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class AnovaBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe a binary sensor that is computed from one poll of the cooker."""

    value_fn: Callable[[AnovaState], bool]


BINARY_SENSORS: tuple[AnovaBinarySensorEntityDescription, ...] = (
    AnovaBinarySensorEntityDescription(
        key="cooking",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda state: state.is_cooking,
    ),
    AnovaBinarySensorEntityDescription(
        key="timer_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda state: state.timer.running,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    data = entry.runtime_data
    async_add_entities(
        AnovaBinarySensor(data, description) for description in BINARY_SENSORS
    )


class AnovaBinarySensor(AnovaEntity, BinarySensorEntity):
    """A binary sensor with a value taken from the last poll."""

    entity_description: AnovaBinarySensorEntityDescription

    def __init__(
        self, data: AnovaData, description: AnovaBinarySensorEntityDescription
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(data, description.key)
        self.entity_description = description

    @property
    @override
    def is_on(self) -> bool:
        """Return the value for the last poll."""
        return self.entity_description.value_fn(self.coordinator.data)
