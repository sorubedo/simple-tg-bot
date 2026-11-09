"""External hook programs and their stdin/stdout protocol.

Each configured program is executed as a child process with a JSON document on
stdin. The document describes the hook, the verified target group, the topic and
(for message-like hooks) the triggering message. The program may write an action
JSON document to stdout. Diagnostics belong on stderr.

The child's exit status is meaningful:

* exit 0 with empty stdout: success, no actions
* exit 0 with valid action JSON: queue those actions
* non-zero exit, timeout, or malformed stdout: report it and discard stdout

A program therefore never has to emit a "do nothing" action or manage a
cancellation token. Simple-TG-Bot decides from the process outcome and the
presence of stdout.

Hooks run sequentially in configuration order. Message and edited-message hooks
are already serialized by PTB's single-update processing, so a slow program
naturally applies backpressure instead of creating concurrent side effects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Final, Protocol

from telegram import Message
from telegram.error import TelegramError

from . import __version__
from .actions import Action, ActionContext, ActionPayloadError, parse_actions
from .config import (
    HOOK_EDITED_MESSAGE,
    HOOK_MESSAGE,
    HOOK_NAMES,
    ProgramConfig,
)
from .errors import ActionQueueError, ProgramExecutionError

LOGGER = logging.getLogger(__name__)

HOOK_PROTOCOL: Final = "simple-tg-bot.hook/1"
_MESSAGE_HOOKS: Final = frozenset({HOOK_MESSAGE, HOOK_EDITED_MESSAGE})
_STDERR_LIMIT: Final = 16384
_READ_CHUNK: Final = 65536
_TERMINATE_TIMEOUT: Final = 5.0


class GroupInfo(Protocol):
    """The verified target group fields exposed to hook programs."""

    id: int
    title: str
    username: str | None
    is_forum: bool


class ActionSink(Protocol):
    """Minimal interface required by the dispatcher."""

    async def enqueue(self, actions: Sequence[Action]) -> None: ...


class MediaFile(Protocol):
    """The subset of ``telegram.File`` used for downloads."""

    async def download_to_drive(self, custom_path: Path) -> Path: ...


class MediaBot(Protocol):
    """The subset of ``telegram.Bot`` used to download attachments."""

    async def get_file(self, file_id: str) -> MediaFile: ...


@dataclass(frozen=True, slots=True)
class ProgramResult:
    """Captured outcome of one program invocation."""

    returncode: int
    stdout: str
    stderr: str


class ProgramRunner:
    """Run one configured program and capture its output within limits."""

    def __init__(self, *, stderr_limit: int = _STDERR_LIMIT) -> None:
        if stderr_limit < 1:
            raise ValueError("stderr_limit must be at least 1")
        self._stderr_limit = stderr_limit

    async def run(
        self, program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        stdin_bytes = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        environment = os.environ.copy()
        environment.update(program.env)

        try:
            process = await asyncio.create_subprocess_exec(
                *program.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(program.cwd),
                env=environment,
            )
        except FileNotFoundError as exc:
            raise ProgramExecutionError(
                f"cannot start {program.command[0]!r}: executable not found"
            ) from exc
        except OSError as exc:
            detail = exc.strerror or str(exc)
            raise ProgramExecutionError(
                f"cannot start {program.command[0]!r}: {detail}"
            ) from exc

        try:
            (
                stdout,
                stderr,
                stdout_truncated,
                stderr_truncated,
            ) = await asyncio.wait_for(
                self._communicate(
                    process,
                    stdin_bytes,
                    program.max_output_bytes,
                ),
                timeout=program.timeout_seconds,
            )
        except TimeoutError as exc:
            await self._terminate(process)
            raise ProgramExecutionError(
                f"timed out after {program.timeout_seconds:g}s"
            ) from exc
        except asyncio.CancelledError:
            await self._terminate(process)
            raise
        except Exception:
            await self._terminate(process)
            raise

        if stdout_truncated:
            raise ProgramExecutionError(
                f"stdout exceeded {program.max_output_bytes} bytes"
            )
        try:
            stdout_text = stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProgramExecutionError("stdout is not valid UTF-8") from exc

        stderr_text = stderr.decode("utf-8", errors="replace")
        if stderr_truncated:
            stderr_text += "\n...[truncated]"

        return ProgramResult(
            returncode=int(process.returncode or 0),
            stdout=stdout_text,
            stderr=stderr_text,
        )

    async def _communicate(
        self,
        process: asyncio.subprocess.Process,
        stdin_bytes: bytes,
        stdout_limit: int,
    ) -> tuple[bytes, bytes, bool, bool]:
        stdin_writer = process.stdin
        stdout_reader = process.stdout
        stderr_reader = process.stderr
        if (
            stdin_writer is None
            or stdout_reader is None
            or stderr_reader is None
        ):  # pragma: no cover - pipes are always created above
            raise ProgramExecutionError(
                "internal error: subprocess pipes are unavailable"
            )

        async def write_stdin() -> None:
            try:
                stdin_writer.write(stdin_bytes)
                await stdin_writer.drain()
            except OSError:
                pass
            finally:
                with suppress(Exception):
                    stdin_writer.close()

        stdout_task = asyncio.create_task(
            _read_limited(stdout_reader, stdout_limit)
        )
        stderr_task = asyncio.create_task(
            _read_limited(stderr_reader, self._stderr_limit)
        )
        stdin_task = asyncio.create_task(write_stdin())
        wait_task = asyncio.create_task(process.wait())
        tasks = (stdin_task, stdout_task, stderr_task, wait_task)

        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        stdout, stdout_truncated = stdout_task.result()
        stderr, stderr_truncated = stderr_task.result()
        return stdout, stderr, stdout_truncated, stderr_truncated

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            with suppress(OSError):
                process.kill()
        try:
            await asyncio.wait_for(
                process.wait(), timeout=_TERMINATE_TIMEOUT
            )
        except TimeoutError:
            LOGGER.warning(
                "program process %s did not exit after being killed",
                process.pid,
            )
        except OSError:
            pass


class ProgramDispatcher:
    """Run matching programs for a hook and enqueue their actions."""

    def __init__(
        self,
        programs: Mapping[str, ProgramConfig],
        *,
        group: GroupInfo,
        sink: ActionSink,
        runner: ProgramRunner | None = None,
        media_bot: MediaBot | None = None,
    ) -> None:
        self._programs = tuple(programs.values())
        self._group = group
        self._sink = sink
        self._runner = runner or ProgramRunner()
        self._media_bot = media_bot

    @property
    def has_message_hooks(self) -> bool:
        return bool(self.required_update_types)

    @property
    def required_update_types(self) -> frozenset[str]:
        required: set[str] = set()
        for program in self._programs:
            if HOOK_MESSAGE in program.hooks:
                required.add(HOOK_MESSAGE)
            if HOOK_EDITED_MESSAGE in program.hooks:
                required.add(HOOK_EDITED_MESSAGE)
        return frozenset(required)

    async def dispatch_message(
        self, message: Message, *, update_id: int | None = None
    ) -> None:
        await self.run_hook(
            HOOK_MESSAGE,
            message=message,
            update_id=update_id,
        )

    async def dispatch_edited_message(
        self, message: Message, *, update_id: int | None = None
    ) -> None:
        await self.run_hook(
            HOOK_EDITED_MESSAGE,
            message=message,
            update_id=update_id,
        )

    async def run_hook(
        self,
        hook: str,
        *,
        message: Message | None = None,
        update_id: int | None = None,
        reason: str | None = None,
    ) -> None:
        if hook not in HOOK_NAMES:
            raise ValueError(f"unknown hook: {hook!r}")

        for program in self._programs:
            if hook not in program.hooks:
                continue
            if hook in _MESSAGE_HOOKS and not self._matches(
                program, message
            ):
                continue

            tempdir: tempfile.TemporaryDirectory[str] | None = None
            try:
                payload = build_hook_payload(
                    program=program,
                    group=self._group,
                    hook=hook,
                    message=message,
                    update_id=update_id,
                    reason=reason,
                )
                if (
                    hook in _MESSAGE_HOOKS
                    and message is not None
                    and program.message.download_attachments
                ):
                    tempdir = await self._download_attachments(
                        program, payload
                    )
                result = await self._runner.run(program, payload)
            except asyncio.CancelledError:
                raise
            except ProgramExecutionError as exc:
                LOGGER.warning(
                    "program %r failed during the %s hook: %s",
                    program.name,
                    hook,
                    exc,
                )
                continue
            except Exception:
                LOGGER.exception(
                    "unexpected failure while running program %r during the "
                    "%s hook",
                    program.name,
                    hook,
                )
                continue
            finally:
                if tempdir is not None:
                    with suppress(OSError):
                        tempdir.cleanup()

            if result.stderr:
                log = (
                    LOGGER.warning
                    if result.returncode != 0
                    else LOGGER.debug
                )
                log(
                    "program %r stderr:\n%s",
                    program.name,
                    result.stderr.rstrip(),
                )

            if result.returncode != 0:
                LOGGER.warning(
                    "program %r exited with status %d during the %s hook; "
                    "stdout actions discarded",
                    program.name,
                    result.returncode,
                    hook,
                )
                continue

            context = ActionContext(
                topic_id=_message_topic_id(message),
                message_id=message.message_id if message is not None else None,
                base_dir=program.cwd,
            )
            try:
                actions = parse_actions(result.stdout, context=context)
            except ActionPayloadError as exc:
                LOGGER.error(
                    "program %r returned invalid action JSON during the %s "
                    "hook: %s",
                    program.name,
                    hook,
                    exc,
                )
                continue
            except Exception:
                LOGGER.exception(
                    "unexpected failure while parsing stdout from program "
                    "%r during the %s hook",
                    program.name,
                    hook,
                )
                continue

            if not actions:
                LOGGER.debug(
                    "program %r produced no actions during the %s hook "
                    "(exit status 0)",
                    program.name,
                    hook,
                )
                continue

            try:
                await self._sink.enqueue(actions)
            except asyncio.CancelledError:
                raise
            except ActionQueueError as exc:
                LOGGER.error(
                    "actions from program %r were dropped: %s",
                    program.name,
                    exc,
                )
            except Exception:
                LOGGER.exception(
                    "unexpected failure while queuing actions from program "
                    "%r",
                    program.name,
                )
            else:
                LOGGER.debug(
                    "queued %d action(s) from program %r during the %s hook",
                    len(actions),
                    program.name,
                    hook,
                )

    async def _download_attachments(
        self,
        program: ProgramConfig,
        payload: Mapping[str, Any],
    ) -> tempfile.TemporaryDirectory[str] | None:
        """Download attachments for one program invocation.

        The returned temporary directory is removed by the caller after the
        program exits. Individual download failures are reported inside the
        event as ``download_error`` so the program can still run and decide
        what to do.
        """

        message = payload.get("message")
        if not isinstance(message, dict):
            return None
        attachments = message.get("attachments")
        if not isinstance(attachments, list) or not attachments:
            return None

        if self._media_bot is None:
            LOGGER.warning(
                "program %r requested attachment downloads, but no media "
                "client is configured; local_path will be null",
                program.name,
            )
            for attachment in attachments:
                if isinstance(attachment, dict):
                    attachment["download_error"] = "unavailable"
            return None

        tempdir: tempfile.TemporaryDirectory[str] = (
            tempfile.TemporaryDirectory(prefix="simple-tg-bot-media-")
        )
        try:
            for index, attachment in enumerate(attachments):
                if not isinstance(attachment, dict):
                    continue
                await self._download_attachment(
                    program=program,
                    attachment=attachment,
                    destination=Path(tempdir.name)
                    / _attachment_filename(index, attachment),
                )
        except BaseException:
            with suppress(OSError):
                tempdir.cleanup()
            raise
        return tempdir

    async def _download_attachment(
        self,
        *,
        program: ProgramConfig,
        attachment: dict[str, Any],
        destination: Path,
    ) -> None:
        file_id = attachment.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            attachment["download_error"] = "invalid_file_id"
            return

        file_size = attachment.get("file_size")
        limit = program.message.max_attachment_bytes
        if isinstance(file_size, int) and file_size > limit:
            attachment["download_error"] = "too_large"
            LOGGER.warning(
                "skipping attachment download for program %r: file is "
                "%d bytes, limit is %d",
                program.name,
                file_size,
                limit,
            )
            return

        assert self._media_bot is not None
        try:
            remote_file = await asyncio.wait_for(
                self._media_bot.get_file(file_id),
                timeout=program.timeout_seconds,
            )
            await asyncio.wait_for(
                remote_file.download_to_drive(destination),
                timeout=program.timeout_seconds,
            )
            actual_size = destination.stat().st_size
        except TimeoutError:
            attachment["download_error"] = "timeout"
            LOGGER.warning(
                "attachment download for program %r timed out after %gs",
                program.name,
                program.timeout_seconds,
            )
            with suppress(OSError):
                destination.unlink()
            return
        except TelegramError as exc:
            attachment["download_error"] = "telegram_error"
            LOGGER.warning(
                "attachment download for program %r failed: %s",
                program.name,
                _format_error(exc),
            )
            with suppress(OSError):
                destination.unlink()
            return
        except OSError as exc:
            attachment["download_error"] = "io_error"
            LOGGER.warning(
                "attachment download for program %r failed: %s",
                program.name,
                _format_error(exc),
            )
            with suppress(OSError):
                destination.unlink()
            return
        except Exception as exc:
            attachment["download_error"] = "failed"
            LOGGER.warning(
                "attachment download for program %r failed: %s",
                program.name,
                _format_error(exc),
            )
            with suppress(OSError):
                destination.unlink()
            return

        if actual_size > limit:
            attachment["download_error"] = "too_large"
            with suppress(OSError):
                destination.unlink()
            LOGGER.warning(
                "attachment download for program %r exceeded the %d-byte "
                "limit; discarded",
                program.name,
                limit,
            )
            return

        attachment["local_path"] = str(destination)

    def _matches(
        self, program: ProgramConfig, message: Message | None
    ) -> bool:
        if message is None:
            return False
        if message.chat.id != self._group.id:
            return False
        if program.message.ignore_bots and _is_bot_message(message):
            return False
        topics = program.message.topics
        if topics is not None and _message_topic_id(message) not in topics:
            return False
        return True


def build_hook_payload(
    *,
    program: ProgramConfig,
    group: GroupInfo,
    hook: str,
    message: Message | None = None,
    update_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build the JSON document written to a program's stdin."""

    topic_id = _message_topic_id(message)
    return {
        "protocol": HOOK_PROTOCOL,
        "hook": hook,
        "program": program.name,
        "bot": {"version": __version__},
        "group": {
            "id": group.id,
            "title": group.title,
            "username": group.username,
            "is_forum": group.is_forum,
        },
        "topic": {"id": topic_id},
        "message": _message_payload(message) if message is not None else None,
        "update_id": update_id,
        "reason": reason,
    }


