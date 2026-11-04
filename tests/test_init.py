"""Tests for setting up and unloading the integration."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

from bleak.backends.device import BLEDevice
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.anova_ble.const import (
    CONF_CONNECTION_MODE,
    DOMAIN,
    SERVICE_START_COOK,
    SERVICE_STOP_COOK,
)
from custom_components.anova_ble.protocol import (
    AnovaConnectionError,
    AnovaState,
    AnovaTimeoutError,
)
from custom_components.anova_ble.store import SAVE_DELAY

from .const import ADDRESS, TITLE


def store_key(entry: MockConfigEntry) -> str:
    """Return the storage key of the last values of an entry."""
    return f"{DOMAIN}.{entry.entry_id}"


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The entry loads, reads the cooker and releases it on unload."""
    assert init_integration.state is ConfigEntryState.LOADED
    mock_client.read_state.assert_awaited_once()
    mock_client.read_device_info.assert_awaited_once()
    mock_client.close.assert_not_awaited()

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    mock_client.close.assert_awaited_once()


async def test_device_registry(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """The device carries the model and firmware read from the cooker."""
    device = device_registry.async_get_device_by_connection(
        (CONNECTION_BLUETOOTH, ADDRESS), init_integration.entry_id
    )

    assert device is not None
    assert device.manufacturer == "Anova Culinary"
    assert device.model == "Precision Cooker (BT+WiFi, 900 W)"
    assert device.sw_version == "1.2.3"
    assert device.name == "Anova (2D60)"


async def test_device_info_failure_is_not_fatal(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """When the model cannot be read the integration still loads."""
    mock_client.read_device_info.side_effect = AnovaTimeoutError("no answer")
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    device = device_registry.async_get_device_by_connection(
        (CONNECTION_BLUETOOTH, ADDRESS), mock_config_entry.entry_id
    )
    assert device is not None
    assert device.model == "Precision Cooker"
    assert device.sw_version is None


@pytest.mark.parametrize(
    ("options", "disconnect_delay"),
    [
        ({}, 2.0),
        ({CONF_CONNECTION_MODE: "per_poll"}, 2.0),
        ({CONF_CONNECTION_MODE: "persistent"}, None),
    ],
)
async def test_connection_mode(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client_cls: MagicMock,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
    ble_device: MagicMock,
    options: dict[str, str],
    disconnect_delay: float | None,
) -> None:
    """Per poll closes the link after 2 s, persistent keeps it open."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options=options)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)

    mock_client_cls.assert_called_once_with(
        ble_device, disconnect_delay=disconnect_delay
    )
    mock_ble_device.assert_called_with(hass, ADDRESS, connectable=True)


async def test_device_not_found(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client_cls: MagicMock,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """A cooker that is switched off does not stop the entry from loading."""
    mock_ble_device.return_value = None
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    # The client gets a placeholder that the coordinator replaces later.
    (device,) = mock_client_cls.call_args.args
    assert isinstance(device, BLEDevice)
    assert device.address == ADDRESS
    assert device.name == TITLE
    mock_client.read_state.assert_not_awaited()
    assert not mock_config_entry.runtime_data.coordinator.online


async def test_first_refresh_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """A cooker that does not answer does not stop the entry from loading."""
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.read_state.assert_awaited_once()
    mock_client.read_device_info.assert_not_awaited()
    mock_client.close.assert_not_awaited()
    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.last_update_success
    assert not coordinator.online


async def test_first_poll_runs_in_the_background(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Setup does not wait for the first poll."""
    release = asyncio.Event()
    state = mock_client.read_state.return_value

    async def slow_read_state() -> AnovaState:
        await release.wait()
        return state

    mock_client.read_state.side_effect = slow_read_state
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    coordinator = mock_config_entry.runtime_data.coordinator
    assert not coordinator.online
    assert coordinator.data is None

    release.set()
    await hass.async_block_till_done()

    assert coordinator.online
    assert coordinator.data == state


async def test_missing_address(hass: HomeAssistant, mock_client_cls: MagicMock) -> None:
    """An entry without a unique id cannot be set up."""
    entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data={})
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.reason is not None
    assert "no Bluetooth address" in entry.reason
    mock_client_cls.assert_not_called()


async def test_remove_entry_deletes_the_store(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_ble_device: MagicMock,
) -> None:
    """Removing the cooker deletes the stored values."""
    key = store_key(init_integration)
    mock_ble_device.return_value = None  # No further polls push the save back.
    freezer.tick(SAVE_DELAY + 1)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert key in hass_storage

    assert await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_services_registered(hass: HomeAssistant) -> None:
    """The cook actions exist as soon as the integration is loaded."""
    assert await async_setup_component(hass, DOMAIN, {})

    assert hass.services.has_service(DOMAIN, SERVICE_START_COOK)
    assert hass.services.has_service(DOMAIN, SERVICE_STOP_COOK)


async def test_home_assistant_stop_releases_cooker(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The cooker is released when Home Assistant stops."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    mock_client.close.assert_awaited_once()


async def test_card_registration_failure_is_not_fatal(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken card registration only logs, the integration still loads."""
    with patch(
        "custom_components.anova_ble.async_register_card",
        side_effect=RuntimeError("boom"),
    ):
        assert await async_setup_component(hass, DOMAIN, {})
    assert "Could not register the Anova dashboard card" in caplog.text
    assert hass.services.has_service(DOMAIN, SERVICE_START_COOK)
