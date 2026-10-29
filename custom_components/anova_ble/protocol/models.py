"""Data models for the Anova BLE client."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .const import (
    TEMP_MAX_C,
    TEMP_MAX_F,
    TEMP_MIN_C,
    TEMP_MIN_F,
    WIFI_ID_PREFIX,
)


class TemperatureUnit(StrEnum):
    """Temperature unit used by the cooker."""

    CELSIUS = "c"
    FAHRENHEIT = "f"

    @property
    def min_temperature(self) -> float:
        """Lowest target temperature in this unit."""
        return TEMP_MIN_C if self is TemperatureUnit.CELSIUS else TEMP_MIN_F

    @property
    def max_temperature(self) -> float:
        """Highest target temperature in this unit."""
        return TEMP_MAX_C if self is TemperatureUnit.CELSIUS else TEMP_MAX_F


class DeviceStatus(StrEnum):
    """Status reported by the ``status`` command."""

    RUNNING = "running"
    STOPPED = "stopped"
    LOW_WATER = "low_water"
    HEATER_ERROR = "heater_error"
    POWER_LOSS = "power_loss"
    USER_CHANGE_PARAMETER = "user_change_parameter"


@dataclass(frozen=True, slots=True)
class TimerState:
    """Timer value in minutes and whether it is counting down."""

    minutes: int
    running: bool


@dataclass(frozen=True, slots=True)
class AnovaState:
    """One complete poll of the cooker."""

    status: DeviceStatus | None
    raw_status: str
    unit: TemperatureUnit
    current_temperature: float
    target_temperature: float
    timer: TimerState

    @property
    def is_cooking(self) -> bool:
        """Return True when the heater and pump are running."""
        return self.status is DeviceStatus.RUNNING


@dataclass(frozen=True, slots=True)
class AnovaDeviceInfo:
    """Static information about the cooker."""

    id_card: str | None
    firmware_version: str | None

    @property
    def has_wifi(self) -> bool:
        """Return True for the BT+WiFi (900 W) model."""
        return bool(self.id_card and self.id_card.lower().startswith(WIFI_ID_PREFIX))

    @property
    def model(self) -> str:
        """Human readable model name."""
        if self.has_wifi:
            return "Precision Cooker (BT+WiFi, 900 W)"
        if self.id_card is None:
            return "Precision Cooker"
        return "Precision Cooker (BT, 800 W)"
