"""Constants for the Anova Precision Cooker (Bluetooth) integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "anova_ble"
MANUFACTURER: Final = "Anova Culinary"

CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_CONNECTION_MODE: Final = "connection_mode"

CONNECTION_MODE_PER_POLL: Final = "per_poll"
CONNECTION_MODE_PERSISTENT: Final = "persistent"
CONNECTION_MODES: Final = [CONNECTION_MODE_PER_POLL, CONNECTION_MODE_PERSISTENT]

DEFAULT_POLL_INTERVAL: Final = 15
MIN_POLL_INTERVAL: Final = 5
MAX_POLL_INTERVAL: Final = 300
DEFAULT_CONNECTION_MODE: Final = CONNECTION_MODE_PER_POLL

# Polls fail often on a weak link. Keep the last data until this many
# consecutive polls failed, then mark the entities unavailable.
FAILURES_BEFORE_UNAVAILABLE: Final = 3
# Poll interval backoff cap while the cooker cannot be reached.
MAX_BACKOFF_INTERVAL: Final = 300
# In per poll mode the link closes this many seconds after the last command.
PER_POLL_DISCONNECT_DELAY: Final = 2.0
# Minimum time between two refreshes requested after an action.
REFRESH_COOLDOWN: Final = 2.0

SERVICE_START_COOK: Final = "start_cook"
SERVICE_STOP_COOK: Final = "stop_cook"
ATTR_DURATION: Final = "duration"

CARD_FILENAME: Final = "anova-cooker-card.js"
CARD_URL_BASE: Final = f"/{DOMAIN}"
