"""Timer number for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AnovaConfigEntry, AnovaData
from .entity import AnovaEntity
from .protocol import (
    TIMER_MAX_MINUTES,
    TIMER_MIN_MINUTES,
    AnovaClient,
    AnovaState,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AnovaNumberEntityDescription(NumberEntityDescription):
    """Describe a number that reads from a poll and writes through the client."""

    value_fn: Callable[[AnovaState], float]
    set_fn: Callable[[AnovaClient, float], Awaitable[object]]


NUMBERS: tuple[AnovaNumberEntityDescription, ...] = (
    AnovaNumberEntityDescription(
        key="timer",
        device_class=NumberDeviceClass.DURATION,
        mode=NumberMode.BOX,
        native_min_value=TIMER_MIN_MINUTES,
        native_max_value=TIMER_MAX_MINUTES,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda state: state.timer.minutes,
        set_fn=lambda client, value: client.set_timer(int(value)),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entities."""
    data = entry.runtime_data
    async_add_entities(AnovaNumber(data, description) for description in NUMBERS)


class AnovaNumber(AnovaEntity, NumberEntity):
    """A number that is set on the cooker."""

    entity_description: AnovaNumberEntityDescription

    def __init__(
        self, data: AnovaData, description: AnovaNumberEntityDescription
    ) -> None:
        """Initialize the number."""
        super().__init__(data, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> float:
        """Return the value on the cooker."""
        return self.entity_description.value_fn(self.coordinator.data)

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Send the new value to the cooker."""
        await self.coordinator.async_execute(
            self.entity_description.set_fn(self._client, value)
        )
