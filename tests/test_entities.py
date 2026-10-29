"""Tests for the entities and the cook actions."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
import probatio
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anova_ble.const import DOMAIN
from custom_components.anova_ble.protocol import (
    AnovaConnectionError,
    AnovaState,
    AnovaValueError,
    DeviceStatus,
    TemperatureUnit,
    TimerState,
)

from .const import ADDRESS

CLIMATE_ID = "climate.anova_2d60"
BLE_MODULE = "homeassistant.components.bluetooth"

STOPPED = AnovaState(
    status=DeviceStatus.STOPPED,
    raw_status="stopped",
    unit=TemperatureUnit.CELSIUS,
    current_temperature=23.4,
    target_temperature=56.5,
    timer=TimerState(minutes=120, running=False),
)
AT_TARGET = AnovaState(
    status=DeviceStatus.RUNNING,
    raw_status="running",
    unit=TemperatureUnit.CELSIUS,
    current_temperature=56.2,
    target_temperature=56.5,
    timer=TimerState(minutes=30, running=True),
)
FAHRENHEIT = AnovaState(
    status=DeviceStatus.RUNNING,
    raw_status="running",
    unit=TemperatureUnit.FAHRENHEIT,
    current_temperature=130.0,
    target_temperature=140.0,
    timer=TimerState(minutes=0, running=False),
)
LOW_WATER = AnovaState(
    status=DeviceStatus.LOW_WATER,
    raw_status="low water",
    unit=TemperatureUnit.CELSIUS,
    current_temperature=40.0,
    target_temperature=56.5,
    timer=TimerState(minutes=0, running=False),
)
UNKNOWN_STATUS = AnovaState(
    status=None,
    raw_status="something new",
    unit=TemperatureUnit.CELSIUS,
    current_temperature=40.0,
    target_temperature=56.5,
    timer=TimerState(minutes=0, running=False),
)


def entity_id(entity_registry: er.EntityRegistry, domain: str, key: str) -> str:
    """Return the entity id of an entity of the cooker by its key."""
    found = entity_registry.async_get_entity_id(domain, DOMAIN, f"{ADDRESS}_{key}")
    assert found is not None, f"{domain} {key} is not registered"
    return found


async def test_unique_ids(
    entity_registry: er.EntityRegistry, init_integration: MockConfigEntry
) -> None:
    """Every entity of the contract exists with the documented unique id."""
    expected = {
        ("climate", "cooker"),
        ("sensor", "current_temperature"),
        ("sensor", "target_temperature"),
        ("sensor", "timer_remaining"),
        ("sensor", "status"),
        ("sensor", "rssi"),
        ("binary_sensor", "cooking"),
        ("binary_sensor", "timer_running"),
        ("number", "timer"),
        ("button", "start_timer"),
        ("button", "stop_timer"),
        ("select", "temperature_unit"),
    }

    for domain, key in expected:
        registered = entity_registry.async_get(entity_id(entity_registry, domain, key))
        assert registered is not None
        assert registered.unique_id == f"{ADDRESS}_{key}"
        assert registered.translation_key == key
    entries = er.async_entries_for_config_entry(
        entity_registry, init_integration.entry_id
    )
    assert len(entries) == len(expected)


async def test_entity_categories(
    entity_registry: er.EntityRegistry, init_integration: MockConfigEntry
) -> None:
    """The signal strength is a disabled diagnostic, the unit is a config entity."""
    rssi = entity_registry.async_get(entity_id(entity_registry, "sensor", "rssi"))
    assert rssi.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert rssi.entity_category is er.EntityCategory.DIAGNOSTIC

    unit = entity_registry.async_get(
        entity_id(entity_registry, "select", "temperature_unit")
    )
    assert unit.entity_category is er.EntityCategory.CONFIG


async def test_states(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """A heating cooker with a running timer."""
    climate = hass.states.get(CLIMATE_ID)
    assert climate.state == HVACMode.HEAT
    assert climate.attributes[ATTR_CURRENT_TEMPERATURE] == 54.2
    assert climate.attributes[ATTR_TEMPERATURE] == 56.5
    assert climate.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert climate.attributes[ATTR_HVAC_MODES] == [HVACMode.OFF, HVACMode.HEAT]
    assert climate.attributes[ATTR_MIN_TEMP] == 5.0
    assert climate.attributes[ATTR_MAX_TEMP] == 99.9
    assert climate.attributes["target_temp_step"] == 0.1

    current = hass.states.get(
        entity_id(entity_registry, SENSOR_DOMAIN, "current_temperature")
    )
    assert current.state == "54.2"
    assert current.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    assert current.attributes["device_class"] == "temperature"
    assert current.attributes["state_class"] == "measurement"

    target = hass.states.get(
        entity_id(entity_registry, SENSOR_DOMAIN, "target_temperature")
    )
    assert target.state == "56.5"
    assert "state_class" not in target.attributes

    remaining = hass.states.get(
        entity_id(entity_registry, SENSOR_DOMAIN, "timer_remaining")
    )
    assert remaining.state == "90"
    assert remaining.attributes[ATTR_UNIT_OF_MEASUREMENT] == "min"
    assert remaining.attributes["device_class"] == "duration"

    status = hass.states.get(entity_id(entity_registry, SENSOR_DOMAIN, "status"))
    assert status.state == "running"
    assert status.attributes["options"] == [s.value for s in DeviceStatus]

    cooking = hass.states.get(
        entity_id(entity_registry, BINARY_SENSOR_DOMAIN, "cooking")
    )
    assert cooking.state == STATE_ON
    assert cooking.attributes["device_class"] == "running"

    timer = hass.states.get(entity_id(entity_registry, NUMBER_DOMAIN, "timer"))
    assert timer.state == "90"
    assert timer.attributes["min"] == 0
    assert timer.attributes["max"] == 6000
    assert timer.attributes["step"] == 1
    assert timer.attributes["mode"] == "box"
    assert timer.attributes["device_class"] == "duration"
    assert timer.attributes[ATTR_UNIT_OF_MEASUREMENT] == "min"

    unit = hass.states.get(
        entity_id(entity_registry, SELECT_DOMAIN, "temperature_unit")
    )
    assert unit.state == "celsius"
    assert unit.attributes["options"] == ["celsius", "fahrenheit"]

    for key in ("start_timer", "stop_timer"):
        button = hass.states.get(entity_id(entity_registry, BUTTON_DOMAIN, key))
        assert button.state == STATE_UNKNOWN


@pytest.mark.parametrize(
    ("sample_state", "mode", "action", "cooking", "status"),
    [
        (STOPPED, HVACMode.OFF, HVACAction.OFF, STATE_OFF, "stopped"),
        (AT_TARGET, HVACMode.HEAT, HVACAction.IDLE, STATE_ON, "running"),
        (LOW_WATER, HVACMode.OFF, HVACAction.OFF, STATE_OFF, "low_water"),
        (UNKNOWN_STATUS, HVACMode.OFF, HVACAction.OFF, STATE_OFF, STATE_UNKNOWN),
    ],
)
async def test_state_variants(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
    mode: HVACMode,
    action: HVACAction,
    cooking: str,
    status: str,
) -> None:
    """Climate mode and action, cooking and status follow the cooker."""
    climate = hass.states.get(CLIMATE_ID)
    assert climate.state == mode
    assert climate.attributes[ATTR_HVAC_ACTION] == action
    assert (
        hass.states.get(
            entity_id(entity_registry, BINARY_SENSOR_DOMAIN, "cooking")
        ).state
        == cooking
    )
    assert (
        hass.states.get(entity_id(entity_registry, SENSOR_DOMAIN, "status")).state
        == status
    )


@pytest.mark.parametrize("sample_state", [FAHRENHEIT])
async def test_fahrenheit_cooker(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """A cooker in Fahrenheit is shown in the unit system of Home Assistant."""
    climate = hass.states.get(CLIMATE_ID)
    assert climate.attributes[ATTR_CURRENT_TEMPERATURE] == 54.4
    assert climate.attributes[ATTR_TEMPERATURE] == 60.0
    assert climate.attributes[ATTR_MIN_TEMP] == 5.0
    assert climate.attributes[ATTR_MAX_TEMP] == 99.9
    assert climate.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING

    current = hass.states.get(
        entity_id(entity_registry, SENSOR_DOMAIN, "current_temperature")
    )
    assert float(current.state) == pytest.approx(54.444, abs=0.001)
    assert current.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    unit = hass.states.get(
        entity_id(entity_registry, SELECT_DOMAIN, "temperature_unit")
    )
    assert unit.state == "fahrenheit"


async def test_unit_change_on_the_cooker(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """When the cooker switches to Fahrenheit the values stay the same in °C."""
    mock_client.read_state.return_value = AnovaState(
        status=DeviceStatus.RUNNING,
        raw_status="running",
        unit=TemperatureUnit.FAHRENHEIT,
        current_temperature=129.56,
        target_temperature=133.7,
        timer=TimerState(minutes=90, running=True),
    )

    await init_integration.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    current = hass.states.get(
        entity_id(entity_registry, SENSOR_DOMAIN, "current_temperature")
    )
    assert float(current.state) == pytest.approx(54.2, abs=0.01)
    assert current.attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C"
    climate = hass.states.get(CLIMATE_ID)
    assert climate.attributes[ATTR_TEMPERATURE] == 56.5
    unit = hass.states.get(
        entity_id(entity_registry, SELECT_DOMAIN, "temperature_unit")
    )
    assert unit.state == "fahrenheit"


async def test_signal_strength(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
    anova_service_info: MagicMock,
) -> None:
    """The signal strength comes from the last advertisement."""
    rssi_id = entity_id(entity_registry, SENSOR_DOMAIN, "rssi")
    entity_registry.async_update_entity(rssi_id, disabled_by=None)

    with patch(
        f"{BLE_MODULE}.async_last_service_info", return_value=anova_service_info
    ):
        await hass.config_entries.async_reload(init_integration.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(rssi_id)
    assert state.state == "-60"
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "dBm"
    assert state.attributes["device_class"] == "signal_strength"


async def test_signal_strength_unknown_without_advertisement(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """No advertisement seen yet means an unknown value."""
    rssi_id = entity_id(entity_registry, SENSOR_DOMAIN, "rssi")
    entity_registry.async_update_entity(rssi_id, disabled_by=None)

    with patch(f"{BLE_MODULE}.async_last_service_info", return_value=None):
        await hass.config_entries.async_reload(init_integration.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(rssi_id).state == STATE_UNKNOWN


async def test_climate_set_temperature(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Setting the temperature sends it in the unit of the cooker."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 60.5},
        blocking=True,
    )

    mock_client.set_target_temperature.assert_awaited_once_with(
        60.5, unit=TemperatureUnit.CELSIUS
    )


@pytest.mark.parametrize(
    ("mode", "method", "other"),
    [(HVACMode.HEAT, "start", "stop"), (HVACMode.OFF, "stop", "start")],
)
async def test_climate_set_temperature_with_hvac_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    mode: HVACMode,
    method: str,
    other: str,
) -> None:
    """A mode in the same call is applied after the temperature."""
    calls = MagicMock()
    calls.attach_mock(mock_client.set_target_temperature, "set_temperature")
    calls.attach_mock(getattr(mock_client, method), "mode")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 60.5, ATTR_HVAC_MODE: mode},
        blocking=True,
    )

    assert [call[0] for call in calls.mock_calls] == ["set_temperature", "mode"]
    getattr(mock_client, other).assert_not_awaited()


@pytest.mark.parametrize("sample_state", [FAHRENHEIT])
async def test_climate_set_temperature_fahrenheit_cooker(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The climate service converts to the cooker unit, here 60 °C to 140 °F."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 60},
        blocking=True,
    )

    mock_client.set_target_temperature.assert_awaited_once_with(
        140.0, unit=TemperatureUnit.FAHRENHEIT
    )


async def test_climate_set_temperature_out_of_range(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Home Assistant rejects a target outside the range of the cooker."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_TEMPERATURE: 120},
            blocking=True,
        )

    mock_client.set_target_temperature.assert_not_awaited()


@pytest.mark.parametrize(
    ("mode", "method"), [(HVACMode.HEAT, "start"), (HVACMode.OFF, "stop")]
)
async def test_climate_set_hvac_mode(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    mode: HVACMode,
    method: str,
) -> None:
    """Heat starts the cooker and off stops it."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE_ID, ATTR_HVAC_MODE: mode},
        blocking=True,
    )

    getattr(mock_client, method).assert_awaited_once_with()


