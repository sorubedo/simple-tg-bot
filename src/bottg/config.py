"""JSON5 configuration loading and validation.

Only configuration needed by the current milestone is interpreted. The
``programs`` section is deliberately kept as raw data for the future
external-program dispatcher and is never executed here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import json5

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path("config.json5")
CONFIG_ENV_VAR = "BOTTG_CONFIG"
TOKEN_ENV_VAR = "BOTTG_TOKEN"

_TOP_LEVEL_KEYS = frozenset({"telegram", "polling", "runtime", "programs"})
_TELEGRAM_KEYS = frozenset({"token", "group_id"})
_POLLING_KEYS = frozenset(
    {
        "timeout_seconds",
        "poll_interval",
        "drop_pending_updates",
        "allowed_updates",
        "max_pending_updates",
    }
)
_RUNTIME_KEYS = frozenset({"log_level", "telegram_log_level"})


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    """Telegram connection settings."""

    token: str
    group_id: int


@dataclass(frozen=True, slots=True)
class PollingConfig:
    """Long-polling settings passed to PTB's updater."""

    timeout_seconds: int = 30
    poll_interval: float = 0.0
    drop_pending_updates: bool = False
    allowed_updates: tuple[str, ...] | None = None
    # Bound the in-memory update queue. 0 means unbounded.
    max_pending_updates: int = 100


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Process-level settings."""

    log_level: str = "INFO"
    # None (or "OFF" in JSON5) disables the Telegram log channel.
    telegram_log_level: str | None = "INFO"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated application configuration."""

    telegram: TelegramConfig
    polling: PollingConfig = field(default_factory=PollingConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    # Reserved for future, config-defined external programs. Not used yet.
    programs: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_path: Path | None = None


def load_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    env: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load and validate a JSON5 config file.

    ``BOTTG_TOKEN`` takes precedence over ``telegram.token`` so that the
    secret does not have to live in the config file.
    """

    config_path = Path(path).expanduser()
    environment = os.environ if env is None else env

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = json5.load(handle)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ConfigError(f"cannot read {config_path}: {detail}") from exc
    except ValueError as exc:
        raise ConfigError(f"invalid JSON5 in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: top-level value must be an object")

    _reject_unknown_keys(raw, _TOP_LEVEL_KEYS, "top level")

    telegram_raw = _mapping(raw.get("telegram"), "telegram", required=True)
    polling_raw = _mapping(raw.get("polling"), "polling", required=False)
    runtime_raw = _mapping(raw.get("runtime"), "runtime", required=False)
    programs_raw = _mapping(raw.get("programs"), "programs", required=False)

    _reject_unknown_keys(telegram_raw, _TELEGRAM_KEYS, "telegram")
    _reject_unknown_keys(polling_raw, _POLLING_KEYS, "polling")
    _reject_unknown_keys(runtime_raw, _RUNTIME_KEYS, "runtime")

    token = _load_token(telegram_raw, environment)
    group_id = _required_int(telegram_raw, "group_id", "telegram")
    if group_id >= 0:
        raise ConfigError(
            "telegram.group_id must be negative; "
            "supergroup IDs look like -1001234567890"
        )

    polling = PollingConfig(
        timeout_seconds=_optional_int(
            polling_raw, "timeout_seconds", "polling", default=30, minimum=1
        ),
        poll_interval=_optional_number(
            polling_raw, "poll_interval", "polling", default=0.0, minimum=0.0
        ),
        drop_pending_updates=_optional_bool(
            polling_raw, "drop_pending_updates", "polling", default=False
        ),
        allowed_updates=_optional_allowed_updates(polling_raw),
        max_pending_updates=_optional_int(
            polling_raw,
            "max_pending_updates",
            "polling",
            default=100,
            minimum=0,
        ),
    )
    runtime = RuntimeConfig(
        log_level=_optional_log_level(
            runtime_raw, "log_level", default="INFO", allow_disable=False
        ),
        telegram_log_level=_optional_log_level(
            runtime_raw,
            "telegram_log_level",
            default="INFO",
            allow_disable=True,
        ),
    )
    programs = MappingProxyType(dict(programs_raw))

    return AppConfig(
        telegram=TelegramConfig(token=token, group_id=group_id),
        polling=polling,
        runtime=runtime,
        programs=programs,
        source_path=config_path,
    )


def _mapping(value: Any, where: str, *, required: bool) -> dict[str, Any]:
    if value is None:
        if required:
            raise ConfigError(f"missing required section '{where}'")
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{where}' must be an object")
    return value


def _reject_unknown_keys(
    data: Mapping[str, Any], allowed: frozenset[str], where: str
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        rendered = ", ".join(repr(key) for key in unknown)
        raise ConfigError(f"unknown key(s) in {where}: {rendered}")


def _load_token(
    telegram_raw: Mapping[str, Any], environment: Mapping[str, str]
) -> str:
    configured = telegram_raw.get("token", "")
    if configured is not None and not isinstance(configured, str):
        raise ConfigError("'telegram.token' must be a string")

    token = (environment.get(TOKEN_ENV_VAR) or configured or "").strip()
    if not token:
        raise ConfigError(
            "Telegram bot token is missing; set BOTTG_TOKEN or "
            "telegram.token in the config file"
        )

    bot_id, separator, secret = token.partition(":")
    if not separator or not bot_id.isdigit() or not secret:
        raise ConfigError(
            "Telegram bot token has an invalid format; expected '<bot_id>:<secret>'"
        )
    return token


def _required_int(
    data: Mapping[str, Any], key: str, where: str
) -> int:
    if key not in data:
        raise ConfigError(f"missing required key '{where}.{key}'")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{where}.{key}' must be an integer")
    return value


def _optional_int(
    data: Mapping[str, Any],
    key: str,
    where: str,
    *,
    default: int,
    minimum: int,
) -> int:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{where}.{key}' must be an integer")
    if value < minimum:
        raise ConfigError(f"'{where}.{key}' must be >= {minimum}")
    return value


def _optional_number(
    data: Mapping[str, Any],
    key: str,
    where: str,
    *,
    default: float,
    minimum: float,
) -> float:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{where}.{key}' must be a number")
    result = float(value)
    if result < minimum:
        raise ConfigError(f"'{where}.{key}' must be >= {minimum}")
    return result


def _optional_bool(
    data: Mapping[str, Any],
    key: str,
    where: str,
    *,
    default: bool,
) -> bool:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise ConfigError(f"'{where}.{key}' must be a boolean")
    return value


def _optional_allowed_updates(
    polling_raw: Mapping[str, Any],
) -> tuple[str, ...] | None:
    if "allowed_updates" not in polling_raw:
        return None
    value = polling_raw["allowed_updates"]
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ConfigError("'polling.allowed_updates' must be null or an array of strings")

    updates: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(
                f"'polling.allowed_updates[{index}]' must be a non-empty string"
            )
        updates.append(item.strip())
    return tuple(updates)


def _optional_log_level(
    runtime_raw: Mapping[str, Any],
    key: str,
    *,
    default: str,
    allow_disable: bool,
) -> str | None:
    value = runtime_raw.get(key, default)
    full_key = f"runtime.{key}"
    if value is None:
        if allow_disable:
            return None
        raise ConfigError(f"'{full_key}' must be a string")
    if not isinstance(value, str):
        raise ConfigError(f"'{full_key}' must be a string")
    normalized = value.upper()
    if allow_disable and normalized in {"OFF", "NONE"}:
        return None
    if normalized not in logging.getLevelNamesMapping():
        allowed = ", ".join(
            name
            for name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
            if name in logging.getLevelNamesMapping()
        )
        raise ConfigError(
            f"'{full_key}' must be one of {allowed}; got {value!r}"
        )
    return normalized
