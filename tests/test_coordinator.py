"""Tests for the update coordinator: dropouts, backoff and recovery."""

from __future__ import annotations

from datetime import timedelta
import logging
from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.climate import ATTR_CURRENT_TEMPERATURE, ATTR_HVAC_ACTION
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.anova_ble.const import (
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    FAILURES_BEFORE_OFFLINE,
    MAX_BACKOFF_INTERVAL,
)
from custom_components.anova_ble.coordinator import AnovaCoordinator
from custom_components.anova_ble.protocol import (
    AnovaConnectionError,
    AnovaDeviceInfo,
    AnovaError,
    AnovaState,
    AnovaTimeoutError,
)
from custom_components.anova_ble.store import AnovaStore

from .const import ADDRESS

CLIMATE_ID = "climate.anova_2d60"
STATUS_ID = "sensor.anova_2d60_status"
ONLINE_ID = "binary_sensor.anova_2d60_connection"


async def poll(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    coordinator: AnovaCoordinator,
) -> None:
    """Let the scheduled refresh of the coordinator run once."""
    freezer.tick(coordinator.update_interval + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def loud_records(
    caplog: pytest.LogCaptureFixture, level: int
) -> list[logging.LogRecord]:
    """Return the records of the integration and the coordinator at ``level``+."""
    return [
        record
        for record in caplog.records
        if record.levelno >= level
        and record.name.startswith(
            ("custom_components.anova_ble", "homeassistant.helpers.update_coordinator")
        )
    ]


def get_coordinator(entry: MockConfigEntry) -> AnovaCoordinator:
    """Return the coordinator of a loaded entry."""
    return entry.runtime_data.coordinator


async def test_successful_poll(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    ble_device: MagicMock,
) -> None:
    """Every poll hands the current BLEDevice to the client first."""
    coordinator = get_coordinator(init_integration)
    assert coordinator.update_interval == timedelta(seconds=15)
    assert coordinator.data == mock_client.read_state.return_value

    await poll(hass, freezer, coordinator)

    assert mock_client.read_state.await_count == 2
    mock_client.set_ble_device.assert_called_with(ble_device)
    assert coordinator.failures == 0
    assert coordinator.last_update_success
    assert coordinator.online


async def test_poll_interval_option(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """The poll interval comes from the options."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_POLL_INTERVAL: 45}
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert get_coordinator(mock_config_entry).update_interval == timedelta(seconds=45)


async def test_transient_failures_keep_data(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """The first failures keep the last data and the entities stay available."""
    coordinator = get_coordinator(init_integration)
    last_data = coordinator.data
    mock_client.read_state.side_effect = AnovaTimeoutError("no answer")

    for failures in range(1, FAILURES_BEFORE_OFFLINE):
        await poll(hass, freezer, coordinator)

        assert coordinator.failures == failures
        assert coordinator.last_update_success
        assert coordinator.data is last_data
        assert hass.states.get(CLIMATE_ID).state == "heat"
        assert hass.states.get(STATUS_ID).state == "running"


async def test_offline_after_three_failures(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Three failed polls in a row make the cooker offline, never unavailable."""
    coordinator = get_coordinator(init_integration)
    last_data = coordinator.data
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")

    for failures in range(1, FAILURES_BEFORE_OFFLINE):
        await poll(hass, freezer, coordinator)
        assert coordinator.failures == failures
        assert coordinator.online
    assert hass.states.get(STATUS_ID).state == "running"

    await poll(hass, freezer, coordinator)

    assert coordinator.failures == FAILURES_BEFORE_OFFLINE
    assert not coordinator.online
    assert coordinator.last_update_success
    assert coordinator.last_exception is None
    assert coordinator.data is last_data
    assert hass.states.get(STATUS_ID).state == "offline"
    # The other values are frozen and every entity of the cooker is available,
    # except the controls.
    assert hass.states.get(CLIMATE_ID).state == "heat"
    assert hass.states.get(CLIMATE_ID).attributes[ATTR_CURRENT_TEMPERATURE] == 54.2
    assert hass.states.get(CLIMATE_ID).attributes.get(ATTR_HVAC_ACTION) is None
    assert hass.states.get(ONLINE_ID).state == STATE_OFF
    entries = er.async_entries_for_config_entry(
        entity_registry, init_integration.entry_id
    )
    assert entries
    for entry in entries:
        if entry.disabled:
            continue
        expected = entry.domain in ("button", "number", "select")
        assert (hass.states.get(entry.entity_id).state == STATE_UNAVAILABLE) is expected


async def test_backoff_grows_and_is_capped(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """While polls fail the interval doubles, up to the cap."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")

    intervals = []
    for _ in range(7):
        await poll(hass, freezer, coordinator)
        intervals.append(coordinator.update_interval.total_seconds())

    assert intervals == [30, 60, 120, 240, 300, 300, 300]
    assert max(intervals) == MAX_BACKOFF_INTERVAL


async def test_backoff_survives_days_offline(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Thousands of failed polls do not overflow the backoff."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")
    coordinator._failures = 5000  # A cooker unplugged for weeks.

    await poll(hass, freezer, coordinator)

    assert coordinator.last_update_success
    assert not coordinator.online
    assert coordinator.update_interval.total_seconds() == MAX_BACKOFF_INTERVAL


async def test_recovery_resets_backoff(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    sample_state: AnovaState,
) -> None:
    """One good poll resets the counter and the interval and is online again."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")
    for _ in range(FAILURES_BEFORE_OFFLINE + 1):
        await poll(hass, freezer, coordinator)
    assert not coordinator.online
    assert hass.states.get(STATUS_ID).state == "offline"
    assert coordinator.update_interval > timedelta(seconds=15)

    mock_client.read_state.side_effect = None
    await poll(hass, freezer, coordinator)

    assert coordinator.failures == 0
    assert coordinator.update_interval == timedelta(seconds=15)
    assert coordinator.online
    assert hass.states.get(STATUS_ID).state == "running"
    assert hass.states.get(ONLINE_ID).state == STATE_ON


async def test_failure_count_resets_after_dropout(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    sample_state: AnovaState,
) -> None:
    """Failures have to be in a row: a good poll in between starts over."""
    coordinator = get_coordinator(init_integration)
    for _ in range(FAILURES_BEFORE_OFFLINE - 1):
        mock_client.read_state.side_effect = AnovaTimeoutError("no answer")
        await poll(hass, freezer, coordinator)
    mock_client.read_state.side_effect = None
    await poll(hass, freezer, coordinator)
    assert coordinator.failures == 0

    mock_client.read_state.side_effect = AnovaTimeoutError("no answer")
    await poll(hass, freezer, coordinator)

    assert coordinator.failures == 1
    assert coordinator.online
    assert hass.states.get(CLIMATE_ID).state == "heat"


async def test_out_of_range_is_offline_at_once(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
    ble_device: MagicMock,
) -> None:
    """A cooker that Bluetooth cannot see is offline without waiting for retries."""
    coordinator = get_coordinator(init_integration)
    last_data = coordinator.data
    mock_ble_device.return_value = None
    mock_client.read_state.reset_mock()

    await poll(hass, freezer, coordinator)

    assert coordinator.failures == 1
    assert not coordinator.online
    assert coordinator.last_update_success
    assert coordinator.data is last_data
    assert hass.states.get(STATUS_ID).state == "offline"
    assert hass.states.get(CLIMATE_ID).state == "heat"
    mock_client.read_state.assert_not_awaited()

    mock_ble_device.return_value = ble_device
    await poll(hass, freezer, coordinator)

    assert coordinator.failures == 0
    assert coordinator.online
    assert hass.states.get(STATUS_ID).state == "running"
    mock_client.read_state.assert_awaited_once()


async def test_failures_are_logged_once(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dropouts are debug only. Offline and back online are logged once at info."""
    coordinator = get_coordinator(init_integration)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="custom_components.anova_ble")
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")

    for _ in range(FAILURES_BEFORE_OFFLINE - 1):
        await poll(hass, freezer, coordinator)
    assert loud_records(caplog, logging.INFO) == []
    assert any(
        r.levelno == logging.DEBUG
        and r.name == "custom_components.anova_ble.coordinator"
        and "link dropped" in r.getMessage()
        for r in caplog.records
    )

    for _ in range(3):
        await poll(hass, freezer, coordinator)
    records = loud_records(caplog, logging.INFO)
    assert [r.levelno for r in records] == [logging.INFO]
    assert "is offline, keeping the last known values" in records[0].getMessage()

    caplog.clear()
    mock_client.read_state.side_effect = None
    await poll(hass, freezer, coordinator)
    await poll(hass, freezer, coordinator)
    records = loud_records(caplog, logging.INFO)
    assert [r.levelno for r in records] == [logging.INFO]
    assert "is back online" in records[0].getMessage()
    assert loud_records(caplog, logging.WARNING) == []


async def test_out_of_range_is_logged_once(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_ble_device: MagicMock,
    ble_device: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cooker that stays out of range is logged once, never as a warning."""
    coordinator = get_coordinator(init_integration)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="custom_components.anova_ble")
    mock_ble_device.return_value = None

    for _ in range(5):
        await poll(hass, freezer, coordinator)

    records = loud_records(caplog, logging.INFO)
    assert len(records) == 1
    assert "is offline" in records[0].getMessage()

    caplog.clear()
    mock_ble_device.return_value = ble_device
    await poll(hass, freezer, coordinator)

    records = loud_records(caplog, logging.INFO)
    assert len(records) == 1
    assert "is back online" in records[0].getMessage()
    assert loud_records(caplog, logging.WARNING) == []


async def test_first_good_poll_is_not_logged(
    init_integration: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    """Reaching the cooker for the first time is not a change worth a log line."""
    assert get_coordinator(init_integration).online
    assert loud_records(caplog, logging.INFO) == []


async def test_unexpected_error_is_not_swallowed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Only cooker errors are treated as dropouts, a bug still shows."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaError("odd")

    await poll(hass, freezer, coordinator)
    assert coordinator.failures == 1

    mock_client.read_state.side_effect = RuntimeError("bug")
    await poll(hass, freezer, coordinator)

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, RuntimeError)


async def test_poll_at_once_when_cooker_reappears(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
    ble_device: MagicMock,
) -> None:
    """The first advertisement after a dropout polls without waiting for backoff."""
    coordinator = get_coordinator(init_integration)
    mock_ble_device.return_value = None
    for _ in range(FAILURES_BEFORE_OFFLINE):
        await poll(hass, freezer, coordinator)
    assert not coordinator.online
    assert coordinator.update_interval.total_seconds() > DEFAULT_POLL_INTERVAL

    mock_ble_device.return_value = ble_device
    mock_client.read_state.reset_mock()
    coordinator.async_handle_advertisement(MagicMock(), BluetoothChange.ADVERTISEMENT)
    await hass.async_block_till_done()

    mock_client.read_state.assert_awaited_once()
    assert coordinator.online
    assert hass.states.get(STATUS_ID).state == "running"
    assert coordinator.update_interval == timedelta(seconds=DEFAULT_POLL_INTERVAL)

    # Later advertisements of a reachable cooker do not poll again.
    coordinator.async_handle_advertisement(MagicMock(), BluetoothChange.ADVERTISEMENT)
    await hass.async_block_till_done()
    mock_client.read_state.assert_awaited_once()


async def test_advertisement_ignored_while_reachable(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Normal advertisements of a reachable cooker never trigger a poll."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.reset_mock()
    coordinator.async_handle_advertisement(MagicMock(), BluetoothChange.ADVERTISEMENT)
    await hass.async_block_till_done()
    mock_client.read_state.assert_not_awaited()


async def test_never_raises_update_failed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Cooker errors never mark the update as failed."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaTimeoutError("no answer")

    for _ in range(FAILURES_BEFORE_OFFLINE + 2):
        await poll(hass, freezer, coordinator)
        assert coordinator.last_update_success
        assert coordinator.last_exception is None


async def test_device_info_is_read_once(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    device_registry: dr.DeviceRegistry,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    sample_device_info: AnovaDeviceInfo,
) -> None:
    """The model is learned after the first good poll and not read again."""
    coordinator = get_coordinator(init_integration)
    assert coordinator.device_info == sample_device_info
    mock_client.read_device_info.assert_awaited_once()

    await poll(hass, freezer, coordinator)
    await poll(hass, freezer, coordinator)

    mock_client.read_device_info.assert_awaited_once()
    device = device_registry.async_get_device_by_connection(
        (CONNECTION_BLUETOOTH, ADDRESS), init_integration.entry_id
    )
    assert device.model == "Precision Cooker (BT+WiFi, 900 W)"
    assert device.sw_version == "1.2.3"


async def test_device_info_failure_is_retried(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
    sample_device_info: AnovaDeviceInfo,
) -> None:
    """A failed read leaves the device info empty until the next good poll."""
    mock_client.read_device_info.side_effect = AnovaTimeoutError("no answer")
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = get_coordinator(mock_config_entry)
    assert coordinator.online
    assert coordinator.device_info is None

    mock_client.read_device_info.side_effect = None
    await poll(hass, freezer, coordinator)

    assert mock_client.read_device_info.await_count == 2
    assert coordinator.device_info == sample_device_info
    device = device_registry.async_get_device_by_connection(
        (CONNECTION_BLUETOOTH, ADDRESS), mock_config_entry.entry_id
    )
    assert device.model == "Precision Cooker (BT+WiFi, 900 W)"
    assert device.sw_version == "1.2.3"


async def test_failed_poll_does_not_save(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Only a successful poll schedules a save of the last values."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")

    with patch.object(AnovaStore, "async_schedule_save") as schedule_save:
        await poll(hass, freezer, coordinator)
        schedule_save.assert_not_called()

        mock_client.read_state.side_effect = None
        await poll(hass, freezer, coordinator)
        schedule_save.assert_called_once_with(
            mock_client.read_state.return_value, coordinator.device_info
        )
