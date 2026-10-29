"""Register the dashboard card with the Home Assistant frontend."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from ..const import CARD_FILENAME, CARD_URL_BASE, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_CARD_REGISTERED: Final = f"{DOMAIN}_card_registered"
CARD_PATH: Final = Path(__file__).parent / CARD_FILENAME


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and load it in the frontend, once per Home Assistant run.

    The card URL carries the integration version, so browsers fetch the new
    file after an update. Nothing happens when the frontend or the web server
    is not running, for example in tests.
    """
    if hass.data.get(DATA_CARD_REGISTERED):
        return
    if "frontend" not in hass.config.components or hass.http is None:
        _LOGGER.debug("Frontend or web server not available, not loading the card")
        return
    hass.data[DATA_CARD_REGISTERED] = True

    integration = await async_get_integration(hass, DOMAIN)
    url = f"{CARD_URL_BASE}/{CARD_FILENAME}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(url, str(CARD_PATH), True)]
    )
    frontend.add_extra_js_url(hass, f"{url}?v={integration.version}")
    _LOGGER.debug("Registered dashboard card %s", url)