@pytest.mark.parametrize(
    ("service", "method"), [(SERVICE_TURN_ON, "start"), (SERVICE_TURN_OFF, "stop")]
)
async def test_climate_turn_on_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    service: str,
    method: str,
) -> None:
    """Turning the thermostat on or off starts or stops the cooker."""
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: CLIMATE_ID}, blocking=True
    )

    getattr(mock_client, method).assert_awaited_once_with()


async def test_number_sets_timer(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """The timer number sets whole minutes on the cooker."""
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {
            ATTR_ENTITY_ID: entity_id(entity_registry, NUMBER_DOMAIN, "timer"),
            ATTR_VALUE: 150,
        },
        blocking=True,
    )

    mock_client.set_timer.assert_awaited_once_with(150)


@pytest.mark.parametrize(
    ("key", "method"), [("start_timer", "start_timer"), ("stop_timer", "stop_timer")]
)
async def test_buttons(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    key: str,
    method: str,
) -> None:
    """The buttons start and stop the countdown."""
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: entity_id(entity_registry, BUTTON_DOMAIN, key)},
        blocking=True,
    )

    getattr(mock_client, method).assert_awaited_once_with()


@pytest.mark.parametrize(
    ("option", "unit"),
    [
        ("fahrenheit", TemperatureUnit.FAHRENHEIT),
        ("celsius", TemperatureUnit.CELSIUS),
    ],
)
async def test_select_unit(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    option: str,
    unit: TemperatureUnit,
) -> None:
    """The select changes the unit of the cooker."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {
            ATTR_ENTITY_ID: entity_id(
                entity_registry, SELECT_DOMAIN, "temperature_unit"
            ),
            ATTR_OPTION: option,
        },
        blocking=True,
    )

    mock_client.set_unit.assert_awaited_once_with(unit)


async def test_action_requests_refresh(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """After an action the entities are refreshed."""
    assert mock_client.read_state.await_count == 1

    await hass.services.async_call(
        CLIMATE_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: CLIMATE_ID}, blocking=True
    )

    assert mock_client.read_state.await_count == 2


async def test_failed_action_does_not_refresh(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A command that failed leaves the coordinator alone."""
    mock_client.stop.side_effect = AnovaConnectionError("link dropped")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: CLIMATE_ID},
            blocking=True,
        )

    assert mock_client.read_state.await_count == 1


