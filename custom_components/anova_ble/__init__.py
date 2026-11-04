"""The Anova Precision Cooker (Bluetooth) integration."""

from __future__ import annotations

import logging

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import ATTR_TEMPERATURE, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
from homeassistant.helpers.typing import ConfigType
import probatio

from .const import (
    ATTR_DURATION,
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_PERSISTENT,
    DEFAULT_CONNECTION_MODE,
    DOMAIN,
    PER_POLL_DISCONNECT_DELAY,
    SERVICE_START_COOK,
    SERVICE_STOP_COOK,
)
from .coordinator import AnovaConfigEntry, AnovaCoordinator, AnovaData
from .frontend import async_register_card
from .protocol import AnovaClient
from .store import AnovaStore

__all__ = [
    "AnovaConfigEntry",
    "async_remove_entry",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
]

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the cook actions and the dashboard card."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_COOK,
        entity_domain=CLIMATE_DOMAIN,
        schema={
            probatio.Required(ATTR_TEMPERATURE): probatio.Coerce(float),
            probatio.Optional(ATTR_DURATION): cv.positive_time_period,
        },
        func="async_start_cook",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_STOP_COOK,
        entity_domain=CLIMATE_DOMAIN,
        schema=None,
        func="async_stop_cook",
    )
    try:
        await async_register_card(hass)
    except Exception:
        # The card is optional, the cooker must work without it.
        _LOGGER.exception("Could not register the Anova dashboard card")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AnovaConfigEntry) -> bool:
    """Set up a cooker from a config entry.

    Setup never waits for the cooker: it is switched off most of the time.
    The entities start with the last stored values and the first poll runs
    in the background.
    """
    address = entry.unique_id
    if address is None:
        raise ConfigEntryError(
            translation_domain=DOMAIN, translation_key="missing_address"
        )
    store = AnovaStore(hass, entry.entry_id)
    state, device_info = await store.async_load()

    # The coordinator hands the client a fresh BLEDevice before every poll.
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    ) or BLEDevice(address, entry.title, details=None)
    connection_mode = entry.options.get(CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE)
    client = AnovaClient(
        ble_device,
        disconnect_delay=(
            None
            if connection_mode == CONNECTION_MODE_PERSISTENT
            else PER_POLL_DISCONNECT_DELAY
        ),
    )
    coordinator = AnovaCoordinator(
        hass, entry, client, store=store, state=state, device_info=device_info
    )
    entry.runtime_data = AnovaData(client, coordinator)

    async def _async_close_link(_event: Event) -> None:
        """Release the cooker when Home Assistant stops."""
        await coordinator.async_shutdown()
        await client.close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_close_link)
    )
    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            coordinator.async_handle_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), "anova_ble first poll"
    )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: AnovaConfigEntry) -> None:
    """Reload the entry when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AnovaConfigEntry) -> bool:
    """Unload the platforms and close the Bluetooth link.

    Polling stops first, so no poll can reopen the link after it was closed.
    The client is closed for good, which also aborts a slow connect attempt.
    """
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.coordinator.async_shutdown()
        await entry.runtime_data.client.close()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: AnovaConfigEntry) -> None:
    """Delete the stored last values of a removed cooker."""
    await AnovaStore(hass, entry.entry_id).async_remove()
