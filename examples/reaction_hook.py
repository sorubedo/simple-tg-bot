#!/usr/bin/env python3
"""Example reaction hook.

``!react [emoji]`` or ``/react [emoji]`` sets a reaction on the triggering
message. The default is 👍. The action omits ``message_id``, so Simple-TG-Bot
uses the message that triggered the hook.
"""

from __future__ import annotations

import json
import sys


def parse_command(text: str) -> str | None:
    command, _separator, rest = text.partition(" ")
    if command.startswith("/") and "@" in command:
        command = command.split("@", 1)[0]
    if command in ("!react", "/react"):
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

    emoji = rest.split()[0] if rest else "👍"
    json.dump(
        {"action": "set_reaction", "emoji": emoji},
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
