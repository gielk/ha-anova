"""Check that strings, icons and services describe everything the code uses."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
import yaml

from custom_components.anova_ble.protocol import DeviceStatus

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "anova_ble"
STRINGS = json.loads((INTEGRATION / "strings.json").read_text())
ICONS = json.loads((INTEGRATION / "icons.json").read_text())
SERVICES = yaml.safe_load((INTEGRATION / "services.yaml").read_text())

ENTITIES = {
    "binary_sensor": ["cooking", "timer_running"],
    "button": ["start_timer", "stop_timer"],
    "climate": ["cooker"],
    "number": ["timer"],
    "select": ["temperature_unit"],
    "sensor": [
        "current_temperature",
        "rssi",
        "status",
        "target_temperature",
        "timer_remaining",
    ],
}


def test_english_translation_matches_strings() -> None:
    """translations/en.json is a copy of strings.json."""
    english = json.loads((INTEGRATION / "translations" / "en.json").read_text())

    assert english == STRINGS


@pytest.mark.parametrize(
    ("platform", "key"),
    [(platform, key) for platform, keys in ENTITIES.items() for key in keys],
)
def test_entity_has_icon_and_name(platform: str, key: str) -> None:
    """Every entity has an icon, and a name unless it is the device itself."""
    assert "default" in ICONS["entity"][platform][key]
    if (platform, key) != ("climate", "cooker"):
        assert STRINGS["entity"][platform][key]["name"]


def test_enum_states_are_translated() -> None:
    """Every status and every unit option has a translated state."""
    states = STRINGS["entity"]["sensor"]["status"]["state"]
    assert set(states) == {status.value for status in DeviceStatus}
    units = STRINGS["entity"]["select"]["temperature_unit"]["state"]
    assert set(units) == {"celsius", "fahrenheit"}
    assert set(ICONS["entity"]["select"]["temperature_unit"]["state"]) == set(units)


def test_exception_keys_in_code_are_translated() -> None:
    """Every translation key used for an exception exists in strings.json."""
    used = {"update_failed", "device_out_of_range"}  # Passed on by the coordinator.
    for path in INTEGRATION.glob("*.py"):
        used |= set(re.findall(r'translation_key="(\w+)"', path.read_text()))

    assert {"command_failed", "invalid_value", "device_not_found"} <= used
    assert used <= set(STRINGS["exceptions"])
    for key in used:
        assert STRINGS["exceptions"][key]["message"]


@pytest.mark.parametrize("service", ["start_cook", "stop_cook"])
def test_services_are_described(service: str) -> None:
    """Both actions have a name, a description, an icon and a target."""
    assert STRINGS["services"][service]["name"]
    assert STRINGS["services"][service]["description"]
    assert ICONS["services"][service]["service"]
    target = SERVICES[service]["target"]["entity"]
    assert target == {"integration": "anova_ble", "domain": "climate"}


def test_start_cook_fields() -> None:
    """The temperature is required and the duration is optional."""
    fields = SERVICES["start_cook"]["fields"]
    assert fields["temperature"]["required"] is True
    assert fields["temperature"]["selector"]["number"] == {
        "min": 5,
        "max": 212,
        "step": 0.1,
        "mode": "box",
    }
    assert fields["duration"]["required"] is False
    assert "duration" in fields["duration"]["selector"]
    assert set(STRINGS["services"]["start_cook"]["fields"]) == set(fields)
    assert "fields" not in SERVICES["stop_cook"]


def test_options_explain_the_connection_modes() -> None:
    """The option texts explain the trade off between the two modes."""
    step = STRINGS["options"]["step"]["init"]
    text = step["data_description"]["connection_mode"]

    assert "phone app" in text
    assert "reconnect" in text
    assert "one Bluetooth connection" in text
    assert set(step["data"]) == {"poll_interval", "connection_mode"}
    assert set(step["data_description"]) == {"poll_interval", "connection_mode"}
    assert set(STRINGS["selector"]["connection_mode"]["options"]) == {
        "per_poll",
        "persistent",
    }


def test_config_flow_texts() -> None:
    """The flow has texts for every error and abort reason it can return."""
    config = STRINGS["config"]

    assert {"cannot_connect", "unknown"} <= set(config["error"])
    assert {
        "already_configured",
        "already_in_progress",
        "no_devices_found",
        "not_supported",
    } <= set(config["abort"])
    assert {"user", "bluetooth_confirm"} == set(config["step"])
