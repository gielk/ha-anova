"""Config flow for the Anova Precision Cooker (Bluetooth)."""

from __future__ import annotations

import logging
from typing import Any, override

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import probatio

from .const import (
    CONF_CONNECTION_MODE,
    CONF_POLL_INTERVAL,
    CONNECTION_MODES,
    DEFAULT_CONNECTION_MODE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)
from .protocol import LOCAL_NAME, SERVICE_UUID, AnovaClient, AnovaError

_LOGGER = logging.getLogger(__name__)


def _is_anova(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True for an Anova cooker.

    Other devices, for example some scales, advertise the same ``ffe0`` service,
    so the local name has to match as well.
    """
    return (
        service_info.name.startswith(LOCAL_NAME)
        and SERVICE_UUID in service_info.service_uuids
    )


def _title(service_info: BluetoothServiceInfoBleak) -> str:
    """Return a name that tells two cookers apart, for example ``Anova (2D60)``."""
    short_address = service_info.address.replace(":", "")[-4:].upper()
    return f"{service_info.name} ({short_address})"


class AnovaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for an Anova cooker."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> AnovaOptionsFlow:
        """Return the options flow."""
        return AnovaOptionsFlow()

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a cooker found by Bluetooth discovery."""
        if not _is_anova(discovery_info):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for confirmation, then test the connection."""
        discovery_info = self._discovery_info
        if discovery_info is None:
            return self.async_abort(reason="not_supported")
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._async_test_connection(discovery_info)) is None:
                return self._async_create_entry(discovery_info)
            errors["base"] = error
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": _title(discovery_info)},
            errors=errors,
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick one of the cookers Bluetooth has seen."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            if (error := await self._async_test_connection(discovery_info)) is None:
                return self._async_create_entry(discovery_info)
            errors["base"] = error

        current_ids = self._async_current_ids(include_ignore=False)
        for service_info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            address = service_info.address.upper()
            if (
                address in current_ids
                or address in self._discovered_devices
                or not _is_anova(service_info)
            ):
                continue
            self._discovered_devices[address] = service_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ADDRESS): probatio.In(
                        {
                            address: f"{_title(service_info)} - {address}"
                            for address, service_info in (
                                self._discovered_devices.items()
                            )
                        }
                    )
                }
            ),
            errors=errors,
        )

    async def _async_test_connection(
        self, service_info: BluetoothServiceInfoBleak
    ) -> str | None:
        """Read the cooker once. Return an error key, or None when it works."""
        client = AnovaClient(service_info.device, disconnect_delay=None)
        try:
            await client.read_state()
        except AnovaError as err:
            _LOGGER.debug("Cannot connect to %s: %s", service_info.address, err)
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while connecting to the cooker")
            return "unknown"
        finally:
            await client.disconnect()
        return None

    def _async_create_entry(
        self, service_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Create the config entry for a cooker that answered."""
        return self.async_create_entry(
            title=_title(service_info),
            data={CONF_ADDRESS: service_info.address.upper()},
        )


class AnovaOptionsFlow(OptionsFlow):
    """Handle the options of a cooker."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the poll interval and the connection mode."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL]),
                    CONF_CONNECTION_MODE: user_input[CONF_CONNECTION_MODE],
                }
            )
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_POLL_INTERVAL,
                            max=MAX_POLL_INTERVAL,
                            step=1,
                            unit_of_measurement=UnitOfTime.SECONDS,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    probatio.Required(
                        CONF_CONNECTION_MODE,
                        default=options.get(
                            CONF_CONNECTION_MODE, DEFAULT_CONNECTION_MODE
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=CONNECTION_MODES,
                            mode=SelectSelectorMode.LIST,
                            translation_key=CONF_CONNECTION_MODE,
                        )
                    ),
                }
            ),
        )
