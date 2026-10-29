"""Tests for registering the dashboard card."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.setup import async_setup_component
import pytest

from custom_components.anova_ble.const import CARD_FILENAME, DOMAIN
from custom_components.anova_ble.frontend import (
    CARD_PATH,
    DATA_CARD_REGISTERED,
    async_register_card,
)

ADD_EXTRA_JS_URL = "custom_components.anova_ble.frontend.frontend.add_extra_js_url"
CARD_URL = f"/anova_ble/{CARD_FILENAME}"


@pytest.fixture
def mock_http(hass: HomeAssistant) -> MagicMock:
    """Pretend the web server is running."""
    http = MagicMock()
    http.async_register_static_paths = AsyncMock()
    hass.http = http
    return http


@pytest.fixture
def mock_add_extra_js_url() -> MagicMock:
    """Replace the frontend call that loads a module in the browser."""
    with patch(ADD_EXTRA_JS_URL) as mock:
        yield mock


def test_card_file_is_next_to_the_module() -> None:
    """The card is served from the frontend folder of the integration."""
    assert CARD_PATH.name == CARD_FILENAME
    assert CARD_PATH.parent.name == "frontend"
    assert CARD_PATH.is_file()


async def test_registers_card(
    hass: HomeAssistant, mock_http: MagicMock, mock_add_extra_js_url: MagicMock
) -> None:
    """The card is served as a static file and loaded with the version in the URL."""
    hass.config.components.add("frontend")
    integration = await async_get_integration(hass, DOMAIN)

    await async_register_card(hass)

    mock_http.async_register_static_paths.assert_awaited_once_with(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), True)]
    )
    mock_add_extra_js_url.assert_called_once_with(
        hass, f"{CARD_URL}?v={integration.version}"
    )
    assert integration.version != "0.0.0"
    assert hass.data[DATA_CARD_REGISTERED]


async def test_registers_card_once(
    hass: HomeAssistant, mock_http: MagicMock, mock_add_extra_js_url: MagicMock
) -> None:
    """A second call does nothing."""
    hass.config.components.add("frontend")

    await async_register_card(hass)
    await async_register_card(hass)

    mock_http.async_register_static_paths.assert_awaited_once()
    mock_add_extra_js_url.assert_called_once()


async def test_skips_without_frontend(
    hass: HomeAssistant, mock_http: MagicMock, mock_add_extra_js_url: MagicMock
) -> None:
    """Without the frontend package nothing is registered."""
    assert "frontend" not in hass.config.components

    await async_register_card(hass)

    mock_http.async_register_static_paths.assert_not_awaited()
    mock_add_extra_js_url.assert_not_called()
    assert DATA_CARD_REGISTERED not in hass.data


async def test_skips_without_http(
    hass: HomeAssistant, mock_add_extra_js_url: MagicMock
) -> None:
    """Without a web server nothing is registered."""
    hass.config.components.add("frontend")
    hass.http = None

    await async_register_card(hass)

    mock_add_extra_js_url.assert_not_called()
    assert DATA_CARD_REGISTERED not in hass.data


async def test_setup_registers_card(
    hass: HomeAssistant, mock_add_extra_js_url: MagicMock
) -> None:
    """Loading the integration registers the card, without a config entry."""
    hass.config.components.add("frontend")

    with patch(
        "homeassistant.components.http.server.HomeAssistantHTTP"
        ".async_register_static_paths",
        new_callable=AsyncMock,
    ) as register_static_paths:
        assert await async_setup_component(hass, DOMAIN, {})

    register_static_paths.assert_awaited_once()
    mock_add_extra_js_url.assert_called_once()
