"""Pure helpers that build commands and parse cooker responses."""

from __future__ import annotations

import math
import re

from .const import (
    MAX_CHUNK_SIZE,
    RESPONSE_INVALID_COMMAND,
    TIMER_MAX_MINUTES,
    TIMER_MIN_MINUTES,
)
from .exceptions import AnovaCommandError, AnovaResponseError, AnovaValueError
from .models import DeviceStatus, TemperatureUnit, TimerState

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_TIMER_RE = re.compile(r"^\s*(\d+)(?:\s+(running|stopped))?", re.IGNORECASE)
_TERMINATORS = (b"\r", b"\x00")

_STATUS_ALIASES: dict[str, DeviceStatus] = {
    "running": DeviceStatus.RUNNING,
    "stopped": DeviceStatus.STOPPED,
    "low water": DeviceStatus.LOW_WATER,
    "heater error": DeviceStatus.HEATER_ERROR,
    "power loss": DeviceStatus.POWER_LOSS,
    "power interrupt error": DeviceStatus.POWER_LOSS,
    "user change parameter": DeviceStatus.USER_CHANGE_PARAMETER,
}


class ResponseAssembler:
    """Reassemble notification chunks into complete responses.

    A response ends with a carriage return or a NUL byte. The official Anova code also
    treats a chunk shorter than 20 bytes as the end of a response; callers
    can use :attr:`awaiting_more` and :meth:`flush` to implement that rule.
    """

    def __init__(self, chunk_size: int = MAX_CHUNK_SIZE) -> None:
        """Initialize an empty buffer."""
        self._chunk_size = chunk_size
        self._buffer = bytearray()
        self._last_chunk_short = False

    def reset(self) -> None:
        """Drop any buffered data."""
        self._buffer.clear()
        self._last_chunk_short = False

    def feed(self, data: bytes) -> list[str]:
        """Add a chunk and return every response it completed."""
        self._buffer.extend(data)
        self._last_chunk_short = len(data) < self._chunk_size
        responses: list[str] = []
        while True:
            end = _first_terminator(self._buffer)
            if end is None:
                break
            text = _decode(self._buffer[:end])
            del self._buffer[: end + 1]
            if text:
                responses.append(text)
        if not _decode(self._buffer):
            # Only whitespace (for example a trailing "\n") is left.
            self._buffer.clear()
        return responses

    @property
    def has_partial(self) -> bool:
        """Return True when unterminated data is buffered."""
        return bool(self._buffer)

    @property
    def awaiting_more(self) -> bool:
        """Return True when the last chunk was full size, so more may follow."""
        return self.has_partial and not self._last_chunk_short

    def flush(self) -> str | None:
        """Return the unterminated buffer as a response and clear it."""
        text = _decode(self._buffer)
        self.reset()
        return text or None


def _first_terminator(buffer: bytearray) -> int | None:
    positions = [pos for t in _TERMINATORS if (pos := buffer.find(t)) != -1]
    return min(positions) if positions else None


def _decode(data: bytes | bytearray) -> str:
    return data.decode("ascii", errors="ignore").strip()


def check_response(response: str) -> str:
    """Raise when the cooker rejected the command."""
    if response.strip().lower() == RESPONSE_INVALID_COMMAND:
        raise AnovaCommandError(response)
    return response


def parse_float(response: str) -> float:
    """Parse a temperature such as ``56.5``."""
    match = _NUMBER_RE.search(response)
    if match is None:
        raise AnovaResponseError(f"Expected a number, got {response!r}")
    return float(match.group())


def parse_int(response: str) -> int:
    """Parse a whole number such as ``120``."""
    value = parse_float(response)
    if not value.is_integer():
        raise AnovaResponseError(f"Expected a whole number, got {response!r}")
    return int(value)


def parse_unit(response: str) -> TemperatureUnit:
    """Parse ``c`` or ``f``."""
    text = response.strip().lower()
    if text[:1] == "c":
        return TemperatureUnit.CELSIUS
    if text[:1] == "f":
        return TemperatureUnit.FAHRENHEIT
    raise AnovaResponseError(f"Expected a unit, got {response!r}")


def parse_status(response: str) -> DeviceStatus | None:
    """Parse the ``status`` answer, None when the value is unknown."""
    text = " ".join(response.strip().lower().split())
    return _STATUS_ALIASES.get(text)


def parse_timer(response: str) -> TimerState:
    """Parse ``read timer`` answers such as ``45 running``."""
    match = _TIMER_RE.match(response)
    if match is None:
        raise AnovaResponseError(f"Expected a timer value, got {response!r}")
    state = (match.group(2) or "").lower()
    return TimerState(minutes=int(match.group(1)), running=state == "running")


def validate_temperature(value: float, unit: TemperatureUnit) -> float:
    """Round to one decimal and check the range for the unit."""
    if not math.isfinite(value):
        raise AnovaValueError(f"Invalid temperature {value!r}")
    rounded = round(value, 1)
    if not unit.min_temperature <= rounded <= unit.max_temperature:
        raise AnovaValueError(
            f"Temperature {rounded} is outside {unit.min_temperature}"
            f" to {unit.max_temperature} °{unit.value.upper()}"
        )
    return rounded


def validate_timer(minutes: int) -> int:
    """Check the timer range in minutes."""
    if isinstance(minutes, bool) or int(minutes) != minutes:
        raise AnovaValueError(f"Timer must be whole minutes, got {minutes!r}")
    minutes = int(minutes)
    if not TIMER_MIN_MINUTES <= minutes <= TIMER_MAX_MINUTES:
        raise AnovaValueError(
            f"Timer {minutes} is outside {TIMER_MIN_MINUTES} to {TIMER_MAX_MINUTES} min"
        )
    return minutes


def cmd_set_temperature(value: float, unit: TemperatureUnit) -> str:
    """Build ``set temp`` with one decimal."""
    return f"set temp {validate_temperature(value, unit):.1f}"


def cmd_set_unit(unit: TemperatureUnit) -> str:
    """Build ``set unit``."""
    return f"set unit {TemperatureUnit(unit).value}"


def cmd_set_timer(minutes: int) -> str:
    """Build ``set timer``."""
    return f"set timer {validate_timer(minutes)}"


def encode_command(command: str, chunk_size: int = MAX_CHUNK_SIZE) -> list[bytes]:
    """Encode a command with its carriage return, split in BLE sized chunks."""
    payload = f"{command.strip()}\r".encode("ascii")
    return [payload[i : i + chunk_size] for i in range(0, len(payload), chunk_size)]
