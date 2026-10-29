"""Protocol client for the original Anova Precision Cooker (BT and BT+WiFi).

No Home Assistant imports live in this package, so it can be published as a
standalone library later.
"""

from __future__ import annotations

from .client import AnovaClient
from .const import (
    CHARACTERISTIC_UUID,
    LOCAL_NAME,
    SERVICE_UUID,
    TEMP_MAX_C,
    TEMP_MAX_F,
    TEMP_MIN_C,
    TEMP_MIN_F,
    TIMER_MAX_MINUTES,
    TIMER_MIN_MINUTES,
)
from .exceptions import (
    AnovaCommandError,
    AnovaConnectionError,
    AnovaError,
    AnovaResponseError,
    AnovaTimeoutError,
    AnovaValueError,
)
from .models import (
    AnovaDeviceInfo,
    AnovaState,
    DeviceStatus,
    TemperatureUnit,
    TimerState,
)

__all__ = [
    "CHARACTERISTIC_UUID",
    "LOCAL_NAME",
    "SERVICE_UUID",
    "TEMP_MAX_C",
    "TEMP_MAX_F",
    "TEMP_MIN_C",
    "TEMP_MIN_F",
    "TIMER_MAX_MINUTES",
    "TIMER_MIN_MINUTES",
    "AnovaClient",
    "AnovaCommandError",
    "AnovaConnectionError",
    "AnovaDeviceInfo",
    "AnovaError",
    "AnovaResponseError",
    "AnovaState",
    "AnovaTimeoutError",
    "AnovaValueError",
    "DeviceStatus",
    "TemperatureUnit",
    "TimerState",
]
