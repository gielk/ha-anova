"""Async BLE client for the original Anova Precision Cooker.

This module has no Home Assistant imports. It only needs ``bleak`` and
``bleak-retry-connector``, so it can move to its own PyPI package later.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import contextlib
from contextlib import asynccontextmanager
import logging

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)

from .const import (
    CHARACTERISTIC_UUID,
    CMD_GET_ID_CARD,
    CMD_READ_TARGET_TEMP,
    CMD_READ_TEMP,
    CMD_READ_TIMER,
    CMD_READ_UNIT,
    CMD_START,
    CMD_START_TIMER,
    CMD_STATUS,
    CMD_STOP,
    CMD_STOP_TIMER,
    CMD_VERSION,
    DEFAULT_COMMAND_GAP,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_ATTEMPTS,
    DEFAULT_DISCONNECT_DELAY,
    FLUSH_SETTLE,
    FLUSH_TIMEOUT,
    RESPONSE_GRACE_PERIOD,
)
from .exceptions import (
    AnovaCommandError,
    AnovaConnectionError,
    AnovaError,
    AnovaTimeoutError,
    AnovaValueError,
)
from .models import AnovaDeviceInfo, AnovaState, TemperatureUnit, TimerState
from .parser import (
    ResponseAssembler,
    check_response,
    cmd_set_temperature,
    cmd_set_timer,
    cmd_set_unit,
    convert_temperature,
    encode_command,
    is_rejected,
    parse_float,
    parse_int,
    parse_status,
    parse_timer,
    parse_unit,
    validate_timer,
)

_LOGGER = logging.getLogger(__name__)

# Shorter timeout for optional commands that some models do not answer.
_PROBE_TIMEOUT = 5.0


class AnovaClient:
    """Talk to one cooker over BLE.

    * One command at a time, guarded by a lock, each with a timeout.
    * Connects on demand. With ``disconnect_delay`` set, the link is closed
      after that many idle seconds so other apps can connect in between.
      With ``disconnect_delay=None`` the link is kept open.
    * Any transport problem closes the link, so the next call starts clean.
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        *,
        disconnect_delay: float | None = DEFAULT_DISCONNECT_DELAY,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
        command_gap: float = DEFAULT_COMMAND_GAP,
        connect_attempts: int = DEFAULT_CONNECT_ATTEMPTS,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the client."""
        self._ble_device = ble_device
        self._disconnect_delay = disconnect_delay
        self._command_timeout = command_timeout
        self._command_gap = command_gap
        self._connect_attempts = connect_attempts
        self._on_disconnect = on_disconnect

        self._lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._char: BleakGATTCharacteristic | None = None
        self._assembler = ResponseAssembler()
        self._response: asyncio.Future[str] | None = None
        self._grace_handle: asyncio.TimerHandle | None = None
        self._disconnect_handle: asyncio.TimerHandle | None = None
        self._disconnect_task: asyncio.Task[None] | None = None
        self._connect_task: asyncio.Task[BleakClientWithServiceCache] | None = None
        self._closed = False
        self._commands_sent = 0
        self._reset_after_session = False
        self._unit: TemperatureUnit | None = None

    @property
    def address(self) -> str:
        """Return the BLE address."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Return the advertised name, or the address."""
        return self._ble_device.name or self._ble_device.address

    @property
    def is_connected(self) -> bool:
        """Return True while a BLE link is open."""
        return self._client is not None and self._client.is_connected

    @property
    def unit(self) -> TemperatureUnit | None:
        """Return the last unit reported by the cooker."""
        return self._unit

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Use a newer BLEDevice, for example from a closer adapter or proxy."""
        self._ble_device = ble_device

    # Public API -----------------------------------------------------------

    async def connect(self) -> None:
        """Open the link now (normally this happens on demand)."""
        async with self._session():
            pass

    async def close(self) -> None:
        """Close the link for good, aborting a connect or command in progress.

        Use this when the owner goes away (unload, shutdown). Every later call
        raises ``AnovaConnectionError``, so nothing can reopen the link.
        """
        self._closed = True
        self._cancel_disconnect_timer()
        self._fail_pending(AnovaConnectionError(f"{self.name}: client closed"))
        if self._connect_task is not None:
            self._connect_task.cancel()
        async with self._lock:
            await self._disconnect_locked()

    async def disconnect(self) -> None:
        """Close the link and cancel any pending idle disconnect."""
        self._cancel_disconnect_timer()
        async with self._lock:
            await self._disconnect_locked()

    async def send_command(self, command: str) -> str:
        """Send one raw command and return the raw response."""
        async with self._session():
            return await self._send(command)

    async def read_state(self) -> AnovaState:
        """Read status, unit, temperatures and timer in one session."""
        async with self._session():
            raw_status = await self._send(CMD_STATUS)
            unit = parse_unit(await self._send(CMD_READ_UNIT))
            current = parse_float(await self._send(CMD_READ_TEMP))
            target = parse_float(await self._send(CMD_READ_TARGET_TEMP))
            timer = parse_timer(await self._send(CMD_READ_TIMER))
        self._unit = unit
        return AnovaState(
            status=parse_status(raw_status),
            raw_status=raw_status,
            unit=unit,
            current_temperature=current,
            target_temperature=target,
            timer=timer,
        )

    async def read_device_info(self) -> AnovaDeviceInfo:
        """Best effort model detection with ``get id card`` and ``version``."""
        async with self._session():
            id_card = await self._probe(CMD_GET_ID_CARD)
            info = AnovaDeviceInfo(id_card=id_card, firmware_version=None)
            if info.has_wifi:
                info = AnovaDeviceInfo(
                    id_card=id_card, firmware_version=await self._probe(CMD_VERSION)
                )
        return info

    async def read_unit(self) -> TemperatureUnit:
        """Read the temperature unit."""
        async with self._session():
            self._unit = parse_unit(await self._send(CMD_READ_UNIT))
        return self._unit

    async def read_timer(self) -> TimerState:
        """Read the timer."""
        async with self._session():
            return parse_timer(await self._send(CMD_READ_TIMER))

    async def set_unit(self, unit: TemperatureUnit) -> TemperatureUnit:
        """Change the display and protocol unit of the cooker."""
        command = cmd_set_unit(unit)
        async with self._session():
            self._unit = parse_unit(await self._send(command))
        return self._unit

    async def set_target_temperature(
        self, temperature: float, unit: TemperatureUnit | None = None
    ) -> float:
        """Set the target temperature and return the value the cooker echoed.

        ``temperature`` is in ``unit`` (default: the cooker unit). The cooker
        unit is read first and the value converted, so a unit changed on the
        cooker itself can never make it heat to the wrong temperature.
        """
        async with self._session():
            command = await self._set_temperature_command(temperature, unit)
            return parse_float(await self._send(command))

    async def set_timer(self, minutes: int) -> int:
        """Set the timer in whole minutes (0 to 6000)."""
        command = cmd_set_timer(minutes)
        async with self._session():
            return parse_int(await self._send(command))

    async def start(self) -> None:
        """Start heating."""
        await self.send_command(CMD_START)

    async def stop(self) -> None:
        """Stop heating. The cooker also stops the timer."""
        await self.send_command(CMD_STOP)

    async def start_timer(self) -> None:
        """Start the countdown. The cooker must be running."""
        await self.send_command(CMD_START_TIMER)

    async def stop_timer(self) -> None:
        """Stop the countdown."""
        await self.send_command(CMD_STOP_TIMER)

    async def start_cook(
        self,
        temperature: float,
        minutes: int | None = None,
        unit: TemperatureUnit | None = None,
    ) -> None:
        """Set temperature and timer and start, using the official sequence.

        ``temperature`` is in ``unit`` (default: the cooker unit), see
        :meth:`set_target_temperature`.
        """
        minutes = validate_timer(minutes or 0)
        async with self._session():
            await self._send(await self._set_temperature_command(temperature, unit))
            await self._send(CMD_STOP_TIMER)
            await self._send(cmd_set_timer(minutes))
            await self._send(CMD_START)
            if minutes > 0:
                await self._send(CMD_START_TIMER)

    # Connection handling --------------------------------------------------

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[None]:
        """Hold the lock, make sure the link is up and handle errors."""
        self._cancel_disconnect_timer()
        async with self._lock:
            self._commands_sent = 0
            try:
                self._raise_if_closed()
                await self._ensure_connected()
                yield
            except AnovaCommandError, AnovaValueError:
                # The cooker answered or nothing was sent, the link is fine.
                raise
            except AnovaError:
                await self._disconnect_locked()
                raise
            except BLEAK_RETRY_EXCEPTIONS as err:
                await self._disconnect_locked()
                raise AnovaConnectionError(
                    f"{self.name}: BLE error: {type(err).__name__}: {err}"
                ) from err
            except asyncio.CancelledError, Exception:
                # A response may still arrive for an aborted command.
                await self._disconnect_locked()
                raise
            else:
                if self._reset_after_session:
                    await self._disconnect_locked()
            finally:
                self._reset_after_session = False
                self._schedule_disconnect()

    async def _ensure_connected(self) -> None:
        if self.is_connected:
            return
        self._client = None
        _LOGGER.debug("%s: connecting", self.name)
        # A separate task, so close() can abort a slow connect at once.
        self._connect_task = asyncio.get_running_loop().create_task(
            establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.name,
                disconnected_callback=self._handle_disconnected,
                max_attempts=self._connect_attempts,
                ble_device_callback=lambda: self._ble_device,
            )
        )
        try:
            client = await self._connect_task
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if self._closed and task is not None and not task.cancelling():
                raise AnovaConnectionError(f"{self.name}: client closed") from None
            raise
        finally:
            self._connect_task = None
        char = client.services.get_characteristic(CHARACTERISTIC_UUID)
        if char is None:
            await client.clear_cache()
            await client.disconnect()
            raise AnovaConnectionError(
                f"{self.name}: characteristic {CHARACTERISTIC_UUID} not found"
            )
        try:
            await client.start_notify(char, self._handle_notification)
        except BLEAK_RETRY_EXCEPTIONS:
            await client.disconnect()
            raise
        self._client = client
        self._char = char
        self._assembler.reset()
        _LOGGER.debug("%s: connected", self.name)
        await self._flush_line()

    async def _disconnect_locked(self) -> None:
        client, self._client = self._client, None
        self._char = None
        self._fail_pending(AnovaConnectionError(f"{self.name}: disconnected"))
        if client is None:
            return
        _LOGGER.debug("%s: disconnecting", self.name)
        try:
            await client.disconnect()
        except BLEAK_RETRY_EXCEPTIONS as err:
            _LOGGER.debug("%s: error while disconnecting: %s", self.name, err)

    def _handle_disconnected(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return
        _LOGGER.debug("%s: link dropped", self.name)
        self._client = None
        self._char = None
        self._fail_pending(AnovaConnectionError(f"{self.name}: link dropped"))
        if self._on_disconnect is not None:
            self._on_disconnect()

    def _schedule_disconnect(self) -> None:
        self._cancel_disconnect_timer()
        if self._disconnect_delay is None or not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        self._disconnect_handle = loop.call_later(
            self._disconnect_delay, self._start_idle_disconnect
        )

    def _cancel_disconnect_timer(self) -> None:
        if self._disconnect_handle is not None:
            self._disconnect_handle.cancel()
            self._disconnect_handle = None

    def _start_idle_disconnect(self) -> None:
        self._disconnect_handle = None
        if self._lock.locked():
            return  # A new session is running and will reschedule.
        self._disconnect_task = asyncio.get_running_loop().create_task(
            self._idle_disconnect()
        )

    async def _idle_disconnect(self) -> None:
        try:
            async with self._lock:
                if self._disconnect_handle is None:
                    await self._disconnect_locked()
        except Exception as err:  # Nobody awaits this task, never let it fail.
            _LOGGER.debug("%s: idle disconnect failed: %s", self.name, err)

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise AnovaConnectionError(f"{self.name}: client closed")

    # Command handling -----------------------------------------------------

    async def _set_temperature_command(
        self, temperature: float, unit: TemperatureUnit | None
    ) -> str:
        """Read the cooker unit and build ``set temp`` in that unit."""
        if unit is not None:
            unit = TemperatureUnit(unit)
        self._unit = parse_unit(await self._send(CMD_READ_UNIT))
        value = convert_temperature(temperature, unit or self._unit, self._unit)
        return cmd_set_temperature(value, self._unit)

    async def _probe(self, command: str) -> str | None:
        """Send an optional command. None when it is rejected or ignored."""
        try:
            return await self._send(command, response_timeout=_PROBE_TIMEOUT)
        except AnovaCommandError:
            return None
        except AnovaTimeoutError:
            _LOGGER.debug("%s: no answer to optional %r", self.name, command)
            # A late answer could be taken for the next reply: start clean.
            self._reset_after_session = True
            return None

    async def _send(self, command: str, response_timeout: float | None = None) -> str:
        """Send a command and return its answer, retrying once when rejected.

        Right after connecting the cooker can still hold part of a line, so
        a valid command arrives garbled and is answered with "Invalid
        Command". Clearing the line and sending it again fixes that. Every
        command used here is safe to repeat.
        """
        response = await self._exchange(command, response_timeout)
        if is_rejected(response):
            _LOGGER.debug(
                "%s: %r rejected, clearing the line and retrying", self.name, command
            )
            await self._flush_line()
            response = await self._exchange(command, response_timeout)
        return check_response(response)

    async def _flush_line(self) -> None:
        """Send an empty command and drop whatever comes back."""
        # No answer is fine: nothing was pending, or the answer got lost.
        with contextlib.suppress(AnovaTimeoutError):
            await self._exchange("", FLUSH_TIMEOUT)
        await asyncio.sleep(FLUSH_SETTLE)
        self._assembler.reset()

    async def _exchange(self, command: str, response_timeout: float | None) -> str:
        """Write one command and wait for one raw answer."""
        self._raise_if_closed()
        client, char = self._client, self._char
        if client is None or char is None:
            raise AnovaConnectionError(f"{self.name}: not connected")
        if self._commands_sent and self._command_gap:
            await asyncio.sleep(self._command_gap)
        self._commands_sent += 1

        self._assembler.reset()
        self._response = response_future = asyncio.get_running_loop().create_future()
        try:
            async with asyncio.timeout(response_timeout or self._command_timeout):
                for chunk in encode_command(command):
                    await client.write_gatt_char(char, chunk, response=False)
                response = await self._response
        except TimeoutError as err:
            raise AnovaTimeoutError(f"{self.name}: no answer to {command!r}") from err
        finally:
            self._response = None
            # The link can drop during a write: the write then raises and the
            # future holds an error nobody awaited. Retrieve it to avoid an
            # "exception was never retrieved" error in the log.
            if response_future.done() and not response_future.cancelled():
                response_future.exception()
            self._cancel_grace()
        _LOGGER.debug("%s: %r -> %r", self.name, command, response)
        return response

    def _handle_notification(
        self, _char: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        responses = self._assembler.feed(bytes(data))
        if responses:
            self._cancel_grace()
            self._resolve(responses[0])
            if len(responses) > 1:
                _LOGGER.debug("%s: ignoring extra data %r", self.name, responses[1:])
            return
        if self._assembler.has_partial and not self._assembler.awaiting_more:
            # Short chunk without terminator: official rule says it is the
            # end. Wait briefly in case a terminator follows.
            self._cancel_grace()
            self._grace_handle = asyncio.get_running_loop().call_later(
                RESPONSE_GRACE_PERIOD, self._flush_partial
            )

    def _flush_partial(self) -> None:
        self._grace_handle = None
        if (text := self._assembler.flush()) is not None:
            self._resolve(text)

    def _resolve(self, text: str) -> None:
        if self._response is not None and not self._response.done():
            self._response.set_result(text)
        else:
            _LOGGER.debug("%s: unsolicited response %r", self.name, text)

    def _fail_pending(self, err: Exception) -> None:
        if self._response is not None and not self._response.done():
            self._response.set_exception(err)

    def _cancel_grace(self) -> None:
        if self._grace_handle is not None:
            self._grace_handle.cancel()
            self._grace_handle = None
