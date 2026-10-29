"""Exceptions raised by the Anova BLE client."""

from __future__ import annotations


class AnovaError(Exception):
    """Base class for all Anova errors."""


class AnovaConnectionError(AnovaError):
    """The cooker could not be reached or the link dropped."""


class AnovaTimeoutError(AnovaConnectionError):
    """The cooker did not answer a command in time."""


class AnovaResponseError(AnovaError):
    """The cooker sent a response that could not be parsed."""


class AnovaCommandError(AnovaError):
    """The cooker rejected a command (``Invalid Command``)."""


class AnovaValueError(AnovaError, ValueError):
    """A value is outside the range the cooker accepts."""
