"""Action model and parser for external hook programs.

An external program describes what bottg should do by writing JSON to stdout.
Programs never address a chat: every action is executed against the configured
target supergroup, so a script cannot accidentally affect another group.
Message and topic ids default to the hook trigger, which means a script can
simply print ``{"action": "send_message", "text": "hello"}`` to answer in the
same topic.

Accepted stdout forms are a single action object, an array of action objects,
or an object with an ``actions`` array. Empty stdout means "no actions".
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, NoReturn, TypeAlias

from .errors import ActionPayloadError

ACTION_PROTOCOL: Final = "bottg.actions/1"

MAX_MESSAGE_UTF16: Final = 4096
MAX_SLEEP_SECONDS: Final = 3600.0
MAX_EMOJI_CODE_POINTS: Final = 32
MAX_TOPIC_NAME_LENGTH: Final = 128

_ACTION_SEND_MESSAGE: Final = "send_message"
_ACTION_DELETE_MESSAGE: Final = "delete_message"
_ACTION_SET_REACTION: Final = "set_reaction"
_ACTION_CREATE_TOPIC: Final = "create_topic"
_ACTION_DELETE_TOPIC: Final = "delete_topic"
_ACTION_EDIT_TOPIC: Final = "edit_topic"
_ACTION_SLEEP: Final = "sleep"

_PARSE_MODES: Final[dict[str, str]] = {
    "HTML": "HTML",
    "MARKDOWN": "Markdown",
    "MARKDOWNV2": "MarkdownV2",
}

_SEND_MESSAGE_FIELDS: Final = frozenset(
    {
        "action",
        "text",
        "topic_id",
        "reply_to_message_id",
        "parse_mode",
        "disable_notification",
        "disable_web_page_preview",
    }
)
_DELETE_MESSAGE_FIELDS: Final = frozenset({"action", "message_id"})
_SET_REACTION_FIELDS: Final = frozenset(
    {"action", "message_id", "emoji", "is_big"}
)
_CREATE_TOPIC_FIELDS: Final = frozenset({"action", "name"})
_DELETE_TOPIC_FIELDS: Final = frozenset({"action", "topic_id"})
_EDIT_TOPIC_FIELDS: Final = frozenset({"action", "topic_id", "name"})
_SLEEP_FIELDS: Final = frozenset({"action", "seconds"})


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Trigger information used to fill omitted action fields."""

    topic_id: int | None = None
    message_id: int | None = None


@dataclass(frozen=True, slots=True)
class SendMessageAction:
    """Send a text message in the target group."""

    text: str
    topic_id: int | None = None
    reply_to_message_id: int | None = None
    parse_mode: str | None = None
    disable_notification: bool = False
    disable_web_page_preview: bool = False


@dataclass(frozen=True, slots=True)
class DeleteMessageAction:
    """Delete a message in the target group."""

    message_id: int


@dataclass(frozen=True, slots=True)
class SetReactionAction:
    """Set one emoji reaction on a message."""

    message_id: int
    emoji: str
    is_big: bool = False


@dataclass(frozen=True, slots=True)
class CreateTopicAction:
    """Create a forum topic in the target group."""

    name: str


@dataclass(frozen=True, slots=True)
class DeleteTopicAction:
    """Delete a forum topic from the target group."""

    topic_id: int


@dataclass(frozen=True, slots=True)
class EditTopicAction:
    """Rename a forum topic in the target group."""

    topic_id: int
    name: str


@dataclass(frozen=True, slots=True)
class SleepAction:
    """Pause the action queue for a number of seconds."""

    seconds: float


Action: TypeAlias = (
    SendMessageAction
    | DeleteMessageAction
    | SetReactionAction
    | CreateTopicAction
    | DeleteTopicAction
    | EditTopicAction
    | SleepAction
)


