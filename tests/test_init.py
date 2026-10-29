"""Tests for setting up and unloading the integration."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anova_ble.const import (
    CONF_CONNECTION_MODE,
    DOMAIN,
    SERVICE_START_COOK,
    SERVICE_STOP_COOK,
)
from custom_components.anova_ble.protocol import (
    AnovaConnectionError,
    AnovaTimeoutError,
)

from .const import ADDRESS


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The entry loads, reads the cooker and releases it on unload."""
    assert init_integration.state is ConfigEntryState.LOADED
    mock_client.read_state.assert_awaited_once()
    mock_client.read_device_info.assert_awaited_once()
    mock_client.disconnect.assert_not_awaited()

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    mock_client.disconnect.assert_awaited_once()


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
    mock_ble_device: MagicMock,
) -> None:
    """Setup is retried while the cooker is out of range."""
    mock_ble_device.return_value = None
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client_cls.assert_not_called()


async def test_first_refresh_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Setup is retried when the cooker does not answer, and the link is closed."""
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.disconnect.assert_awaited_once()
    mock_client.read_device_info.assert_not_awaited()


async def test_first_refresh_out_of_range_between_lookups(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
    ble_device: MagicMock,
) -> None:
    """A cooker that vanishes before the first poll also means retry."""
    mock_ble_device.side_effect = [ble_device, None]
    mock_config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.read_state.assert_not_awaited()


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

    mock_client.disconnect.assert_awaited_once()
