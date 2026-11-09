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
cancellation token. bottg decides from the process outcome and the presence of
stdout.

Hooks run sequentially in configuration order. Message and edited-message hooks
are already serialized by PTB's single-update processing, so a slow program
naturally applies backpressure instead of creating concurrent side effects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Final, Protocol

from telegram import Message

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

HOOK_PROTOCOL: Final = "bottg.hook/1"
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
    ) -> None:
        self._programs = tuple(programs.values())
        self._group = group
        self._sink = sink
        self._runner = runner or ProgramRunner()

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

            try:
                payload = build_hook_payload(
                    program=program,
                    group=self._group,
                    hook=hook,
                    message=message,
                    update_id=update_id,
                    reason=reason,
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
        "bottg": {"version": __version__},
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
        "sender": _sender_payload(message),
        "reply_to_message_id": (
            message.reply_to_message.message_id
            if message.reply_to_message is not None
            else None
        ),
        "message_thread_id": message.message_thread_id,
        "is_topic_message": bool(message.is_topic_message),
    }


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
