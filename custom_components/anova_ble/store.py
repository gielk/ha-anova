"""Keep the last known cooker values across restarts.

The cooker is switched off most of the time. Storing the last poll lets the
entities show those values (frozen) right after Home Assistant starts,
instead of failing setup or showing nothing until the cooker is back.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .protocol import (
    AnovaDeviceInfo,
    AnovaState,
    DeviceStatus,
    TemperatureUnit,
    TimerState,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION: Final = 1
# Polls run every few seconds, so write at most once a minute.
SAVE_DELAY: Final = 60


class AnovaStore:
    """Load and save the last state and device info of one cooker."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store for a config entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self._latest: dict[str, Any] = {}
        self._save_scheduled = False

    async def async_load(self) -> tuple[AnovaState | None, AnovaDeviceInfo | None]:
        """Return the stored state and device info, None for what is missing."""
        data = await self._store.async_load() or {}
        return _state_from_dict(data.get("state")), _info_from_dict(
            data.get("device_info")
        )

    @callback
    def async_schedule_save(
        self, state: AnovaState | None, device_info: AnovaDeviceInfo | None
    ) -> None:
        """Save within ``SAVE_DELAY``, coalescing frequent polls into one write.

        A new delayed save would push the pending one back, so with polls
        every few seconds nothing would be written during a long cook. Only
        the first call schedules a write; later calls update what it writes.
        """
        self._latest = {
            "state": _state_to_dict(state),
            "device_info": _info_to_dict(device_info),
        }
        if self._save_scheduled:
            return
        self._save_scheduled = True
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Return the newest values, called by the store when it writes."""
        self._save_scheduled = False
        return self._latest

    async def async_remove(self) -> None:
        """Delete the stored data, used when the entry is removed."""
        await self._store.async_remove()


def _state_to_dict(state: AnovaState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "status": state.status.value if state.status else None,
        "raw_status": state.raw_status,
        "unit": state.unit.value,
        "current_temperature": state.current_temperature,
        "target_temperature": state.target_temperature,
        "timer_minutes": state.timer.minutes,
        "timer_running": state.timer.running,
    }


def _state_from_dict(data: Any) -> AnovaState | None:
    if not isinstance(data, dict):
        return None
    try:
        return AnovaState(
            status=DeviceStatus(data["status"]) if data["status"] else None,
            raw_status=str(data["raw_status"]),
            unit=TemperatureUnit(data["unit"]),
            current_temperature=float(data["current_temperature"]),
            target_temperature=float(data["target_temperature"]),
            timer=TimerState(
                minutes=int(data["timer_minutes"]),
                running=bool(data["timer_running"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as err:
        _LOGGER.debug("Ignoring stored state that does not load: %s", err)
        return None


def _info_to_dict(info: AnovaDeviceInfo | None) -> dict[str, Any] | None:
    if info is None:
        return None
    return {"id_card": info.id_card, "firmware_version": info.firmware_version}


def _info_from_dict(data: Any) -> AnovaDeviceInfo | None:
    if not isinstance(data, dict):
        return None
    return AnovaDeviceInfo(
        id_card=data.get("id_card"), firmware_version=data.get("firmware_version")
    )
