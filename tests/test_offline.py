"""Tests for a cooker that is switched off: placeholder, restore and storage."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.anova_ble.const import DEFAULT_POLL_INTERVAL, DOMAIN
from custom_components.anova_ble.protocol import AnovaConnectionError
from custom_components.anova_ble.store import SAVE_DELAY, STORAGE_VERSION

from .const import ADDRESS

CLIMATE_ID = "climate.anova_2d60"

STORED_STATE = {
    "status": "running",
    "raw_status": "running",
    "unit": "c",
    "current_temperature": 61.5,
    "target_temperature": 63.0,
    "timer_minutes": 45,
    "timer_running": True,
}
STORED_DEVICE_INFO = {"id_card": "anova f56-123456", "firmware_version": "1.2.3"}


def seed_storage(
    hass_storage: dict[str, Any],
    entry: MockConfigEntry,
    state: Any = STORED_STATE,
    device_info: Any = STORED_DEVICE_INFO,
) -> str:
    """Put the last values of a previous run in the storage."""
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "key": key,
        "data": {"state": state, "device_info": device_info},
    }
    return key


def entity_id(entity_registry: er.EntityRegistry, domain: str, key: str) -> str:
    """Return the entity id of an entity of the cooker by its key."""
    found = entity_registry.async_get_entity_id(domain, DOMAIN, f"{ADDRESS}_{key}")
    assert found is not None, f"{domain} {key} is not registered"
    return found


async def setup_out_of_range(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_ble_device: MagicMock,
) -> None:
    """Set up the entry while Bluetooth cannot see the cooker."""
    mock_ble_device.return_value = None
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_setup_with_cooker_missing(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """A cooker that was never seen shows offline and no values."""
    await setup_out_of_range(hass, mock_config_entry, mock_ble_device)

    mock_client.read_state.assert_not_awaited()
    assert hass.states.get(entity_id(entity_registry, "sensor", "status")).state == (
        "offline"
    )
    assert (
        hass.states.get(entity_id(entity_registry, "binary_sensor", "online")).state
        == STATE_OFF
    )
    for domain, key in (
        ("sensor", "current_temperature"),
        ("sensor", "target_temperature"),
        ("sensor", "timer_remaining"),
        ("binary_sensor", "cooking"),
        ("binary_sensor", "timer_running"),
    ):
        state = hass.states.get(entity_id(entity_registry, domain, key))
        assert state.state == STATE_UNKNOWN, key
    # No data yet: the temperature unit defaults to Celsius.
    assert (
        hass.states.get(
            entity_id(entity_registry, "sensor", "current_temperature")
        ).attributes[ATTR_UNIT_OF_MEASUREMENT]
        == "°C"
    )
    # Controls cannot be used, the thermostat stays available.
    for domain, key in (
        ("button", "start_timer"),
        ("button", "stop_timer"),
        ("number", "timer"),
        ("select", "temperature_unit"),
    ):
        state = hass.states.get(entity_id(entity_registry, domain, key))
        assert state.state == STATE_UNAVAILABLE, key
    climate = hass.states.get(CLIMATE_ID)
    assert climate.state != STATE_UNAVAILABLE
    assert climate.attributes[ATTR_CURRENT_TEMPERATURE] is None
    assert climate.attributes[ATTR_TEMPERATURE] is None
    assert climate.attributes.get(ATTR_HVAC_ACTION) is None
    assert climate.attributes[ATTR_MIN_TEMP] == 5.0
    assert climate.attributes[ATTR_MAX_TEMP] == 99.9
    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.last_update_success
    assert not coordinator.online


async def test_restore_from_storage(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """The last values are shown frozen when the cooker cannot be reached."""
    seed_storage(hass_storage, mock_config_entry)

    await setup_out_of_range(hass, mock_config_entry, mock_ble_device)

    mock_client.read_state.assert_not_awaited()
    mock_client.read_device_info.assert_not_awaited()
    climate = hass.states.get(CLIMATE_ID)
    assert climate.state == "heat"
    assert climate.attributes[ATTR_CURRENT_TEMPERATURE] == 61.5
    assert climate.attributes[ATTR_TEMPERATURE] == 63.0
    assert climate.attributes.get(ATTR_HVAC_ACTION) is None

    def value(domain: str, key: str) -> str:
        return hass.states.get(entity_id(entity_registry, domain, key)).state

    assert value("sensor", "current_temperature") == "61.5"
    assert value("sensor", "target_temperature") == "63.0"
    assert value("sensor", "timer_remaining") == "45"
    assert value("sensor", "status") == "offline"
    assert value("binary_sensor", "cooking") == STATE_ON
    assert value("binary_sensor", "timer_running") == STATE_ON
    assert value("binary_sensor", "online") == STATE_OFF
    # Frozen, but not controllable.
    assert value("number", "timer") == STATE_UNAVAILABLE
    assert value("select", "temperature_unit") == STATE_UNAVAILABLE
    device = device_registry.async_get_device_by_connection(
        (CONNECTION_BLUETOOTH, ADDRESS), mock_config_entry.entry_id
    )
    assert device.model == "Precision Cooker (BT+WiFi, 900 W)"
    assert device.sw_version == "1.2.3"


async def test_restored_values_stay_when_polls_fail(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """A cooker in range that does not answer keeps the restored values too."""
    seed_storage(hass_storage, mock_config_entry)
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data.coordinator
    assert not coordinator.online
    assert coordinator.failures == 1
    assert (
        hass.states.get(
            entity_id(entity_registry, "sensor", "current_temperature")
        ).state
        == "61.5"
    )


async def test_restored_values_replaced_by_first_poll(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """The first good poll replaces the stored values and goes online."""
    seed_storage(hass_storage, mock_config_entry)
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.online
    assert coordinator.data == mock_client.read_state.return_value
    # The device info came from the store, so it is not read again.
    mock_client.read_device_info.assert_not_awaited()
    assert hass.states.get(CLIMATE_ID).attributes[ATTR_CURRENT_TEMPERATURE] == 54.2


@pytest.mark.parametrize(
    "state",
    [
        "junk",
        [],
        {},
        {**STORED_STATE, "status": "bogus"},
        {**STORED_STATE, "unit": "k"},
        {**STORED_STATE, "current_temperature": "hot"},
        {**STORED_STATE, "timer_minutes": None},
        {k: v for k, v in STORED_STATE.items() if k != "raw_status"},
    ],
)
async def test_corrupt_storage_is_ignored(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
    state: Any,
) -> None:
    """Stored data that does not load leaves the entities without values."""
    seed_storage(hass_storage, mock_config_entry, state=state, device_info="junk")

    await setup_out_of_range(hass, mock_config_entry, mock_ble_device)

    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.data is None
    assert coordinator.device_info is None
    assert (
        hass.states.get(
            entity_id(entity_registry, "sensor", "current_temperature")
        ).state
        == STATE_UNKNOWN
    )
    assert hass.states.get(CLIMATE_ID).attributes[ATTR_CURRENT_TEMPERATURE] is None


async def test_poll_saves_to_storage(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """While polls keep succeeding, the newest values are written within a minute."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    key = f"{DOMAIN}.{mock_config_entry.entry_id}"
    assert key not in hass_storage

    async def polls(count: int) -> None:
        for _ in range(count):
            freezer.tick(DEFAULT_POLL_INTERVAL + 1)
            async_fire_time_changed(hass)
            await hass.async_block_till_done()

    # A value polled half way is in the write a minute after the first poll,
    # even though polls keep asking for a save every 15 s.
    first = mock_client.read_state.return_value
    await polls(2)
    mock_client.read_state.return_value = replace(first, current_temperature=55.0)
    await polls(3)  # 80 s after the first poll

    assert hass_storage[key]["version"] == STORAGE_VERSION
    assert hass_storage[key]["data"] == {
        "state": {
            "status": "running",
            "raw_status": "running",
            "unit": "c",
            "current_temperature": 55.0,
            "target_temperature": 56.5,
            "timer_minutes": 90,
            "timer_running": True,
        },
        "device_info": {
            "id_card": "anova f56-123456",
            "firmware_version": "1.2.3",
        },
    }

    # The next minute is written again.
    mock_client.read_state.return_value = replace(first, current_temperature=56.0)
    await polls(5)
    assert hass_storage[key]["data"]["state"]["current_temperature"] == 56.0


async def test_failed_polls_keep_the_stored_values(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Nothing is written while the cooker cannot be reached."""
    key = seed_storage(hass_storage, mock_config_entry)

    await setup_out_of_range(hass, mock_config_entry, mock_ble_device)
    freezer.tick(SAVE_DELAY + 1)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass_storage[key]["data"]["state"] == STORED_STATE
