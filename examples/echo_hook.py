#!/usr/bin/env python3
"""Example bottg message hook.

Reads one hook event as JSON from stdin. If the message is a ``!echo ...`` or
``/echo ...`` command, writes one action as JSON to stdout. Every other message
produces no stdout, which tells bottg to do nothing.

Diagnostics must go to stderr; stdout is reserved for the action protocol.
"""

from __future__ import annotations

import json
import sys


def extract_reply(text: str) -> str | None:
    if text.startswith("!echo "):
        return text[len("!echo ") :].strip()
    if text == "/echo" or text.startswith("/echo "):
        return text[len("/echo") :].strip()
    if text.startswith("/echo@"):
        _command, _separator, rest = text.partition(" ")
        return rest.strip()
    return None


def main() -> int:
    event = json.load(sys.stdin)
    message = event.get("message")
    if not isinstance(message, dict):
        return 0

    text = message.get("text")
    if not isinstance(text, str):
        return 0

    reply = extract_reply(text)
    if not reply:
        return 0

    json.dump(
        {
            "action": "send_message",
            "text": reply,
            "reply_to_message_id": message["id"],
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