def _message_payload(message: Message) -> dict[str, Any]:
    return {
        "id": message.message_id,
        "date": message.date.isoformat() if message.date is not None else None,
        "edit_date": (
            message.edit_date.isoformat()
            if message.edit_date is not None
            else None
        ),
        "text": message.text,
        "caption": message.caption,
        "attachments": _attachments_payload(message),
        "media_group_id": message.media_group_id,
        "sender": _sender_payload(message),
        "reply_to_message_id": (
            message.reply_to_message.message_id
            if message.reply_to_message is not None
            else None
        ),
        "message_thread_id": message.message_thread_id,
        "is_topic_message": bool(message.is_topic_message),
    }


def _attachments_payload(message: Message) -> list[dict[str, Any]]:
    """Describe the media contained in one Telegram message.

    Telegram puts one logical attachment in a message (albums are separate
    messages sharing ``media_group_id``). A photo carries several sizes; the
    largest is exposed because that is the version hooks usually want to
    process or re-upload.
    """

    attachments: list[dict[str, Any]] = []
    photo = message.photo
    if photo:
        attachments.append(
            _attachment_payload(
                "photo",
                max(
                    photo,
                    key=lambda item: (
                        (item.width or 0) * (item.height or 0),
                        item.file_size or 0,
                    ),
                ),
                mime_type="image/jpeg",
            )
        )

    animation = message.animation
    if animation is not None:
        # Telegram also populates ``document`` for backward compatibility.
        attachments.append(_attachment_payload("animation", animation))
    elif message.document is not None:
        attachments.append(_attachment_payload("document", message.document))

    if message.video is not None:
        attachments.append(_attachment_payload("video", message.video))
    if message.audio is not None:
        attachments.append(_attachment_payload("audio", message.audio))
    if message.voice is not None:
        attachments.append(_attachment_payload("voice", message.voice))
    if message.video_note is not None:
        attachments.append(_attachment_payload("video_note", message.video_note))
    if message.sticker is not None:
        attachments.append(_attachment_payload("sticker", message.sticker))
    return attachments


