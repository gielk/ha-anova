"""Tests for the update coordinator: dropouts, backoff and recovery."""

from __future__ import annotations

from datetime import timedelta
import logging
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.anova_ble.const import (
    CONF_POLL_INTERVAL,
    FAILURES_BEFORE_UNAVAILABLE,
    MAX_BACKOFF_INTERVAL,
)
from custom_components.anova_ble.coordinator import AnovaCoordinator
from custom_components.anova_ble.protocol import (
    AnovaConnectionError,
    AnovaError,
    AnovaState,
    AnovaTimeoutError,
)

from .const import ADDRESS

CLIMATE_ID = "climate.anova_2d60"
STATUS_ID = "sensor.anova_2d60_status"


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

    for failures in range(1, FAILURES_BEFORE_UNAVAILABLE):
        await poll(hass, freezer, coordinator)

        assert coordinator.failures == failures
        assert coordinator.last_update_success
        assert coordinator.data is last_data
        assert hass.states.get(CLIMATE_ID).state == "heat"
        assert hass.states.get(STATUS_ID).state == "running"


async def test_entities_unavailable_after_three_failures(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Three failed polls in a row make every entity unavailable."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")

    for _ in range(FAILURES_BEFORE_UNAVAILABLE):
        await poll(hass, freezer, coordinator)

    assert coordinator.failures == FAILURES_BEFORE_UNAVAILABLE
    assert not coordinator.last_update_success
    entries = er.async_entries_for_config_entry(
        entity_registry, init_integration.entry_id
    )
    assert entries
    for entry in entries:
        if entry.disabled:
            continue
        assert hass.states.get(entry.entity_id).state == STATE_UNAVAILABLE

    error = coordinator.last_exception
    assert isinstance(error, UpdateFailed)
    assert error.translation_key == "update_failed"
    assert error.translation_placeholders == {"error": "link dropped"}


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

    assert not coordinator.last_update_success
    assert coordinator.update_interval.total_seconds() == MAX_BACKOFF_INTERVAL


async def test_recovery_resets_backoff(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    sample_state: AnovaState,
) -> None:
    """One good poll resets the counter, the interval and the availability."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")
    for _ in range(FAILURES_BEFORE_UNAVAILABLE + 1):
        await poll(hass, freezer, coordinator)
    assert hass.states.get(CLIMATE_ID).state == STATE_UNAVAILABLE
    assert coordinator.update_interval > timedelta(seconds=15)

    mock_client.read_state.side_effect = None
    await poll(hass, freezer, coordinator)

    assert coordinator.failures == 0
    assert coordinator.update_interval == timedelta(seconds=15)
    assert coordinator.last_update_success
    assert hass.states.get(CLIMATE_ID).state == "heat"


async def test_failure_count_resets_after_dropout(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    sample_state: AnovaState,
) -> None:
    """Failures have to be in a row: a good poll in between starts over."""
    coordinator = get_coordinator(init_integration)
    for _ in range(FAILURES_BEFORE_UNAVAILABLE - 1):
        mock_client.read_state.side_effect = AnovaTimeoutError("no answer")
        await poll(hass, freezer, coordinator)
    mock_client.read_state.side_effect = None
    await poll(hass, freezer, coordinator)
    assert coordinator.failures == 0

    mock_client.read_state.side_effect = AnovaTimeoutError("no answer")
    await poll(hass, freezer, coordinator)

    assert coordinator.failures == 1
    assert hass.states.get(CLIMATE_ID).state == "heat"


async def test_out_of_range_counts_as_failure(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
    ble_device: MagicMock,
) -> None:
    """A cooker that Bluetooth cannot see is a failed poll, without a connect."""
    coordinator = get_coordinator(init_integration)
    mock_ble_device.return_value = None
    mock_client.read_state.reset_mock()

    for failures in range(1, FAILURES_BEFORE_UNAVAILABLE):
        await poll(hass, freezer, coordinator)
        assert coordinator.failures == failures
        assert coordinator.last_update_success
    await poll(hass, freezer, coordinator)

    assert coordinator.failures == FAILURES_BEFORE_UNAVAILABLE
    assert not coordinator.last_update_success
    assert hass.states.get(CLIMATE_ID).state == STATE_UNAVAILABLE
    mock_client.read_state.assert_not_awaited()
    error = coordinator.last_exception
    assert isinstance(error, UpdateFailed)
    assert error.translation_key == "device_out_of_range"
    assert error.translation_placeholders == {"address": ADDRESS}

    mock_ble_device.return_value = ble_device
    await poll(hass, freezer, coordinator)

    assert coordinator.failures == 0
    assert coordinator.last_update_success
    assert hass.states.get(CLIMATE_ID).state == "heat"
    mock_client.read_state.assert_awaited_once()


async def test_failures_are_logged_once(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dropouts are debug only. Unavailable and recovery are logged once."""
    coordinator = get_coordinator(init_integration)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="custom_components.anova_ble")
    mock_client.read_state.side_effect = AnovaConnectionError("link dropped")

    for _ in range(FAILURES_BEFORE_UNAVAILABLE - 1):
        await poll(hass, freezer, coordinator)
    assert loud_records(caplog, logging.WARNING) == []
    assert any(
        r.levelno == logging.DEBUG
        and r.name == "custom_components.anova_ble.coordinator"
        for r in caplog.records
    )

    for _ in range(3):
        await poll(hass, freezer, coordinator)
    errors = loud_records(caplog, logging.WARNING)
    assert len(errors) == 1
    assert "link dropped" in errors[0].getMessage()

    caplog.clear()
    mock_client.read_state.side_effect = None
    await poll(hass, freezer, coordinator)
    await poll(hass, freezer, coordinator)
    loud = loud_records(caplog, logging.INFO)
    assert len(loud) == 1
    assert "recovered" in loud[0].getMessage()


async def test_unexpected_error_is_not_swallowed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """Only cooker errors are treated as dropouts."""
    coordinator = get_coordinator(init_integration)
    mock_client.read_state.side_effect = AnovaError("odd")

    await poll(hass, freezer, coordinator)
    assert coordinator.failures == 1

    mock_client.read_state.side_effect = RuntimeError("bug")
    await poll(hass, freezer, coordinator)

    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, RuntimeError)
