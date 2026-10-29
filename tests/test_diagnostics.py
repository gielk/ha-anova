"""Tests for the diagnostics download."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from habluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.anova_ble.protocol import AnovaConnectionError

from .const import ADDRESS

BLE_MODULE = "homeassistant.components.bluetooth"


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    anova_service_info: BluetoothServiceInfoBleak,
) -> None:
    """The download holds the state, the connection details and no serial number."""
    mock_client.is_connected = True

    with patch(
        f"{BLE_MODULE}.async_last_service_info", return_value=anova_service_info
    ):
        diagnostics = await get_diagnostics_for_config_entry(
            hass, hass_client, init_integration
        )

    assert diagnostics == {
        "entry": {"data": {"address": ADDRESS}, "options": {}},
        "state": {
            "status": "running",
            "raw_status": "running",
            "unit": "c",
            "current_temperature": 54.2,
            "target_temperature": 56.5,
            "timer": {"minutes": 90, "running": True},
        },
        "device_info": {"id_card": "**REDACTED**", "firmware_version": "1.2.3"},
        "last_update_success": True,
        "failures": 0,
        "update_interval": 15.0,
        "connection_mode": "per_poll",
        "connected": True,
        "bluetooth": {
            "name": "Anova",
            "rssi": -60,
            "source": "local",
            "connectable": True,
        },
    }


async def test_diagnostics_while_failing(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """The failure count and the backoff interval show up in the download."""
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")
    freezer.tick(16)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    with patch(f"{BLE_MODULE}.async_last_service_info", return_value=None):
        diagnostics = await get_diagnostics_for_config_entry(
            hass, hass_client, init_integration
        )

    assert diagnostics["failures"] == 1
    assert diagnostics["update_interval"] == 30.0
    assert diagnostics["last_update_success"] is True
    assert diagnostics["connected"] is False
    assert diagnostics["bluetooth"] is None