def parse_actions(
    text: str, *, context: ActionContext | None = None
) -> list[Action]:
    """Parse an external program's stdout into an ordered action list.

    ``ActionPayloadError`` is raised when the output is malformed. Nothing is
    partially returned: either the whole stdout value is valid and every action
    is returned, or the caller gets an exception and should discard the output.
    """

    stripped = text.strip().lstrip("\ufeff")
    if not stripped:
        return []

    try:
        payload = json.loads(stripped, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        detail = f"{exc.msg} (line {exc.lineno}, column {exc.colno})"
        raise ActionPayloadError(f"stdout is not valid JSON: {detail}") from exc
    except ValueError as exc:
        raise ActionPayloadError(f"stdout is not valid JSON: {exc}") from exc

    return _parse_payload(payload, context or ActionContext(), "stdout")


def describe_action(action: Action) -> str:
    """Return a short, log-safe description of an action."""

    if isinstance(action, SendMessageAction):
        details = [f"topic={_topic_label(action.topic_id)}"]
        if action.reply_to_message_id is not None:
            details.append(f"reply_to={action.reply_to_message_id}")
        details.append(f"chars={len(action.text)}")
        return f"send_message({', '.join(details)})"
    if isinstance(action, DeleteMessageAction):
        return f"delete_message(message_id={action.message_id})"
    if isinstance(action, SetReactionAction):
        return f"set_reaction(message_id={action.message_id}, emoji={action.emoji!r})"
    if isinstance(action, CreateTopicAction):
        return f"create_topic(name={action.name!r})"
    if isinstance(action, DeleteTopicAction):
        return f"delete_topic(topic_id={action.topic_id})"
    if isinstance(action, EditTopicAction):
        return (
            f"edit_topic(topic_id={action.topic_id}, name={action.name!r})"
        )
    return f"sleep(seconds={action.seconds:g})"


def _parse_payload(
    value: Any, context: ActionContext, where: str
) -> list[Action]:
    if isinstance(value, list):
        return [
            _parse_action(item, context, f"{where}[{index}]")
            for index, item in enumerate(value)
        ]

    if isinstance(value, dict):
        if "actions" in value:
            _reject_unknown_fields(value, {"actions", "protocol"}, where)
            protocol = value.get("protocol")
            if protocol is not None and protocol != ACTION_PROTOCOL:
                raise ActionPayloadError(
                    f"{where}.protocol: unsupported protocol {protocol!r}; "
                    f"expected {ACTION_PROTOCOL!r}"
                )
            items = value["actions"]
            if not isinstance(items, list):
                raise ActionPayloadError(f"{where}.actions must be an array")
            return [
                _parse_action(item, context, f"{where}.actions[{index}]")
                for index, item in enumerate(items)
            ]

        if "action" in value:
            return [_parse_action(value, context, where)]

        raise ActionPayloadError(
            f"{where}: expected an action object, an array of actions, "
            "or an object with an 'actions' array"
        )

    raise ActionPayloadError(f"{where}: expected a JSON object or array")


def _parse_action(value: Any, context: ActionContext, where: str) -> Action:
    if not isinstance(value, dict):
        raise ActionPayloadError(f"{where}: action must be an object")

    name = value.get("action")
    if not isinstance(name, str):
        raise ActionPayloadError(f"{where}.action: expected a string")

    if name == _ACTION_SEND_MESSAGE:
        return _parse_send_message(value, context, where)
    if name == _ACTION_DELETE_MESSAGE:
        return _parse_delete_message(value, context, where)
    if name == _ACTION_SET_REACTION:
        return _parse_set_reaction(value, context, where)
    if name == _ACTION_CREATE_TOPIC:
        return _parse_create_topic(value, where)
    if name == _ACTION_DELETE_TOPIC:
        return _parse_delete_topic(value, context, where)
    if name == _ACTION_EDIT_TOPIC:
        return _parse_edit_topic(value, context, where)
    if name == _ACTION_SLEEP:
        return _parse_sleep(value, where)

    supported = ", ".join(
        repr(item)
        for item in (
            _ACTION_SEND_MESSAGE,
            _ACTION_DELETE_MESSAGE,
            _ACTION_SET_REACTION,
            _ACTION_CREATE_TOPIC,
            _ACTION_DELETE_TOPIC,
            _ACTION_EDIT_TOPIC,
            _ACTION_SLEEP,
        )
    )
    raise ActionPayloadError(
        f"{where}.action: unknown action {name!r}; supported actions are "
        f"{supported}"
    )


def _parse_send_message(
    data: Mapping[str, Any], context: ActionContext, where: str
) -> SendMessageAction:
    _reject_unknown_fields(data, _SEND_MESSAGE_FIELDS, where)

    text = _required_str(data, "text", where)
    if not text.strip():
        raise ActionPayloadError(f"{where}.text must not be empty")
    units = _utf16_length(text, f"{where}.text")
    if units > MAX_MESSAGE_UTF16:
        raise ActionPayloadError(
            f"{where}.text is too long ({units} UTF-16 code units; "
            f"max {MAX_MESSAGE_UTF16})"
        )

    if "topic_id" in data:
        topic_id = _nullable_positive_int(
            data["topic_id"], f"{where}.topic_id"
        )
    else:
        topic_id = context.topic_id

    return SendMessageAction(
        text=text,
        topic_id=topic_id,
        reply_to_message_id=_optional_nullable_positive_int(
            data, "reply_to_message_id", where
        ),
        parse_mode=_optional_parse_mode(data, where),
        disable_notification=_optional_bool(
            data, "disable_notification", where, default=False
        ),
        disable_web_page_preview=_optional_bool(
            data, "disable_web_page_preview", where, default=False
        ),
    )


def _parse_delete_message(
    data: Mapping[str, Any], context: ActionContext, where: str
) -> DeleteMessageAction:
    _reject_unknown_fields(data, _DELETE_MESSAGE_FIELDS, where)
    return DeleteMessageAction(
        message_id=_resolve_message_id(data, context, where)
    )


def _parse_set_reaction(
    data: Mapping[str, Any], context: ActionContext, where: str
) -> SetReactionAction:
    _reject_unknown_fields(data, _SET_REACTION_FIELDS, where)

    emoji = _required_str(data, "emoji", where)
    if not emoji.strip():
        raise ActionPayloadError(f"{where}.emoji must not be empty")
    if len(emoji) > MAX_EMOJI_CODE_POINTS:
        raise ActionPayloadError(
            f"{where}.emoji must be a single emoji, not {len(emoji)} "
            "characters"
        )

    return SetReactionAction(
        message_id=_resolve_message_id(data, context, where),
        emoji=emoji,
        is_big=_optional_bool(data, "is_big", where, default=False),
    )


def _parse_sleep(data: Mapping[str, Any], where: str) -> SleepAction:
    _reject_unknown_fields(data, _SLEEP_FIELDS, where)

    if "seconds" not in data:
        raise ActionPayloadError(f"missing required field '{where}.seconds'")
    value = data["seconds"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActionPayloadError(f"{where}.seconds must be a number")
    seconds = float(value)
    if not math.isfinite(seconds):
        raise ActionPayloadError(f"{where}.seconds must be finite")
    if seconds <= 0:
        raise ActionPayloadError(f"{where}.seconds must be > 0")
    if seconds > MAX_SLEEP_SECONDS:
        raise ActionPayloadError(
            f"{where}.seconds must be <= {MAX_SLEEP_SECONDS:g}"
        )
    return SleepAction(seconds=seconds)


def _parse_create_topic(
    data: Mapping[str, Any], where: str
) -> CreateTopicAction:
    _reject_unknown_fields(data, _CREATE_TOPIC_FIELDS, where)
    return CreateTopicAction(name=_topic_name(data, where))


def _parse_delete_topic(
    data: Mapping[str, Any], context: ActionContext, where: str
) -> DeleteTopicAction:
    _reject_unknown_fields(data, _DELETE_TOPIC_FIELDS, where)
    return DeleteTopicAction(
        topic_id=_resolve_topic_id(data, context, where)
    )


def _parse_edit_topic(
    data: Mapping[str, Any], context: ActionContext, where: str
) -> EditTopicAction:
    _reject_unknown_fields(data, _EDIT_TOPIC_FIELDS, where)
    return EditTopicAction(
        topic_id=_resolve_topic_id(data, context, where),
        name=_topic_name(data, where),
    )


def _resolve_message_id(
    data: Mapping[str, Any], context: ActionContext, where: str
) -> int:
    if "message_id" in data:
        return _positive_int(data["message_id"], f"{where}.message_id")
    if context.message_id is None:
        raise ActionPayloadError(
            f"{where}.message_id is required because the hook has no "
            "triggering message"
        )
    return context.message_id


def _resolve_topic_id(
    data: Mapping[str, Any], context: ActionContext, where: str
) -> int:
    if "topic_id" in data:
        return _positive_int(data["topic_id"], f"{where}.topic_id")
    if context.topic_id is None:
        raise ActionPayloadError(
            f"{where}.topic_id is required because the hook has no topic "
            "context (General topic or non-forum group)"
        )
    return context.topic_id


def _topic_name(data: Mapping[str, Any], where: str) -> str:
    name = _required_str(data, "name", where)
    if not name.strip():
        raise ActionPayloadError(f"{where}.name must not be empty")
    if len(name) > MAX_TOPIC_NAME_LENGTH:
        raise ActionPayloadError(
            f"{where}.name is too long ({len(name)} characters; "
            f"max {MAX_TOPIC_NAME_LENGTH})"
        )
    return name


def _reject_unknown_fields(
    data: Mapping[str, Any], allowed: frozenset[str] | set[str], where: str
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        rendered = ", ".join(repr(key) for key in unknown)
        raise ActionPayloadError(f"{where}: unknown field(s) {rendered}")


def _required_str(
    data: Mapping[str, Any], key: str, where: str
) -> str:
    if key not in data:
        raise ActionPayloadError(f"missing required field '{where}.{key}'")
    value = data[key]
    if not isinstance(value, str):
        raise ActionPayloadError(f"{where}.{key} must be a string")
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
        raise ActionPayloadError(f"{where}.{key} must be a boolean")
    return value


def _optional_parse_mode(
    data: Mapping[str, Any], where: str
) -> str | None:
    if "parse_mode" not in data or data["parse_mode"] is None:
        return None
    value = data["parse_mode"]
    if not isinstance(value, str):
        raise ActionPayloadError(f"{where}.parse_mode must be a string or null")
    canonical = _PARSE_MODES.get(value.upper())
    if canonical is None:
        allowed = ", ".join(_PARSE_MODES.values())
        raise ActionPayloadError(
            f"{where}.parse_mode must be null or one of {allowed}"
        )
    return canonical


def _optional_nullable_positive_int(
    data: Mapping[str, Any], key: str, where: str
) -> int | None:
    if key not in data or data[key] is None:
        return None
    return _positive_int(data[key], f"{where}.{key}")


def _nullable_positive_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, where)


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ActionPayloadError(f"{where} must be a positive integer")
    return value


def _utf16_length(text: str, where: str) -> int:
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ActionPayloadError(
            f"{where} contains invalid Unicode"
        ) from exc


def _topic_label(topic_id: int | None) -> str:
    return "general" if topic_id is None else str(topic_id)


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite number {value!r} is not allowed")


__all__ = [
    "ACTION_PROTOCOL",
    "MAX_MESSAGE_UTF16",
    "MAX_SLEEP_SECONDS",
    "MAX_TOPIC_NAME_LENGTH",
    "Action",
    "ActionContext",
    "CreateTopicAction",
    "DeleteMessageAction",
    "DeleteTopicAction",
    "EditTopicAction",
    "SendMessageAction",
    "SetReactionAction",
    "SleepAction",
    "describe_action",
    "parse_actions",
]
