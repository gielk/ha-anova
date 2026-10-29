"""Diagnostics for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import AnovaConfigEntry

# The id card holds the serial number of the cooker.
TO_REDACT = {"id_card"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AnovaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    coordinator = data.coordinator
    service_info = bluetooth.async_last_service_info(
        hass, coordinator.address, connectable=True
    )
    update_interval = coordinator.update_interval
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "state": asdict(coordinator.data) if coordinator.data else None,
        "device_info": async_redact_data(asdict(data.device_info), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "failures": coordinator.failures,
        "update_interval": update_interval.total_seconds() if update_interval else None,
        "connection_mode": coordinator.connection_mode,
        "connected": data.client.is_connected,
        "bluetooth": (
            {
                "name": service_info.name,
                "rssi": service_info.rssi,
                "source": service_info.source,
                "connectable": service_info.connectable,
            }
            if service_info
            else None
        ),
    }