def _attachment_payload(
    kind: str, item: Any, *, mime_type: str | None = None
) -> dict[str, Any]:
    return {
        "type": kind,
        "file_id": item.file_id,
        "file_unique_id": item.file_unique_id,
        "file_name": getattr(item, "file_name", None),
        "mime_type": getattr(item, "mime_type", None) or mime_type,
        "file_size": getattr(item, "file_size", None),
        "width": getattr(item, "width", None),
        "height": getattr(item, "height", None),
        "duration": _duration_seconds(getattr(item, "duration", None)),
        # Filled only when the program sets message.download_attachments.
        "local_path": None,
        "download_error": None,
    }


def _duration_seconds(value: Any) -> int | float | None:
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _attachment_filename(
    index: int, attachment: Mapping[str, Any]
) -> str:
    raw_name = attachment.get("file_name")
    name = Path(raw_name).name if isinstance(raw_name, str) else ""
    name = "".join(
        character
        for character in name
        if character.isprintable() and character not in {"/", "\\"}
    )
    name = name.strip(" .")
    if not name:
        raw_type = attachment.get("type")
        name = raw_type if isinstance(raw_type, str) else "attachment"
    # Keep the complete name below common 255-byte filesystem limits even
    # when it contains multi-byte characters.
    name = name.encode("utf-8")[:200].decode("utf-8", errors="ignore")
    return f"{index:02d}-{name}"


