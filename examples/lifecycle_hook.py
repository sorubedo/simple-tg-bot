#!/usr/bin/env python3
"""Example bottg startup/shutdown hook.

Sends a short notice to the General topic on startup and shutdown. Shutdown
actions are still drained after this program exits, so the message is delivered
before bottg closes the Telegram client.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    event = json.load(sys.stdin)
    hook = event.get("hook")
    if hook == "startup":
        text = f"bottg {event['bottg']['version']} started"
    elif hook == "shutdown":
        text = f"bottg stopping ({event.get('reason')})"
    else:
        return 0

    json.dump(
        {"action": "send_message", "text": text},
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
