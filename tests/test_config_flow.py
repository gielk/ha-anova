"""Tests for the Anova config flow and options flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from habluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import probatio
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anova_ble.const import (
    CONF_CONNECTION_MODE,
    CONF_POLL_INTERVAL,
    CONNECTION_MODE_PERSISTENT,
    DOMAIN,
)
from custom_components.anova_ble.protocol import AnovaConnectionError

from .const import ADDRESS, TITLE

DISCOVERED = (
    "custom_components.anova_ble.config_flow.bluetooth.async_discovered_service_info"
)


async def test_bluetooth_discovery(
    hass: HomeAssistant,
    anova_service_info: BluetoothServiceInfoBleak,
    mock_client: MagicMock,
    mock_setup_entry: MagicMock,
) -> None:
    """A discovered cooker is confirmed, tested and added."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=anova_service_info
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": TITLE}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == ADDRESS
    mock_client.read_state.assert_awaited_once()
    mock_client.disconnect.assert_awaited_once()
    assert len(mock_setup_entry.mock_calls) == 1


async def test_bluetooth_discovery_not_anova(
    hass: HomeAssistant, scale_service_info: BluetoothServiceInfoBleak
) -> None:
    """Another device with service ffe0, such as a scale, is not accepted."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=scale_service_info
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_already_configured(
    hass: HomeAssistant,
    anova_service_info: BluetoothServiceInfoBleak,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A cooker that is set up already is not offered again."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=anova_service_info
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_discovery_cannot_connect_then_recover(
    hass: HomeAssistant,
    anova_service_info: BluetoothServiceInfoBleak,
    mock_client: MagicMock,
    mock_setup_entry: MagicMock,
) -> None:
    """A failed connection shows an error and the user can try again."""
    sample = mock_client.read_state.return_value
    mock_client.read_state.side_effect = [AnovaConnectionError("busy"), sample]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=anova_service_info
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["errors"] == {"base": "cannot_connect"}
    mock_client.disconnect.assert_awaited_once()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert mock_client.disconnect.await_count == 2


async def test_bluetooth_discovery_unexpected_error(
    hass: HomeAssistant,
    anova_service_info: BluetoothServiceInfoBleak,
    mock_client: MagicMock,
) -> None:
    """An unexpected error is shown as unknown and the link is closed."""
    mock_client.read_state.side_effect = RuntimeError("bug")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=anova_service_info
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    mock_client.disconnect.assert_awaited_once()


async def test_user_step_picks_device(
    hass: HomeAssistant,
    anova_service_info: BluetoothServiceInfoBleak,
    scale_service_info: BluetoothServiceInfoBleak,
    mock_client: MagicMock,
    mock_setup_entry: MagicMock,
) -> None:
    """The user can pick a cooker. Devices that are not Anova are left out."""
    with patch(DISCOVERED, return_value=[scale_service_info, anova_service_info]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    schema = result["data_schema"]
    assert schema({CONF_ADDRESS: ADDRESS}) == {CONF_ADDRESS: ADDRESS}
    with pytest.raises(probatio.Invalid):
        schema({CONF_ADDRESS: scale_service_info.address})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_ADDRESS: ADDRESS}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == ADDRESS
    mock_client.read_state.assert_awaited_once()
    mock_client.disconnect.assert_awaited_once()


async def test_user_step_no_devices(
    hass: HomeAssistant, scale_service_info: BluetoothServiceInfoBleak
) -> None:
    """The flow stops when no cooker is in range."""
    with patch(DISCOVERED, return_value=[scale_service_info]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_step_skips_configured_devices(
    hass: HomeAssistant,
    anova_service_info: BluetoothServiceInfoBleak,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A cooker that is set up already is not listed."""
    mock_config_entry.add_to_hass(hass)

    with patch(DISCOVERED, return_value=[anova_service_info]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_step_cannot_connect_then_recover(
    hass: HomeAssistant,
    anova_service_info: BluetoothServiceInfoBleak,
    mock_client: MagicMock,
    mock_setup_entry: MagicMock,
) -> None:
    """After a failed connection the same list is shown with an error."""
    sample = mock_client.read_state.return_value
    mock_client.read_state.side_effect = [AnovaConnectionError("busy"), sample]
    with patch(DISCOVERED, return_value=[anova_service_info]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: ADDRESS}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert mock_client.disconnect.await_count == 2


async def test_options_flow(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client_cls: MagicMock,
) -> None:
    """The options are saved as whole numbers and reload the entry."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    # The form starts with the defaults.
    assert result["data_schema"]({}) == {
        CONF_POLL_INTERVAL: 15,
        CONF_CONNECTION_MODE: "per_poll",
    }
    mock_client_cls.reset_mock()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_POLL_INTERVAL: 30.0,
            CONF_CONNECTION_MODE: CONNECTION_MODE_PERSISTENT,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_POLL_INTERVAL: 30,
        CONF_CONNECTION_MODE: CONNECTION_MODE_PERSISTENT,
    }
    assert isinstance(init_integration.options[CONF_POLL_INTERVAL], int)
    # The entry was reloaded with the new connection mode.
    assert mock_client_cls.call_args.kwargs["disconnect_delay"] is None
    coordinator = init_integration.runtime_data.coordinator
    assert coordinator.update_interval.total_seconds() == 30


@pytest.mark.parametrize("poll_interval", [4, 301])
async def test_options_flow_poll_interval_range(
    hass: HomeAssistant, init_integration: MockConfigEntry, poll_interval: int
) -> None:
    """The poll interval has to be between 5 and 300 seconds."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_POLL_INTERVAL: poll_interval,
                CONF_CONNECTION_MODE: "per_poll",
            },
        )
