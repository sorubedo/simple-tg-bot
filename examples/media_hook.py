#!/usr/bin/env python3
"""Example bottg attachment hook.

Media messages have ``message.text == null``; the command is usually in
``message.caption``. Attachments are described in ``message.attachments``.

Commands (``!`` or ``/`` prefix, optional ``@bot`` suffix):

* ``repost [caption]`` resends the first attachment using its ``file_id``.
* ``attachmentinfo`` replies with the attachment metadata.

The bot must be able to read the message, so disable privacy mode or make it an
administrator. This example does not download bytes; enable
``message.download_attachments`` in the program config to get ``local_path``.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def parse_command(text: str) -> tuple[str, str] | None:
    command, _separator, rest = text.partition(" ")
    if command.startswith("/") and "@" in command:
        command = command.split("@", 1)[0]
    for name in ("repost", "attachmentinfo"):
        if command in (f"!{name}", f"/{name}"):
            return name, rest.strip()
    return None


def reply(text: str, message_id: int) -> dict[str, object]:
    return {
        "action": "send_message",
        "text": text,
        "reply_to_message_id": message_id,
    }


def attachment_info(attachment: dict[str, Any]) -> str:
    lines = [
        f"type: {attachment.get('type')}",
        f"file_id: {attachment.get('file_id')}",
    ]
    for key in ("file_name", "mime_type", "file_size", "width", "height"):
        if attachment.get(key) is not None:
            lines.append(f"{key}: {attachment[key]}")
    if attachment.get("local_path") is not None:
        lines.append(f"local_path: {attachment['local_path']}")
    if attachment.get("download_error") is not None:
        lines.append(f"download_error: {attachment['download_error']}")
    return "\n".join(lines)


def main() -> int:
    event = json.load(sys.stdin)
    message = event.get("message")
    if not isinstance(message, dict):
        return 0

    # A command on a photo or document arrives as a caption, not text.
    text = message.get("text") or message.get("caption")
    if not isinstance(text, str):
        return 0

    parsed = parse_command(text)
    if parsed is None:
        return 0

    command, rest = parsed
    message_id = message["id"]
    attachments = message.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        payload: object = reply(
            "这条消息没有附件。请把命令写在图片或文件的标题里。",
            message_id,
        )
    elif command == "attachmentinfo":
        payload = reply(attachment_info(attachments[0]), message_id)
    else:
        attachment = attachments[0]
        caption = rest or "repost"
        if attachment.get("type") == "photo":
            payload = {
                "action": "send_photo",
                "photo": attachment["file_id"],
                "caption": caption,
            }
        else:
            payload = {
                "action": "send_document",
                "document": {"file_id": attachment["file_id"]},
                "caption": caption,
            }

    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
