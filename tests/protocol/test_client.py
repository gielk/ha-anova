"""Tests for the BLE client, with a fake bleak client standing in for BLE."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import pytest

from custom_components.anova_ble.protocol import (
    AnovaClient,
    AnovaCommandError,
    AnovaConnectionError,
    AnovaTimeoutError,
    AnovaValueError,
    DeviceStatus,
    TemperatureUnit,
    TimerState,
)
from custom_components.anova_ble.protocol.const import CHARACTERISTIC_UUID

MODULE = "custom_components.anova_ble.protocol.client"
ADDRESS = "01:02:03:04:2D:60"

Reply = str | bytes | list[bytes] | None


def default_replies() -> dict[str, Reply]:
    """Answers of a stopped cooker at 23.4 °C with a 120 minute timer."""
    return {
        "status": "stopped",
        "read unit": "c",
        "read temp": "23.4",
        "read set temp": "56.5",
        "read timer": "120 stopped",
        "start": "start",
        "stop": "stop",
        "start time": "start time",
        "stop time": "stop time",
        "get id card": "anova f56-0123456789",
        "version": "1.4.4",
    }


class FakeBleakClient:
    """Minimal stand in for BleakClientWithServiceCache."""

    def __init__(self, replies: dict[str, Reply]) -> None:
        """Answer commands from ``replies``; a callable may compute them."""
        self.replies = replies
        self.is_connected = True
        self.writes: list[bytes] = []
        self.commands: list[str] = []
        self.char = MagicMock(uuid=CHARACTERISTIC_UUID)
        self.services = MagicMock()
        self.services.get_characteristic.return_value = self.char
        self.disconnect = AsyncMock(side_effect=self._disconnect)
        self.clear_cache = AsyncMock()
        self.notify: Callable[[Any, bytearray], None] | None = None
        self.disconnected_callback: Callable[[Any], None] | None = None
        self._pending = b""

    async def start_notify(self, char: Any, callback: Any) -> None:
        """Remember the notification callback."""
        assert char is self.char
        self.notify = callback

    async def write_gatt_char(
        self, char: Any, data: bytes, response: bool | None = None
    ) -> None:
        """Collect writes and answer complete commands."""
        assert char is self.char
        assert response is False
        assert len(data) <= 20
        self.writes.append(bytes(data))
        self._pending += bytes(data)
        if self._pending.endswith(b"\r"):
            command = self._pending[:-1].decode()
            self._pending = b""
            self.commands.append(command)
            self._answer(command)

    def _answer(self, command: str) -> None:
        reply = self.replies.get(command, "Invalid Command")
        if callable(reply):
            reply = reply(command)
        if command.startswith("set temp "):
            reply = self.replies.get("set temp", command.removeprefix("set temp "))
        elif command.startswith("set timer "):
            reply = self.replies.get("set timer", command.removeprefix("set timer "))
        elif command.startswith("set unit "):
            reply = command.removeprefix("set unit ")
        if reply is None:
            return  # Simulate a lost answer.
        if isinstance(reply, str):
            chunks = [f"{reply}\r".encode()]
        elif isinstance(reply, bytes):
            chunks = [reply]
        else:
            chunks = reply
        loop = asyncio.get_running_loop()
        for chunk in chunks:
            loop.call_soon(self._deliver, chunk)

    def _deliver(self, chunk: bytes) -> None:
        if self.notify is not None and self.is_connected:
            self.notify(self.char, bytearray(chunk))

    def drop_link(self) -> None:
        """Simulate the cooker going away."""
        self.is_connected = False
        if self.disconnected_callback is not None:
            self.disconnected_callback(self)

    async def _disconnect(self) -> None:
        self.is_connected = False


@pytest.fixture
def replies() -> dict[str, Reply]:
    """Replies of the fake cooker, tests can change them."""
    return default_replies()


@pytest.fixture
def fakes(replies: dict[str, Reply]) -> list[FakeBleakClient]:
    """Every fake bleak client created, newest last."""
    return []


@pytest.fixture
def mock_connect(
    replies: dict[str, Reply], fakes: list[FakeBleakClient]
) -> Generator[AsyncMock]:
    """Patch establish_connection to hand out fake clients."""

    async def _connect(
        _cls: Any, _device: Any, _name: str, **kwargs: Any
    ) -> FakeBleakClient:
        fake = FakeBleakClient(replies)
        fake.disconnected_callback = kwargs["disconnected_callback"]
        fakes.append(fake)
        return fake

    with (
        patch(f"{MODULE}.establish_connection", side_effect=_connect) as mock,
        patch(f"{MODULE}.RESPONSE_GRACE_PERIOD", 0.01),
    ):
        yield mock


def make_client(**kwargs: Any) -> AnovaClient:
    """Create a client with fast timings."""
    kwargs.setdefault("command_timeout", 0.2)
    kwargs.setdefault("command_gap", 0)
    kwargs.setdefault("disconnect_delay", None)
    device = BLEDevice(ADDRESS, "Anova", details=None)
    return AnovaClient(device, **kwargs)


async def test_read_state(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """A poll reads everything in one connection."""
    client = make_client()
    state = await client.read_state()
    assert state.status is DeviceStatus.STOPPED
    assert state.raw_status == "stopped"
    assert state.unit is TemperatureUnit.CELSIUS
    assert state.current_temperature == 23.4
    assert state.target_temperature == 56.5
    assert state.timer == TimerState(120, False)
    assert not state.is_cooking
    assert client.unit is TemperatureUnit.CELSIUS
    assert mock_connect.call_count == 1
    assert fakes[0].commands == [
        "status",
        "read unit",
        "read temp",
        "read set temp",
        "read timer",
    ]
    assert all(w.endswith(b"\r") for w in fakes[0].writes)
    assert client.is_connected
    assert client.address == ADDRESS
    assert client.name == "Anova"


async def test_running_state(
    mock_connect: AsyncMock, replies: dict[str, Reply]
) -> None:
    """Running status means cooking."""
    replies["status"] = "running"
    replies["read timer"] = "45 running"
    state = await make_client().read_state()
    assert state.is_cooking
    assert state.timer == TimerState(45, True)


async def test_split_and_unterminated_responses(
    mock_connect: AsyncMock, replies: dict[str, Reply]
) -> None:
    """Split chunks, NUL terminators and missing terminators all work."""
    replies["read temp"] = [b"23", b".4\r"]
    replies["read set temp"] = b"56.5\x00"
    replies["read timer"] = b"120 stopped"  # short chunk, no terminator
    replies["status"] = [b"\r", b"running\r\n"]  # stray empty line first
    state = await make_client().read_state()
    assert state.current_temperature == 23.4
    assert state.target_temperature == 56.5
    assert state.timer == TimerState(120, False)
    assert state.status is DeviceStatus.RUNNING


async def test_unknown_status(
    mock_connect: AsyncMock, replies: dict[str, Reply]
) -> None:
    """Unknown status strings are kept raw."""
    replies["status"] = "brand new state"
    state = await make_client().read_state()
    assert state.status is None
    assert state.raw_status == "brand new state"


async def test_timeout_disconnects_and_recovers(
    mock_connect: AsyncMock, replies: dict[str, Reply], fakes: list[FakeBleakClient]
) -> None:
    """A lost answer closes the link, the next call reconnects."""
    replies["read temp"] = None
    client = make_client()
    with pytest.raises(AnovaTimeoutError):
        await client.read_state()
    assert not client.is_connected
    fakes[0].disconnect.assert_awaited()
    replies["read temp"] = "23.4"
    assert (await client.read_state()).current_temperature == 23.4
    assert mock_connect.call_count == 2


async def test_timeout_is_connection_error() -> None:
    """Callers can treat timeouts as connection problems."""
    assert issubclass(AnovaTimeoutError, AnovaConnectionError)


async def test_invalid_command_keeps_link(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """A rejected command does not drop the connection."""
    client = make_client()
    with pytest.raises(AnovaCommandError):
        await client.send_command("bogus")
    assert client.is_connected
    assert await client.send_command("status") == "stopped"
    assert mock_connect.call_count == 1


async def test_garbage_response_disconnects(
    mock_connect: AsyncMock, replies: dict[str, Reply]
) -> None:
    """A response that does not parse resets the link (likely out of sync)."""
    replies["read unit"] = "56.5"
    client = make_client()
    with pytest.raises(Exception, match="unit"):
        await client.read_state()
    assert not client.is_connected


async def test_link_drop_during_command(
    mock_connect: AsyncMock, replies: dict[str, Reply], fakes: list[FakeBleakClient]
) -> None:
    """Dropping the link fails the pending command at once."""

    def _drop(_command: str) -> None:
        asyncio.get_running_loop().call_soon(fakes[-1].drop_link)

    on_disconnect = MagicMock()
    replies["read temp"] = _drop
    client = make_client(command_timeout=5, on_disconnect=on_disconnect)
    with pytest.raises(AnovaConnectionError, match="dropped"):
        await asyncio.wait_for(client.read_state(), 1)
    assert not client.is_connected
    on_disconnect.assert_called_once()


async def test_connect_failure(mock_connect: AsyncMock) -> None:
    """BLE errors while connecting become AnovaConnectionError."""
    mock_connect.side_effect = BleakError("out of range")
    client = make_client()
    with pytest.raises(AnovaConnectionError, match="out of range"):
        await client.read_state()
    assert not client.is_connected


async def test_missing_characteristic(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient], replies: dict[str, Reply]
) -> None:
    """A device without ffe1 is rejected and the cache cleared."""
    fake = FakeBleakClient(replies)
    fake.services.get_characteristic.return_value = None
    mock_connect.side_effect = None
    mock_connect.return_value = fake
    with pytest.raises(AnovaConnectionError, match="not found"):
        await make_client().read_state()
    fake.clear_cache.assert_awaited_once()
    fake.disconnect.assert_awaited_once()


async def test_start_notify_failure(
    mock_connect: AsyncMock, replies: dict[str, Reply]
) -> None:
    """A failure to subscribe disconnects and raises."""
    fake = FakeBleakClient(replies)
    fake.start_notify = AsyncMock(side_effect=BleakError("notify"))  # type: ignore[method-assign]
    mock_connect.side_effect = None
    mock_connect.return_value = fake
    with pytest.raises(AnovaConnectionError, match="notify"):
        await make_client().read_state()
    fake.disconnect.assert_awaited_once()


async def test_write_failure(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """A failing write becomes AnovaConnectionError and drops the link."""
    client = make_client()
    await client.connect()
    fakes[0].write_gatt_char = AsyncMock(side_effect=BleakError("write"))  # type: ignore[method-assign]
    with pytest.raises(AnovaConnectionError, match="write"):
        await client.start()
    assert not client.is_connected


async def test_idle_disconnect(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """With a disconnect delay the link closes after the session."""
    client = make_client(disconnect_delay=0.01)
    await client.read_state()
    assert client.is_connected
    await asyncio.sleep(0.05)
    assert not client.is_connected
    fakes[0].disconnect.assert_awaited_once()


async def test_idle_disconnect_postponed_by_new_command(
    mock_connect: AsyncMock,
) -> None:
    """A new command restarts the idle timer instead of reconnecting."""
    client = make_client(disconnect_delay=0.05)
    await client.read_state()
    await asyncio.sleep(0.03)
    await client.start()
    await asyncio.sleep(0.03)
    assert client.is_connected
    assert mock_connect.call_count == 1
    await asyncio.sleep(0.06)
    assert not client.is_connected


async def test_persistent_connection(mock_connect: AsyncMock) -> None:
    """Without a delay the link stays open until disconnect()."""
    client = make_client(disconnect_delay=None)
    await client.read_state()
    await asyncio.sleep(0.02)
    assert client.is_connected
    await client.disconnect()
    assert not client.is_connected
    await client.disconnect()  # second call is a no op


async def test_commands_are_serialized(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """Concurrent calls never interleave their commands."""
    client = make_client()
    await asyncio.gather(client.read_state(), client.set_timer(30), client.stop())
    commands = fakes[0].commands
    poll = ["status", "read unit", "read temp", "read set temp", "read timer"]
    start = commands.index("status")
    assert commands[start : start + 5] == poll
    assert sorted(commands) == sorted([*poll, "set timer 30", "stop"])


async def test_command_gap(mock_connect: AsyncMock) -> None:
    """Commands in one session are spaced."""
    client = make_client(command_gap=0.01)
    with patch(f"{MODULE}.asyncio.sleep", wraps=asyncio.sleep) as sleep:
        await client.read_state()
    assert sleep.await_count == 4


async def test_set_target_temperature(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """The unit is read fresh, then the value is sent in that unit."""
    client = make_client()
    assert await client.set_target_temperature(56.54) == 56.5
    assert fakes[0].commands == ["read unit", "set temp 56.5"]
    assert client.unit is TemperatureUnit.CELSIUS


async def test_set_target_temperature_converts_units(
    mock_connect: AsyncMock, replies: dict[str, Reply], fakes: list[FakeBleakClient]
) -> None:
    """A value in another unit is converted to the cooker unit."""
    client = make_client()
    await client.set_target_temperature(135, TemperatureUnit.FAHRENHEIT)
    assert fakes[0].commands[-1] == "set temp 57.2"
    replies["read unit"] = "f"
    await client.set_target_temperature(56.5, TemperatureUnit.CELSIUS)
    assert fakes[0].commands[-1] == "set temp 133.7"
    assert client.unit is TemperatureUnit.FAHRENHEIT
    await client.set_target_temperature(150)
    assert fakes[0].commands[-1] == "set temp 150.0"


async def test_set_target_temperature_out_of_range(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """Out of range values are rejected before sending and keep the link."""
    client = make_client()
    with pytest.raises(AnovaValueError):
        await client.set_target_temperature(150, TemperatureUnit.CELSIUS)
    assert fakes[0].commands == ["read unit"]
    assert client.is_connected


async def test_set_timer(mock_connect: AsyncMock, fakes: list[FakeBleakClient]) -> None:
    """Timer minutes are validated and echoed."""
    client = make_client()
    assert await client.set_timer(90) == 90
    with pytest.raises(AnovaValueError):
        await client.set_timer(6001)
    assert fakes[0].commands == ["set timer 90"]


async def test_set_and_read_unit(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """Unit changes are echoed by the cooker."""
    client = make_client()
    assert (
        await client.set_unit(TemperatureUnit.FAHRENHEIT) is TemperatureUnit.FAHRENHEIT
    )
    assert client.unit is TemperatureUnit.FAHRENHEIT
    assert await client.read_unit() is TemperatureUnit.CELSIUS
    assert fakes[0].commands == ["set unit f", "read unit"]


async def test_simple_commands(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """Start, stop and timer commands use the documented strings."""
    client = make_client()
    await client.start()
    await client.stop()
    await client.start_timer()
    await client.stop_timer()
    assert await client.read_timer() == TimerState(120, False)
    assert fakes[0].commands == [
        "start",
        "stop",
        "start time",
        "stop time",
        "read timer",
    ]


async def test_start_cook_with_timer(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """The official start sequence, with a timer."""
    await make_client().start_cook(56.5, 120, TemperatureUnit.CELSIUS)
    assert fakes[0].commands == [
        "read unit",
        "set temp 56.5",
        "stop time",
        "set timer 120",
        "start",
        "start time",
    ]


async def test_start_cook_without_timer(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """Without minutes the timer is cleared and not started."""
    await make_client().start_cook(60)
    assert fakes[0].commands == [
        "read unit",
        "set temp 60.0",
        "stop time",
        "set timer 0",
        "start",
    ]


async def test_start_cook_validates_first(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """Nothing is sent when a value is invalid."""
    client = make_client()
    with pytest.raises(AnovaValueError):
        await client.start_cook(56.5, 7000, TemperatureUnit.CELSIUS)
    assert mock_connect.call_count == 0
    with pytest.raises(AnovaValueError):
        await client.start_cook(120, 10, TemperatureUnit.CELSIUS)
    assert fakes[0].commands == ["read unit"]


async def test_device_info_wifi(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """The 900 W model answers with an f56 id and a version."""
    info = await make_client().read_device_info()
    assert info.has_wifi
    assert info.firmware_version == "1.4.4"
    assert info.model == "Precision Cooker (BT+WiFi, 900 W)"
    assert fakes[0].commands == ["get id card", "version"]


async def test_device_info_bluetooth_only(
    mock_connect: AsyncMock, replies: dict[str, Reply], fakes: list[FakeBleakClient]
) -> None:
    """The 800 W model rejects the id card command."""
    del replies["get id card"]
    info = await make_client().read_device_info()
    assert info.id_card is None
    assert not info.has_wifi
    assert info.firmware_version is None
    assert fakes[0].commands == ["get id card"]


async def test_device_info_no_answer(
    mock_connect: AsyncMock, replies: dict[str, Reply]
) -> None:
    """An ignored probe is not an error."""
    replies["get id card"] = None
    client = make_client()
    with patch(f"{MODULE}._PROBE_TIMEOUT", 0.05):
        info = await client.read_device_info()
    assert info.id_card is None
    # The link is reset so a late answer cannot be taken for the next reply.
    assert not client.is_connected
    assert (await client.read_state()).status is DeviceStatus.STOPPED


async def test_hanging_write_times_out(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """The command timeout also covers the write."""

    async def _hang(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(10)

    client = make_client(command_timeout=0.05)
    await client.connect()
    fakes[0].write_gatt_char = _hang  # type: ignore[method-assign]
    with pytest.raises(AnovaTimeoutError):
        await client.start()
    assert not client.is_connected


async def test_unsolicited_notification_ignored(
    mock_connect: AsyncMock, fakes: list[FakeBleakClient]
) -> None:
    """Data without a pending command is dropped."""
    client = make_client()
    await client.connect()
    fakes[0].notify(fakes[0].char, bytearray(b"user change parameter\r"))  # type: ignore[misc]
    fakes[0].notify(fakes[0].char, bytearray(b"half"))  # type: ignore[misc]
    await asyncio.sleep(0.02)
    assert await client.send_command("status") == "stopped"


async def test_extra_responses_in_one_chunk(
    mock_connect: AsyncMock, replies: dict[str, Reply]
) -> None:
    """Only the first response in a chunk answers the command."""
    replies["status"] = b"running\rstopped\r"
    assert await make_client().send_command("status") == "running"


async def test_set_ble_device(mock_connect: AsyncMock) -> None:
    """A newer BLEDevice is used for the next connection."""
    client = make_client()
    newer = BLEDevice(ADDRESS, None, details={"source": "proxy"})
    client.set_ble_device(newer)
    assert client.name == ADDRESS
    await client.connect()
    assert mock_connect.call_args.args[1] is newer


async def test_cancelled_command_disconnects(
    mock_connect: AsyncMock, replies: dict[str, Reply]
) -> None:
    """Cancelling mid command closes the link so no late answer confuses us."""
    replies["read temp"] = None
    client = make_client(command_timeout=5)
    task = asyncio.create_task(client.read_state())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.is_connected
