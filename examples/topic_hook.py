#!/usr/bin/env python3
"""Example forum-topic hook.

Commands (``!`` or ``/`` prefix, optional ``@bot`` suffix):

* ``newtopic <name>`` creates a topic.
* ``renametopic <name>`` renames the current topic.
* ``deletetopic confirm`` deletes the current topic.
* ``topicid`` replies with the current topic id.
* ``topics`` explains why listing topics requires external state.

The Telegram Bot API has no method that lists all topics. An external program
that needs a topic registry must save it itself. This example only uses the
topic id carried by the hook event.
"""

from __future__ import annotations

import json
import sys


def parse_command(text: str) -> tuple[str, str] | None:
    command, _separator, rest = text.partition(" ")
    if command.startswith("/") and "@" in command:
        command = command.split("@", 1)[0]
    for name in (
        "newtopic",
        "renametopic",
        "deletetopic",
        "topicid",
        "topics",
    ):
        if command in (f"!{name}", f"/{name}"):
            return name, rest.strip()
    return None


def reply(text: str, message_id: int) -> dict[str, object]:
    return {
        "action": "send_message",
        "text": text,
        "reply_to_message_id": message_id,
    }


def main() -> int:
    event = json.load(sys.stdin)
    message = event.get("message")
    if not isinstance(message, dict):
        return 0

    text = message.get("text")
    if not isinstance(text, str):
        return 0

    parsed = parse_command(text)
    if parsed is None:
        return 0
    command, rest = parsed

    topic = event.get("topic")
    topic_id = topic.get("id") if isinstance(topic, dict) else None
    message_id = message["id"]

    if command == "newtopic":
        if not rest:
            return 0
        payload: object = {"action": "create_topic", "name": rest}
    elif command == "renametopic":
        if not rest:
            return 0
        if topic_id is None:
            payload = reply("请在具体话题内使用这个命令。", message_id)
        else:
            payload = {
                "action": "edit_topic",
                "topic_id": topic_id,
                "name": rest,
            }
    elif command == "deletetopic":
        if topic_id is None:
            payload = reply("请在具体话题内使用这个命令。", message_id)
        elif rest.lower() != "confirm":
            payload = reply(
                "请发送 !deletetopic confirm 确认删除当前话题。",
                message_id,
            )
        else:
            payload = {"action": "delete_topic", "topic_id": topic_id}
    elif command == "topicid":
        label = "General/无话题" if topic_id is None else str(topic_id)
        payload = reply(f"当前话题 ID: {label}", message_id)
    else:
        payload = reply(
            "Telegram 没有列出话题的 API，请由外挂程序自行保存话题列表。",
            message_id,
        )

    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
