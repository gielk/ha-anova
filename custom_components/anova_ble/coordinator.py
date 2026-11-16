"""Data update coordinator for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_CONNECTION_MODE,
    CONF_POLL_INTERVAL,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    FAILURES_BEFORE_OFFLINE,
    MAX_BACKOFF_INTERVAL,
    MAX_BACKOFF_INTERVAL_VISIBLE,
    REFRESH_COOLDOWN,
)
from .protocol import (
    AnovaClient,
    AnovaDeviceInfo,
    AnovaError,
    AnovaState,
    AnovaValueError,
)
from .store import AnovaStore

_LOGGER = logging.getLogger(__name__)

type AnovaConfigEntry = ConfigEntry[AnovaData]


@dataclass
class AnovaData:
    """Everything a loaded config entry needs, stored in ``runtime_data``."""

    client: AnovaClient
    coordinator: AnovaCoordinator


class AnovaCoordinator(DataUpdateCoordinator[AnovaState | None]):
    """Poll the cooker and keep the last values while it is offline.

    The cooker is switched off most of the time, so being unreachable is
    normal and never an error. A failed poll returns the last data (frozen,
    also restored from storage after a restart) and, once the cooker counts
    as offline, ``online`` turns False. Entities read ``online`` to show the
    status and to disable their controls. While polls fail the interval grows
    up to ``MAX_BACKOFF_INTERVAL``; an advertisement from the cooker brings
    the next poll forward. While Bluetooth still sees the cooker the interval
    only grows up to ``MAX_BACKOFF_INTERVAL_VISIBLE``.
    """

    config_entry: AnovaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AnovaConfigEntry,
        client: AnovaClient,
        *,
        store: AnovaStore,
        state: AnovaState | None,
        device_info: AnovaDeviceInfo | None,
    ) -> None:
        """Initialize the coordinator with the last known (stored) values."""
        if entry.unique_id is None:
            raise ValueError("The Anova config entry has no unique id")
        self.address: str = entry.unique_id
        self.client = client
        self.device_info = device_info
        self.connection_mode: str = entry.options.get(
            CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE
        )
        self.poll_interval = timedelta(
            seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
        self._store = store
        self._failures = 0
        # None until the first poll, so the first result is not logged as a change.
        self._online: bool | None = None
        # True while polls fail because Bluetooth cannot see the cooker.
        self._waiting_for_cooker = False
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.address}",
            update_interval=self.poll_interval,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.data = state

    @property
    def online(self) -> bool:
        """Return True while the cooker answers."""
        return bool(self._online)

    @property
    def failures(self) -> int:
        """Return the number of failed polls in a row."""
        return self._failures

    @callback
    def _async_use_current_ble_device(self) -> bool:
        """Hand the freshest BLEDevice to the client, False when out of range."""
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            return False
        self.client.set_ble_device(ble_device)
        return True

    @callback
    def async_handle_advertisement(
        self,
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Poll at once when a cooker that was out of range is seen again.

        Without this the backoff could keep the cooker offline for up to
        ``MAX_BACKOFF_INTERVAL`` after it is switched back on. Only the first
        advertisement after a dropout triggers a poll.
        """
        if not self._waiting_for_cooker:
            return
        self._waiting_for_cooker = False
        _LOGGER.debug("Cooker seen again, polling now")
        self.update_interval = self.poll_interval
        self.config_entry.async_create_background_task(
            self.hass,
            self.async_request_refresh(),
            "anova_ble poll after advertisement",
        )

    async def _async_update_data(self) -> AnovaState | None:
        """Read the cooker, or keep the last data while it is unreachable."""
        if not self._async_use_current_ble_device():
            # Not even advertising: switched off or far away.
            self._waiting_for_cooker = True
            return self._async_poll_failed("out of Bluetooth range", offline=True)
        try:
            state = await self.client.read_state()
        except AnovaError as err:
            # Bluetooth sees the cooker, so keep trying fairly often.
            return self._async_poll_failed(
                str(err),
                offline=self._failures + 1 >= FAILURES_BEFORE_OFFLINE,
                max_backoff=MAX_BACKOFF_INTERVAL_VISIBLE,
            )
        if self._failures:
            _LOGGER.debug("Poll succeeded after %s failed polls", self._failures)
        self._failures = 0
        self._waiting_for_cooker = False
        self.update_interval = self.poll_interval
        self._async_set_online(True)
        if self.device_info is None:
            await self._async_learn_device_info()
        self._store.async_schedule_save(state, self.device_info)
        return state

    @callback
    def _async_poll_failed(
        self, reason: str, *, offline: bool, max_backoff: int = MAX_BACKOFF_INTERVAL
    ) -> AnovaState | None:
        """Count a failed poll, back off, and return the last (frozen) data.

        Failed polls are normal for a cooker that is switched off, so they
        are only logged at debug level. Going offline and coming back are
        logged once each at info level.
        """
        self._failures += 1
        backoff = min(
            # Cap the exponent: a cooker unplugged for days must not overflow.
            self.poll_interval.total_seconds() * 2 ** min(self._failures, 10),
            # Never below the poll interval the user chose.
            max(max_backoff, self.poll_interval.total_seconds()),
        )
        self.update_interval = timedelta(seconds=backoff)
        _LOGGER.debug(
            "Poll failed (%s), %s in a row, next try in %s s",
            reason,
            self._failures,
            backoff,
        )
        if offline:
            self._async_set_online(False)
        return self.data

    @callback
    def _async_set_online(self, online: bool) -> None:
        if online == self._online:
            return
        if online and self._online is False:
            _LOGGER.info("%s is back online", self.config_entry.title)
        elif not online:
            _LOGGER.info(
                "%s is offline, keeping the last known values",
                self.config_entry.title,
            )
        self._online = online

    async def _async_learn_device_info(self) -> None:
        """Read the model once and put it on the device. Best effort."""
        try:
            info = await self.client.read_device_info()
        except AnovaError as err:
            _LOGGER.debug("Could not read the device info yet: %s", err)
            return
        self.device_info = info
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_connection(
            (dr.CONNECTION_BLUETOOTH, self.address), self.config_entry.entry_id
        )
        if device is not None:
            registry.async_update_device(
                device.id, model=info.model, sw_version=info.firmware_version
            )

    async def async_execute[T](self, command: Callable[[], Awaitable[T]]) -> T:
        """Run a client command for an entity action.

        A cooker that Bluetooth cannot see fails at once with a clear message.
        Out of range values become ``ServiceValidationError`` and every other
        client error becomes ``HomeAssistantError``, both translated. After a
        successful command a refresh is requested so the entities catch up.
        """
        if not self._async_use_current_ble_device():
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cooker_offline"
            )
        try:
            result = await command()
        except AnovaValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_value",
                translation_placeholders={"error": str(err)},
            ) from err
        except AnovaError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._async_set_online(True)
        await self.async_request_refresh()
        return result
