"""Data update coordinator for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONNECTION_MODE,
    CONF_POLL_INTERVAL,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    FAILURES_BEFORE_UNAVAILABLE,
    MAX_BACKOFF_INTERVAL,
    REFRESH_COOLDOWN,
)
from .protocol import (
    AnovaClient,
    AnovaDeviceInfo,
    AnovaError,
    AnovaState,
    AnovaValueError,
)

_LOGGER = logging.getLogger(__name__)

type AnovaConfigEntry = ConfigEntry[AnovaData]


@dataclass
class AnovaData:
    """Everything a loaded config entry needs, stored in ``runtime_data``."""

    client: AnovaClient
    coordinator: AnovaCoordinator
    device_info: AnovaDeviceInfo


class AnovaCoordinator(DataUpdateCoordinator[AnovaState]):
    """Poll the cooker and keep the last data through short dropouts.

    A failed poll keeps the previous data. After ``FAILURES_BEFORE_UNAVAILABLE``
    failed polls in a row, or when there is no data yet, the update fails and
    the entities become unavailable. While polls fail the interval grows
    exponentially up to ``MAX_BACKOFF_INTERVAL``.
    """

    config_entry: AnovaConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: AnovaConfigEntry, client: AnovaClient
    ) -> None:
        """Initialize the coordinator."""
        if entry.unique_id is None:
            raise ValueError("The Anova config entry has no unique id")
        self.address: str = entry.unique_id
        self.client = client
        self.connection_mode: str = entry.options.get(
            CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE
        )
        self.poll_interval = timedelta(
            seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
        self._failures = 0
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

    async def _async_update_data(self) -> AnovaState:
        """Read the cooker, or keep the last data during a short dropout."""
        if not self._async_use_current_ble_device():
            return self._async_handle_failure(
                "device_out_of_range",
                {"address": self.address},
                "out of Bluetooth range",
            )
        try:
            state = await self.client.read_state()
        except AnovaError as err:
            return self._async_handle_failure(
                "update_failed", {"error": str(err)}, str(err)
            )
        if self._failures:
            _LOGGER.debug("Poll succeeded after %s failed polls", self._failures)
        self._failures = 0
        self.update_interval = self.poll_interval
        return state

    def _async_handle_failure(
        self, translation_key: str, placeholders: dict[str, str], reason: str
    ) -> AnovaState:
        """Count a failed poll and return the last data or raise.

        Transient failures are only logged at debug level. Home Assistant logs
        the first ``UpdateFailed`` and the recovery once each.
        """
        self._failures += 1
        backoff = min(
            # Cap the exponent: a cooker unplugged for days must not overflow.
            self.poll_interval.total_seconds() * 2 ** min(self._failures, 10),
            MAX_BACKOFF_INTERVAL,
        )
        self.update_interval = timedelta(seconds=backoff)
        if self.data is not None and self._failures < FAILURES_BEFORE_UNAVAILABLE:
            _LOGGER.debug(
                "Poll %s of %s failed (%s), keeping the last data, retry in %s s",
                self._failures,
                FAILURES_BEFORE_UNAVAILABLE,
                reason,
                backoff,
            )
            return self.data
        _LOGGER.debug(
            "Poll failed (%s), %s in a row, retry in %s s",
            reason,
            self._failures,
            backoff,
        )
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )

    async def async_execute[T](self, command: Awaitable[T]) -> T:
        """Run a client command for an entity action.

        Out of range values become ``ServiceValidationError`` and every other
        client error becomes ``HomeAssistantError``, both translated. After a
        successful command a refresh is requested so the entities catch up.
        """
        # Best effort: without a device the client tries the one it knows.
        self._async_use_current_ble_device()
        try:
            result = await command
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
        await self.async_request_refresh()
        return result
