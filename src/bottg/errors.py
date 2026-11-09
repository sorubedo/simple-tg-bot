"""Application-specific exceptions."""

from __future__ import annotations


class BottgError(Exception):
    """Base class for expected bottg failures."""


class ConfigError(BottgError):
    """The JSON5 configuration is missing or invalid."""


class StartupError(BottgError):
    """The bot cannot start with the supplied configuration or target group."""


class ActionError(BottgError):
    """Base class for action-related failures."""


class ActionPayloadError(ActionError):
    """An external program produced invalid action JSON."""


class ActionQueueError(ActionError):
    """An action batch could not be accepted by the action queue."""


class ProgramError(BottgError):
    """Base class for external program failures."""


class ProgramExecutionError(ProgramError):
    """An external program could not be run or did not complete normally."""
