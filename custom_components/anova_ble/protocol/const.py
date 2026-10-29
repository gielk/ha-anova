"""Constants for the original Anova Precision Cooker BLE protocol."""

from __future__ import annotations

from typing import Final

SERVICE_UUID: Final = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID: Final = "0000ffe1-0000-1000-8000-00805f9b34fb"
LOCAL_NAME: Final = "Anova"

# Writes and notifications are limited to 20 bytes (default ATT MTU of 23).
MAX_CHUNK_SIZE: Final = 20

# The official Anova CLI waits up to 15 s for a response.
DEFAULT_COMMAND_TIMEOUT: Final = 15.0
# Wait this long for more data after a short chunk without a terminator.
RESPONSE_GRACE_PERIOD: Final = 0.3
# Small pause between two commands, the official docs ask to space commands.
DEFAULT_COMMAND_GAP: Final = 0.1
# Close an idle connection after this many seconds (None keeps it open).
DEFAULT_DISCONNECT_DELAY: Final = 2.0
DEFAULT_CONNECT_ATTEMPTS: Final = 3

TEMP_MIN_C: Final = 5.0
TEMP_MAX_C: Final = 99.9
TEMP_MIN_F: Final = 41.0
TEMP_MAX_F: Final = 211.8
TIMER_MIN_MINUTES: Final = 0
TIMER_MAX_MINUTES: Final = 6000

WIFI_ID_PREFIX: Final = "anova f56-"
RESPONSE_INVALID_COMMAND: Final = "invalid command"

CMD_STATUS: Final = "status"
CMD_START: Final = "start"
CMD_STOP: Final = "stop"
CMD_READ_TEMP: Final = "read temp"
CMD_READ_TARGET_TEMP: Final = "read set temp"
CMD_READ_UNIT: Final = "read unit"
CMD_READ_TIMER: Final = "read timer"
CMD_START_TIMER: Final = "start time"
CMD_STOP_TIMER: Final = "stop time"
CMD_GET_ID_CARD: Final = "get id card"
CMD_VERSION: Final = "version"
CMD_CLEAR_ALARM: Final = "clear alarm"
