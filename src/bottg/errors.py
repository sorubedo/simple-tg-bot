"""Application-specific exceptions."""

from __future__ import annotations


class BottgError(Exception):
    """Base class for expected bottg failures."""


class ConfigError(BottgError):
    """The JSON5 configuration is missing or invalid."""


class StartupError(BottgError):
    """The bot cannot start with the supplied configuration or target group."""
