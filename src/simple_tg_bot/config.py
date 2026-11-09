"""JSON5 configuration loading and validation.

This module only produces immutable configuration objects. It does not touch
Telegram and does not execute external programs; ``programs`` is validated into
``ProgramConfig`` objects for the dispatcher in :mod:`simple_tg_bot.programs`.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import json5

from .errors import ConfigError

DEFAULT_CONFIG_PATH = Path("config.json5")
CONFIG_ENV_VAR = "SIMPLE_TG_BOT_CONFIG"
TOKEN_ENV_VAR = "SIMPLE_TG_BOT_TOKEN"

HOOK_STARTUP = "startup"
HOOK_MESSAGE = "message"
HOOK_EDITED_MESSAGE = "edited_message"
HOOK_SHUTDOWN = "shutdown"
HOOK_NAMES = frozenset(
    {HOOK_STARTUP, HOOK_MESSAGE, HOOK_EDITED_MESSAGE, HOOK_SHUTDOWN}
)

_TOP_LEVEL_KEYS = frozenset(
    {"telegram", "polling", "runtime", "actions", "programs"}
)
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
_ACTIONS_KEYS = frozenset(
    {
        "max_pending",
        "enqueue_timeout_seconds",
        "drain_timeout_seconds",
        "min_interval_seconds",
    }
)
_PROGRAM_KEYS = frozenset(
    {
        "command",
        "hooks",
        "timeout_seconds",
        "cwd",
        "env",
        "message",
        "max_output_bytes",
    }
)
_PROGRAM_MESSAGE_KEYS = frozenset(
    {
        "topics",
        "ignore_bots",
        "download_attachments",
        "max_attachment_bytes",
    }
)


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
class ActionsConfig:
    """Action queue settings."""

    # Maximum number of action batches waiting for the single worker.
    max_pending: int = 100
    # How long an enqueue may wait for room before the batch is rejected.
    enqueue_timeout_seconds: float = 10.0
    # How long graceful shutdown may spend draining queued actions.
    drain_timeout_seconds: float = 30.0
    # Minimum gap between Telegram API actions.
    min_interval_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class MessageHookConfig:
    """Message and edited-message hook filters for one program."""

    # None means all topics. A tuple entry may be None to mean the General
    # topic (or a non-forum chat).
    topics: tuple[int | None, ...] | None = None
    ignore_bots: bool = True
    # Download message attachments into a per-invocation temp directory and
    # expose the paths as message.attachments[].local_path.
    download_attachments: bool = False
    # Per-file limit for downloads. Telegram bot downloads are capped at
    # 20 MB by the Bot API anyway.
    max_attachment_bytes: int = 20_000_000


@dataclass(frozen=True, slots=True)
class ProgramConfig:
    """One external program and its hook subscriptions."""

    name: str
    command: tuple[str, ...]
    hooks: frozenset[str]
    timeout_seconds: float = 30.0
    # Absolute path. Defaults to the directory containing the config file.
    cwd: Path = Path(".")
    env: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    message: MessageHookConfig = field(default_factory=MessageHookConfig)
    max_output_bytes: int = 65536


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated application configuration."""

    telegram: TelegramConfig
    polling: PollingConfig = field(default_factory=PollingConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    programs: Mapping[str, ProgramConfig] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_path: Path | None = None


def load_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    env: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load and validate a JSON5 config file.

    ``SIMPLE_TG_BOT_TOKEN`` takes precedence over ``telegram.token`` so that
    the secret does not have to live in the config file.
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
    actions_raw = _mapping(raw.get("actions"), "actions", required=False)
    programs_raw = _mapping(raw.get("programs"), "programs", required=False)

    _reject_unknown_keys(telegram_raw, _TELEGRAM_KEYS, "telegram")
    _reject_unknown_keys(polling_raw, _POLLING_KEYS, "polling")
    _reject_unknown_keys(runtime_raw, _RUNTIME_KEYS, "runtime")
    _reject_unknown_keys(actions_raw, _ACTIONS_KEYS, "actions")

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
    actions = ActionsConfig(
        max_pending=_optional_int(
            actions_raw, "max_pending", "actions", default=100, minimum=1
        ),
        enqueue_timeout_seconds=_optional_number(
            actions_raw,
            "enqueue_timeout_seconds",
            "actions",
            default=10.0,
            minimum=0.0,
        ),
        drain_timeout_seconds=_optional_number(
            actions_raw,
            "drain_timeout_seconds",
            "actions",
            default=30.0,
            minimum=0.0,
        ),
        min_interval_seconds=_optional_number(
            actions_raw,
            "min_interval_seconds",
            "actions",
            default=0.0,
            minimum=0.0,
        ),
    )

    base_dir = config_path.resolve().parent
    programs = _parse_programs(programs_raw, base_dir)

    return AppConfig(
        telegram=TelegramConfig(token=token, group_id=group_id),
        polling=polling,
        runtime=runtime,
        actions=actions,
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


def _parse_programs(
    raw: Mapping[str, Any], base_dir: Path
) -> Mapping[str, ProgramConfig]:
    programs: dict[str, ProgramConfig] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("program names must be non-empty strings")
        where = f"programs.{name}"
        if not isinstance(value, dict):
            raise ConfigError(f"'{where}' must be an object")
        _reject_unknown_keys(value, _PROGRAM_KEYS, where)

        command = _required_command(value, where)
        hooks = _required_hooks(value, where)
        timeout_seconds = _optional_positive_number(
            value, "timeout_seconds", where, default=30.0
        )
        cwd = _optional_cwd(value, where, base_dir)
        env = _optional_env(value, where)
        max_output_bytes = _optional_int(
            value, "max_output_bytes", where, default=65536, minimum=1
        )

        message_raw = _mapping(
            value.get("message"), f"{where}.message", required=False
        )
        _reject_unknown_keys(
            message_raw, _PROGRAM_MESSAGE_KEYS, f"{where}.message"
        )
        if "message" in value and not (
            hooks & {HOOK_MESSAGE, HOOK_EDITED_MESSAGE}
        ):
            raise ConfigError(
                f"'{where}.message' is only valid when 'message' or "
                f"'edited_message' is listed in '{where}.hooks'"
            )
        message = MessageHookConfig(
            topics=_optional_topics(message_raw, f"{where}.message"),
            ignore_bots=_optional_bool(
                message_raw,
                "ignore_bots",
                f"{where}.message",
                default=True,
            ),
            download_attachments=_optional_bool(
                message_raw,
                "download_attachments",
                f"{where}.message",
                default=False,
            ),
            max_attachment_bytes=_optional_int(
                message_raw,
                "max_attachment_bytes",
                f"{where}.message",
                default=20_000_000,
                minimum=1,
            ),
        )

        programs[name] = ProgramConfig(
            name=name,
            command=command,
            hooks=hooks,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
            env=MappingProxyType(env),
            message=message,
            max_output_bytes=max_output_bytes,
        )
    return MappingProxyType(programs)


def _required_command(
    data: Mapping[str, Any], where: str
) -> tuple[str, ...]:
    if "command" not in data:
        raise ConfigError(f"missing required key '{where}.command'")
    raw = data["command"]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ConfigError(
            f"'{where}.command' must be a non-empty array of strings"
        )

    command: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item:
            raise ConfigError(
                f"'{where}.command[{index}]' must be a non-empty string"
            )
        command.append(item)
    return tuple(command)


def _required_hooks(
    data: Mapping[str, Any], where: str
) -> frozenset[str]:
    if "hooks" not in data:
        raise ConfigError(f"missing required key '{where}.hooks'")
    raw = data["hooks"]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ConfigError(f"'{where}.hooks' must be a non-empty array")

    allowed = ", ".join(sorted(HOOK_NAMES))
    hooks: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, str) or item not in HOOK_NAMES:
            raise ConfigError(
                f"'{where}.hooks[{index}]' must be one of {allowed}"
            )
        hooks.add(item)
    return frozenset(hooks)


def _optional_cwd(
    data: Mapping[str, Any], where: str, base_dir: Path
) -> Path:
    if "cwd" not in data or data["cwd"] is None:
        return base_dir
    value = data["cwd"]
    if not isinstance(value, str) or not value:
        raise ConfigError(
            f"'{where}.cwd' must be a non-empty string or null"
        )

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_dir():
        raise ConfigError(f"'{where}.cwd' is not a directory: {path}")
    return path


def _optional_env(
    data: Mapping[str, Any], where: str
) -> dict[str, str]:
    if "env" not in data or data["env"] is None:
        return {}
    raw = data["env"]
    if not isinstance(raw, dict):
        raise ConfigError(f"'{where}.env' must be an object")

    env: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise ConfigError(
                f"'{where}.env' keys must be non-empty strings"
            )
        if not isinstance(value, str):
            raise ConfigError(f"'{where}.env[{key!r}]' must be a string")
        env[key] = value
    return env


def _optional_topics(
    data: Mapping[str, Any], where: str
) -> tuple[int | None, ...] | None:
    if "topics" not in data or data["topics"] is None:
        return None
    raw = data["topics"]
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ConfigError(
            f"'{where}.topics' must be null or a non-empty array"
        )

    topics: list[int | None] = []
    for index, item in enumerate(raw):
        if item is None:
            topics.append(None)
            continue
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ConfigError(
                f"'{where}.topics[{index}]' must be null or a positive "
                "integer"
            )
        topics.append(item)
    return tuple(topics)


def _load_token(
    telegram_raw: Mapping[str, Any], environment: Mapping[str, str]
) -> str:
    configured = telegram_raw.get("token", "")
    if configured is not None and not isinstance(configured, str):
        raise ConfigError("'telegram.token' must be a string")

    token = (environment.get(TOKEN_ENV_VAR) or configured or "").strip()
    if not token:
        raise ConfigError(
            "Telegram bot token is missing; set SIMPLE_TG_BOT_TOKEN or "
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
    if not math.isfinite(result):
        raise ConfigError(f"'{where}.{key}' must be finite")
    return result


def _optional_positive_number(
    data: Mapping[str, Any],
    key: str,
    where: str,
    *,
    default: float,
) -> float:
    value = _optional_number(
        data, key, where, default=default, minimum=0.0
    )
    if value <= 0:
        raise ConfigError(f"'{where}.{key}' must be > 0")
    return value


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
        raise ConfigError(
            "'polling.allowed_updates' must be null or an array of strings"
        )

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
