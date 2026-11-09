#!/usr/bin/env python3
"""Example delete-message hook.

``!delete`` or ``/delete`` deletes the triggering message. An optional numeric
argument deletes a specific message instead.
"""

from __future__ import annotations

import json
import sys


def parse_command(text: str) -> str | None:
    command, _separator, rest = text.partition(" ")
    if command.startswith("/") and "@" in command:
        command = command.split("@", 1)[0]
    if command in ("!delete", "/delete"):
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

    rest = parse_command(text)
    if rest is None:
        return 0

    payload: dict[str, object] = {"action": "delete_message"}
    if rest:
        parts = rest.split()
        if not parts or not parts[0].isdigit():
            return 0
        message_id = int(parts[0])
        if message_id <= 0:
            return 0
        payload["message_id"] = message_id

    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
