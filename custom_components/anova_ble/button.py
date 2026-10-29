"""Buttons for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AnovaConfigEntry, AnovaData
from .entity import AnovaEntity
from .protocol import AnovaClient

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AnovaButtonEntityDescription(ButtonEntityDescription):
    """Describe a button that sends one command to the cooker."""

    press_fn: Callable[[AnovaClient], Awaitable[None]]


BUTTONS: tuple[AnovaButtonEntityDescription, ...] = (
    AnovaButtonEntityDescription(
        key="start_timer",
        press_fn=lambda client: client.start_timer(),
    ),
    AnovaButtonEntityDescription(
        key="stop_timer",
        press_fn=lambda client: client.stop_timer(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    data = entry.runtime_data
    async_add_entities(AnovaButton(data, description) for description in BUTTONS)


class AnovaButton(AnovaEntity, ButtonEntity):
    """A button that sends a command to the cooker."""

    entity_description: AnovaButtonEntityDescription

    def __init__(
        self, data: AnovaData, description: AnovaButtonEntityDescription
    ) -> None:
        """Initialize the button."""
        super().__init__(data, description.key)
        self.entity_description = description

    @override
    async def async_press(self) -> None:
        """Send the command."""
        await self.coordinator.async_execute(
            self.entity_description.press_fn(self._client)
        )