# start_cook and stop_cook ---------------------------------------------------


async def start_cook(hass: HomeAssistant, **data: object) -> None:
    """Call anova_ble.start_cook on the climate entity."""
    await hass.services.async_call(
        DOMAIN,
        "start_cook",
        {ATTR_ENTITY_ID: CLIMATE_ID, **data},
        blocking=True,
    )


async def test_start_cook(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Temperature and duration are passed on, the unit is the one of the cooker."""
    await start_cook(hass, temperature=56.5, duration="01:30:00")

    mock_client.start_cook.assert_awaited_once_with(
        56.5, minutes=90, unit=TemperatureUnit.CELSIUS
    )


async def test_start_cook_without_duration(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Without a duration no timer is set."""
    await start_cook(hass, temperature=62)

    mock_client.start_cook.assert_awaited_once_with(
        62.0, minutes=None, unit=TemperatureUnit.CELSIUS
    )


@pytest.mark.parametrize(
    ("duration", "minutes"),
    [
        ({"hours": 2}, 120),
        ({"minutes": 1}, 1),
        ({"minutes": 1, "seconds": 1}, 2),
        ({"minutes": 59, "seconds": 59}, 60),
        ({"seconds": 30}, 1),
        (timedelta(hours=1, minutes=30, seconds=1), 91),
        ({"seconds": 0}, 0),
    ],
)
async def test_start_cook_duration_rounds_up(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    duration: object,
    minutes: int,
) -> None:
    """Partial minutes round up, so the food never cooks shorter than asked."""
    await start_cook(hass, temperature=56.5, duration=duration)

    mock_client.start_cook.assert_awaited_once_with(
        56.5, minutes=minutes, unit=TemperatureUnit.CELSIUS
    )


@pytest.mark.parametrize(
    ("units", "state", "temperature", "expected", "unit"),
    [
        # Home Assistant in °F, cooker in °C: 133.7 °F is 56.5 °C.
        (US_CUSTOMARY_SYSTEM, None, 133.7, 56.5, TemperatureUnit.CELSIUS),
        # Rounded to 0.1: 135 °F is 57.22 °C.
        (US_CUSTOMARY_SYSTEM, None, 135, 57.2, TemperatureUnit.CELSIUS),
        # Home Assistant in °C, cooker in °F: 56.5 °C is 133.7 °F.
        (METRIC_SYSTEM, FAHRENHEIT, 56.5, 133.7, TemperatureUnit.FAHRENHEIT),
        # Same unit: untouched.
        (US_CUSTOMARY_SYSTEM, FAHRENHEIT, 140, 140.0, TemperatureUnit.FAHRENHEIT),
        (METRIC_SYSTEM, None, 56.5, 56.5, TemperatureUnit.CELSIUS),
    ],
)
async def test_start_cook_converts_temperature(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: MagicMock,
    mock_ble_device: MagicMock,
    units: object,
    state: AnovaState | None,
    temperature: float,
    expected: float,
    unit: TemperatureUnit,
) -> None:
    """The temperature is given in the Home Assistant unit and converted."""
    if state is not None:
        mock_client.read_state.return_value = state
    hass.config.units = units
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    await start_cook(hass, temperature=temperature, duration="00:10:00")

    mock_client.start_cook.assert_awaited_once_with(expected, minutes=10, unit=unit)


async def test_stop_cook(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Stopping a cook stops the cooker."""
    await hass.services.async_call(
        DOMAIN, "stop_cook", {ATTR_ENTITY_ID: CLIMATE_ID}, blocking=True
    )

    mock_client.stop.assert_awaited_once_with()


async def test_start_cook_value_error_is_validation_error(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A value the cooker refuses is reported as a validation error."""
    mock_client.start_cook.side_effect = AnovaValueError(
        "Temperature 150.0 is too high"
    )

    with pytest.raises(ServiceValidationError) as exc_info:
        await start_cook(hass, temperature=150, duration="01:00:00")

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "invalid_value"
    assert exc_info.value.translation_placeholders == {
        "error": "Temperature 150.0 is too high"
    }
    # A refused value changes nothing, so there is no refresh.
    assert mock_client.read_state.await_count == 1


async def test_timer_value_error_is_validation_error(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
) -> None:
    """The same mapping applies to every entity action."""
    mock_client.set_timer.side_effect = AnovaValueError("Timer 6001 is outside")

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {
                ATTR_ENTITY_ID: entity_id(entity_registry, NUMBER_DOMAIN, "timer"),
                ATTR_VALUE: 100,
            },
            blocking=True,
        )

    assert exc_info.value.translation_key == "invalid_value"


async def test_stop_cook_connection_error_is_home_assistant_error(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Other client errors become a translated error that is not a validation error."""
    mock_client.stop.side_effect = AnovaConnectionError("link dropped")

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "stop_cook", {ATTR_ENTITY_ID: CLIMATE_ID}, blocking=True
        )

    assert not isinstance(exc_info.value, ServiceValidationError)
    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "command_failed"
    assert exc_info.value.translation_placeholders == {"error": "link dropped"}


async def test_start_cook_requires_temperature(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The temperature field is required."""
    with pytest.raises(probatio.Invalid, match="temperature"):
        await start_cook(hass, duration="01:00:00")

    mock_client.start_cook.assert_not_awaited()
