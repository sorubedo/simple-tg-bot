#!/usr/bin/env python3
"""Example Simple-TG-Bot message hook.

Reads one hook event as JSON from stdin. ``!echo`` and ``/echo`` reply with the
remaining text. ``!slowecho`` does the same but first enqueues a one-second
``sleep`` action, showing how to pace a sequence.

Every other message produces no stdout, which tells Simple-TG-Bot to do
nothing.
Diagnostics must go to stderr; stdout is reserved for the action protocol.
"""

from __future__ import annotations

import json
import sys


def parse_command(text: str) -> tuple[str, str] | None:
    command, _separator, rest = text.partition(" ")
    if command.startswith("/") and "@" in command:
        command = command.split("@", 1)[0]
    if command in ("!echo", "/echo"):
        return "echo", rest.strip()
    if command in ("!slowecho", "/slowecho"):
        return "slowecho", rest.strip()
    return None


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
    command, reply = parsed
    if not reply:
        return 0

    send = {
        "action": "send_message",
        "text": reply,
        "reply_to_message_id": message["id"],
    }
    payload: object
    if command == "slowecho":
        payload = [{"action": "sleep", "seconds": 1.0}, send]
    else:
        payload = send

    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
