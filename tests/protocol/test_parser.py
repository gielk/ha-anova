"""Tests for the pure protocol helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.anova_ble.protocol.exceptions import (
    AnovaCommandError,
    AnovaResponseError,
    AnovaValueError,
)
from custom_components.anova_ble.protocol.models import (
    AnovaDeviceInfo,
    DeviceStatus,
    TemperatureUnit,
    TimerState,
)
from custom_components.anova_ble.protocol.parser import (
    ResponseAssembler,
    check_response,
    cmd_set_temperature,
    cmd_set_timer,
    cmd_set_unit,
    convert_temperature,
    encode_command,
    parse_float,
    parse_int,
    parse_status,
    parse_timer,
    parse_unit,
)

PROTOCOL_DIR = (
    Path(__file__).parents[2] / "custom_components" / "anova_ble" / "protocol"
)


def test_protocol_package_has_no_homeassistant_imports() -> None:
    """The protocol package must stay usable without Home Assistant."""
    for path in PROTOCOL_DIR.glob("*.py"):
        text = path.read_text()
        assert "homeassistant" not in text, path.name


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        ([b"56.5\r"], ["56.5"]),
        ([b"56", b".5\r"], ["56.5"]),
        ([b"running\r\n"], ["running"]),
        ([b"stopped\x00"], ["stopped"]),
        ([b"c\rf\r"], ["c", "f"]),
        ([b"\r"], []),
        ([b"  23.4  \r"], ["23.4"]),
    ],
)
def test_assembler_terminated(chunks: list[bytes], expected: list[str]) -> None:
    """Responses end at carriage return or NUL."""
    assembler = ResponseAssembler()
    result: list[str] = []
    for chunk in chunks:
        result.extend(assembler.feed(chunk))
    assert result == expected
    assert not assembler.has_partial


def test_assembler_long_response_over_full_chunks() -> None:
    """A 20 byte chunk means more data follows."""
    assembler = ResponseAssembler()
    assert assembler.feed(b"anova f56-0123456789") == []
    assert assembler.has_partial
    assert assembler.awaiting_more
    assert assembler.feed(b"abcdef\r") == ["anova f56-0123456789abcdef"]


def test_assembler_short_chunk_without_terminator() -> None:
    """The official rule: a short chunk can end a response."""
    assembler = ResponseAssembler()
    assert assembler.feed(b"56.5") == []
    assert assembler.has_partial
    assert not assembler.awaiting_more
    assert assembler.flush() == "56.5"
    assert not assembler.has_partial
    assert assembler.flush() is None


def test_assembler_reset() -> None:
    """Reset drops buffered data."""
    assembler = ResponseAssembler()
    assembler.feed(b"half")
    assembler.reset()
    assert not assembler.has_partial
    assert assembler.feed(b"full\r") == ["full"]


def test_assembler_ignores_non_ascii() -> None:
    """Garbage bytes are dropped instead of raising."""
    assert ResponseAssembler().feed(b"\xff56.5\r") == ["56.5"]


def test_encode_command_adds_cr_and_chunks() -> None:
    """Commands end with CR and are split in 20 byte writes."""
    assert encode_command("status") == [b"status\r"]
    chunks = encode_command("x" * 25)
    assert [len(c) for c in chunks] == [20, 6]
    assert b"".join(chunks) == b"x" * 25 + b"\r"
    assert all(len(c) <= 20 for c in encode_command("set temp 56.5"))


def test_check_response() -> None:
    """Invalid Command is an error, anything else passes."""
    assert check_response("56.5") == "56.5"
    with pytest.raises(AnovaCommandError):
        check_response("Invalid Command")


@pytest.mark.parametrize(
    ("text", "value"),
    [("56.5", 56.5), ("23", 23.0), ("-1.5", -1.5), ("temp 99.9", 99.9)],
)
def test_parse_float(text: str, value: float) -> None:
    """Numbers are parsed from the response."""
    assert parse_float(text) == value


def test_parse_float_error() -> None:
    """A response without a number raises."""
    with pytest.raises(AnovaResponseError):
        parse_float("stopped")


def test_parse_int() -> None:
    """Timer echoes are whole numbers."""
    assert parse_int("120") == 120
    with pytest.raises(AnovaResponseError):
        parse_int("1.5")


@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("c", TemperatureUnit.CELSIUS),
        ("C", TemperatureUnit.CELSIUS),
        ("f", TemperatureUnit.FAHRENHEIT),
        ("f ", TemperatureUnit.FAHRENHEIT),
    ],
)
def test_parse_unit(text: str, unit: TemperatureUnit) -> None:
    """Units are c or f."""
    assert parse_unit(text) is unit


def test_parse_unit_error() -> None:
    """Anything else raises."""
    with pytest.raises(AnovaResponseError):
        parse_unit("56.5")


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("running", DeviceStatus.RUNNING),
        ("stopped", DeviceStatus.STOPPED),
        ("low water", DeviceStatus.LOW_WATER),
        ("Heater  Error", DeviceStatus.HEATER_ERROR),
        ("power loss", DeviceStatus.POWER_LOSS),
        ("power interrupt error", DeviceStatus.POWER_LOSS),
        ("user change parameter", DeviceStatus.USER_CHANGE_PARAMETER),
        ("something new", None),
    ],
)
def test_parse_status(text: str, status: DeviceStatus | None) -> None:
    """All known status strings map to an enum value."""
    assert parse_status(text) is status


@pytest.mark.parametrize(
    ("text", "timer"),
    [
        ("120 stopped", TimerState(120, False)),
        ("45 running", TimerState(45, True)),
        ("45 Running", TimerState(45, True)),
        ("0", TimerState(0, False)),
    ],
)
def test_parse_timer(text: str, timer: TimerState) -> None:
    """Timer is minutes plus state."""
    assert parse_timer(text) == timer


def test_parse_timer_error() -> None:
    """A timer response needs a number."""
    with pytest.raises(AnovaResponseError):
        parse_timer("running")


def test_cmd_set_temperature() -> None:
    """One decimal, range depends on the unit."""
    assert cmd_set_temperature(56.54, TemperatureUnit.CELSIUS) == "set temp 56.5"
    assert cmd_set_temperature(5, TemperatureUnit.CELSIUS) == "set temp 5.0"
    assert cmd_set_temperature(99.9, TemperatureUnit.CELSIUS) == "set temp 99.9"
    assert cmd_set_temperature(135, TemperatureUnit.FAHRENHEIT) == "set temp 135.0"
    for bad in (4.9, 100.0, float("nan"), float("inf")):
        with pytest.raises(AnovaValueError):
            cmd_set_temperature(bad, TemperatureUnit.CELSIUS)
    with pytest.raises(AnovaValueError):
        cmd_set_temperature(212.0, TemperatureUnit.FAHRENHEIT)
    with pytest.raises(AnovaValueError):
        cmd_set_temperature(40.9, TemperatureUnit.FAHRENHEIT)


def test_cmd_set_timer() -> None:
    """Whole minutes from 0 to 6000."""
    assert cmd_set_timer(0) == "set timer 0"
    assert cmd_set_timer(6000) == "set timer 6000"
    assert cmd_set_timer(120.0) == "set timer 120"  # type: ignore[arg-type]
    for bad in (-1, 6001, 1.5, True):
        with pytest.raises(AnovaValueError):
            cmd_set_timer(bad)  # type: ignore[arg-type]


def test_cmd_set_unit() -> None:
    """Units are sent lower case."""
    assert cmd_set_unit(TemperatureUnit.CELSIUS) == "set unit c"
    assert cmd_set_unit("f") == "set unit f"  # type: ignore[arg-type]


def test_anova_value_error_is_value_error() -> None:
    """Callers can catch a plain ValueError."""
    assert issubclass(AnovaValueError, ValueError)


def test_unit_ranges() -> None:
    """Ranges come from the protocol."""
    assert TemperatureUnit.CELSIUS.min_temperature == 5.0
    assert TemperatureUnit.CELSIUS.max_temperature == 99.9
    assert TemperatureUnit.FAHRENHEIT.min_temperature == 41.0
    assert TemperatureUnit.FAHRENHEIT.max_temperature == 211.8


@pytest.mark.parametrize(
    ("id_card", "wifi", "model"),
    [
        ("anova f56-abcdef0123", True, "Precision Cooker (BT+WiFi, 900 W)"),
        ("Anova F56-ABCDEF", True, "Precision Cooker (BT+WiFi, 900 W)"),
        ("anova a2-123", False, "Precision Cooker (BT, 800 W)"),
        (None, False, "Precision Cooker"),
    ],
)
def test_device_info_model(id_card: str | None, wifi: bool, model: str) -> None:
    """Model detection uses the id card prefix."""
    info = AnovaDeviceInfo(id_card=id_card, firmware_version=None)
    assert info.has_wifi is wifi
    assert info.model == model


def test_convert_temperature() -> None:
    """Celsius and Fahrenheit conversion."""
    c, f = TemperatureUnit.CELSIUS, TemperatureUnit.FAHRENHEIT
    assert convert_temperature(100, c, f) == 212
    assert convert_temperature(212, f, c) == 100
    assert convert_temperature(56.5, c, c) == 56.5
    assert round(convert_temperature(135, f, c), 1) == 57.2
