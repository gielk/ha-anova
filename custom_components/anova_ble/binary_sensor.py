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
        [
            *(AnovaBinarySensor(data, description) for description in BINARY_SENSORS),
            AnovaOnlineBinarySensor(data),
        ]
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
    def is_on(self) -> bool | None:
        """Return the last known value, frozen while the cooker is offline."""
        if (data := self.state_data) is None:
            return None
        return self.entity_description.value_fn(data)


class AnovaOnlineBinarySensor(AnovaEntity, BinarySensorEntity):
    """On while the cooker answers, off while it is switched off or away."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, data: AnovaData) -> None:
        """Initialize the connection sensor."""
        super().__init__(data, "online")

    @property
    @override
    def is_on(self) -> bool:
        """Return True while the cooker answers."""
        return self.coordinator.online