def _sender_payload(message: Message) -> dict[str, Any] | None:
    chat = message.sender_chat
    if chat is not None:
        return {
            "type": "chat",
            "id": chat.id,
            "title": chat.title,
            "username": chat.username,
        }

    user = message.from_user
    if user is None:
        return None
    return {
        "type": "user",
        "id": user.id,
        "is_bot": bool(user.is_bot),
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def _message_topic_id(message: Message | None) -> int | None:
    return message.message_thread_id if message is not None else None


def _is_bot_message(message: Message) -> bool:
    if message.sender_chat is not None:
        return False
    user = message.from_user
    return user is not None and bool(user.is_bot)


def _format_error(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    if not detail:
        detail = type(exc).__name__
    return detail[:500]


async def _read_limited(
    stream: asyncio.StreamReader, limit: int
) -> tuple[bytes, bool]:
    """Read a stream to EOF while retaining at most *limit* bytes."""

    chunks: list[bytes] = []
    total = 0
    truncated = False
    while True:
        chunk = await stream.read(_READ_CHUNK)
        if not chunk:
            break
        if total < limit:
            keep = min(len(chunk), limit - total)
            chunks.append(chunk[:keep])
            total += keep
            if keep < len(chunk):
                truncated = True
        else:
            truncated = True
    return b"".join(chunks), truncated


__all__ = [
    "ActionSink",
    "GroupInfo",
    "HOOK_PROTOCOL",
    "ProgramDispatcher",
    "ProgramResult",
    "ProgramRunner",
    "build_hook_payload",
]
