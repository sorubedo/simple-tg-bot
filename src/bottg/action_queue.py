"""Serial, bounded action queue bound to the target group.

External hooks enqueue whole action batches. A single worker executes them in
FIFO order, which makes ordering deterministic and keeps Telegram calls out of
the update callback. ``sleep`` actions pause the queue, so a program can pace a
sequence without spawning another timer.

The queue is bounded. When it is full, producers wait for room (backpressure)
up to a configurable timeout; an enqueue that cannot fit is rejected as a
whole, never partially applied.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Protocol

from telegram import LinkPreviewOptions, ReactionTypeEmoji, ReplyParameters
from telegram.error import RetryAfter, TelegramError

from .actions import (
    Action,
    CreateTopicAction,
    DeleteMessageAction,
    DeleteTopicAction,
    EditTopicAction,
    FileSource,
    SendDocumentAction,
    SendMessageAction,
    SendPhotoAction,
    SetReactionAction,
    SleepAction,
    describe_action,
)
from .errors import ActionQueueError

LOGGER = logging.getLogger(__name__)

_MAX_RETRY_AFTER_ATTEMPTS: Final = 3
_MAX_RETRY_AFTER_SECONDS: Final = 120.0
_STOP: Final = object()


class ActionBot(Protocol):
    """The subset of ``telegram.Bot`` used by the action queue."""

    async def send_message(
        self, chat_id: int, text: str, **kwargs: Any
    ) -> Any: ...

    async def send_photo(
        self, chat_id: int, photo: str | Path, **kwargs: Any
    ) -> Any: ...

    async def send_document(
        self, chat_id: int, document: str | Path, **kwargs: Any
    ) -> Any: ...

    async def delete_message(
        self, chat_id: int, message_id: int, **kwargs: Any
    ) -> Any: ...

    async def set_message_reaction(
        self,
        chat_id: int,
        message_id: int,
        reaction: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    async def create_forum_topic(
        self, chat_id: int, name: str, **kwargs: Any
    ) -> Any: ...

    async def delete_forum_topic(
        self, chat_id: int, message_thread_id: int, **kwargs: Any
    ) -> Any: ...

    async def edit_forum_topic(
        self,
        chat_id: int,
        message_thread_id: int,
        name: str | None = None,
        **kwargs: Any,
    ) -> Any: ...


class ActionQueue:
    """Execute action batches one at a time against one Telegram chat."""

    def __init__(
        self,
        bot: ActionBot,
        chat_id: int,
        *,
        capacity: int = 100,
        min_interval_seconds: float = 0.0,
        enqueue_timeout_seconds: float = 10.0,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")
        if enqueue_timeout_seconds < 0:
            raise ValueError("enqueue_timeout_seconds must not be negative")

        self._bot = bot
        self._chat_id = chat_id
        self._capacity = capacity
        self._min_interval_seconds = min_interval_seconds
        self._enqueue_timeout_seconds = enqueue_timeout_seconds

        self._queue: asyncio.Queue[tuple[Action, ...] | object] = asyncio.Queue(
            maxsize=capacity
        )
        self._task: asyncio.Task[None] | None = None
        self._current_batch: tuple[Action, ...] | None = None
        self._last_action_at = 0.0
        self._processed = 0
        self._failed = 0
        self._closed = False

    @property
    def pending_batches(self) -> int:
        return self._queue.qsize()

    @property
    def processed_actions(self) -> int:
        return self._processed

    @property
    def failed_actions(self) -> int:
        return self._failed

    async def start(self) -> None:
        if self._closed:
            raise ActionQueueError("action queue has already been closed")
        if self._task is not None:
            raise RuntimeError("action queue has already been started")
        self._task = asyncio.create_task(self._run(), name="bottg:actions")

    async def enqueue(self, actions: Sequence[Action]) -> None:
        """Queue one batch atomically.

        If the queue stays full for longer than ``enqueue_timeout_seconds``,
        ``ActionQueueError`` is raised and none of the batch is queued.
        """

        if not actions:
            return
        if self._closed:
            raise ActionQueueError("action queue is closed")
        task = self._task
        if task is None:
            raise RuntimeError("action queue has not been started")
        if task.done():
            raise ActionQueueError("action queue worker has stopped")

        batch = tuple(actions)
        if self._enqueue_timeout_seconds <= 0:
            try:
                self._queue.put_nowait(batch)
            except asyncio.QueueFull as exc:
                raise ActionQueueError(
                    f"action queue is full ({self._capacity} pending batches)"
                ) from exc
            return

        try:
            await asyncio.wait_for(
                self._queue.put(batch),
                timeout=self._enqueue_timeout_seconds,
            )
        except TimeoutError as exc:
            raise ActionQueueError(
                f"action queue is full ({self._capacity} pending batches); "
                f"waited {self._enqueue_timeout_seconds:g}s"
            ) from exc

    async def close(self, *, timeout: float = 30.0) -> None:
        """Drain pending batches, then stop the worker.

        If draining exceeds *timeout*, the current action is cancelled and the
        remaining batches are dropped. This method is idempotent.
        """

        if self._closed:
            return
        self._closed = True

        task = self._task
        if task is None:
            return
        self._task = None

        if task.done():
            self._log_worker_result(task)
            return

        try:
            if timeout > 0:
                await asyncio.wait_for(self._queue.join(), timeout=timeout)
            elif self._queue.empty() and self._current_batch is None:
                await self._queue.join()
            else:
                raise TimeoutError
        except TimeoutError:
            pending = self._queue.qsize()
            current = self._current_batch
            detail = (
                f", current batch has {len(current)} action(s)"
                if current
                else ""
            )
            LOGGER.warning(
                "action queue did not drain within %.1fs; cancelling worker "
                "(%d batch(es) still queued%s)",
                timeout,
                pending,
                detail,
            )
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            return
        except asyncio.CancelledError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise

        # The queue is empty and every batch called task_done(). Wake the
        # worker so it can exit cleanly.
        try:
            self._queue.put_nowait(_STOP)
        except asyncio.QueueFull:  # Defensive: only possible on a late put.
            LOGGER.warning(
                "action queue received work during shutdown; cancelling "
                "worker"
            )
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            return
        with suppress(asyncio.CancelledError):
            await task
        LOGGER.debug(
            "action queue stopped (processed=%d, failed=%d)",
            self._processed,
            self._failed,
        )

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, tuple)
                self._current_batch = item
                for action in item:
                    await self._run_action(action)
            finally:
                self._current_batch = None
                self._queue.task_done()

    async def _run_action(self, action: Action) -> None:
        try:
            await self._execute_with_retry(action)
        except asyncio.CancelledError:
            raise
        except TelegramError as exc:
            self._failed += 1
            LOGGER.warning(
                "action %s failed: %s",
                describe_action(action),
                _format_error(exc),
            )
        except Exception:
            self._failed += 1
            LOGGER.exception(
                "unexpected failure while executing %s",
                describe_action(action),
            )
        else:
            self._processed += 1
            LOGGER.debug("executed %s", describe_action(action))

    async def _execute_with_retry(self, action: Action) -> None:
        attempt = 0
        while True:
            try:
                await self._execute(action)
                return
            except RetryAfter as exc:
                attempt += 1
                delay = max(0.0, _retry_after_seconds(exc))
                if (
                    attempt >= _MAX_RETRY_AFTER_ATTEMPTS
                    or delay > _MAX_RETRY_AFTER_SECONDS
                ):
                    raise
                LOGGER.warning(
                    "Telegram rate limit hit while executing %s; retrying in "
                    "%.1fs",
                    describe_action(action),
                    delay,
                )
                await asyncio.sleep(delay)

    async def _execute(self, action: Action) -> None:
        if isinstance(action, SleepAction):
            await asyncio.sleep(action.seconds)
            return

        await self._throttle()
        try:
            if isinstance(action, SendMessageAction):
                await self._send_message(action)
            elif isinstance(action, SendPhotoAction):
                await self._send_photo(action)
            elif isinstance(action, SendDocumentAction):
                await self._send_document(action)
            elif isinstance(action, DeleteMessageAction):
                await self._bot.delete_message(
                    chat_id=self._chat_id,
                    message_id=action.message_id,
                )
            elif isinstance(action, SetReactionAction):
                kwargs: dict[str, Any] = {}
                if action.is_big:
                    kwargs["is_big"] = True
                await self._bot.set_message_reaction(
                    chat_id=self._chat_id,
                    message_id=action.message_id,
                    reaction=[ReactionTypeEmoji(emoji=action.emoji)],
                    **kwargs,
                )
            elif isinstance(action, CreateTopicAction):
                forum_topic = await self._bot.create_forum_topic(
                    chat_id=self._chat_id,
                    name=action.name,
                )
                topic_id = getattr(forum_topic, "message_thread_id", None)
                LOGGER.info(
                    "created forum topic %r (topic_id=%s)",
                    action.name,
                    topic_id,
                )
            elif isinstance(action, DeleteTopicAction):
                await self._bot.delete_forum_topic(
                    chat_id=self._chat_id,
                    message_thread_id=action.topic_id,
                )
                LOGGER.info("deleted forum topic %s", action.topic_id)
            elif isinstance(action, EditTopicAction):
                await self._bot.edit_forum_topic(
                    chat_id=self._chat_id,
                    message_thread_id=action.topic_id,
                    name=action.name,
                )
                LOGGER.info(
                    "renamed forum topic %s to %r",
                    action.topic_id,
                    action.name,
                )
            else:  # Defensive: the parser only returns known action types.
                raise TypeError(f"unsupported action type: {type(action)!r}")
        finally:
            self._last_action_at = time.monotonic()

    async def _send_message(self, action: SendMessageAction) -> None:
        kwargs = _target_kwargs(
            topic_id=action.topic_id,
            reply_to_message_id=action.reply_to_message_id,
            disable_notification=action.disable_notification,
        )
        if action.parse_mode is not None:
            kwargs["parse_mode"] = action.parse_mode
        if action.disable_web_page_preview:
            kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)

        await self._bot.send_message(
            chat_id=self._chat_id,
            text=action.text,
            **kwargs,
        )

    async def _send_photo(self, action: SendPhotoAction) -> None:
        kwargs = _media_kwargs(
            topic_id=action.topic_id,
            reply_to_message_id=action.reply_to_message_id,
            disable_notification=action.disable_notification,
            caption=action.caption,
            parse_mode=action.parse_mode,
        )
        if action.has_spoiler:
            kwargs["has_spoiler"] = True
        await self._bot.send_photo(
            chat_id=self._chat_id,
            photo=_file_input(action.photo),
            **kwargs,
        )

    async def _send_document(self, action: SendDocumentAction) -> None:
        kwargs = _media_kwargs(
            topic_id=action.topic_id,
            reply_to_message_id=action.reply_to_message_id,
            disable_notification=action.disable_notification,
            caption=action.caption,
            parse_mode=action.parse_mode,
        )
        await self._bot.send_document(
            chat_id=self._chat_id,
            document=_file_input(action.document),
            **kwargs,
        )

    async def _throttle(self) -> None:
        if self._min_interval_seconds <= 0:
            return
        delay = (
            self._last_action_at
            + self._min_interval_seconds
            - time.monotonic()
        )
        if delay > 0:
            await asyncio.sleep(delay)

    def _log_worker_result(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            LOGGER.error(
                "action worker stopped unexpectedly: %s",
                _format_error(exc),
            )


def _target_kwargs(
    *,
    topic_id: int | None,
    reply_to_message_id: int | None,
    disable_notification: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if topic_id is not None:
        kwargs["message_thread_id"] = topic_id
    if reply_to_message_id is not None:
        kwargs["reply_parameters"] = ReplyParameters(
            message_id=reply_to_message_id
        )
    if disable_notification:
        kwargs["disable_notification"] = True
    return kwargs


def _media_kwargs(
    *,
    topic_id: int | None,
    reply_to_message_id: int | None,
    disable_notification: bool,
    caption: str | None,
    parse_mode: str | None,
) -> dict[str, Any]:
    kwargs = _target_kwargs(
        topic_id=topic_id,
        reply_to_message_id=reply_to_message_id,
        disable_notification=disable_notification,
    )
    if caption is not None:
        kwargs["caption"] = caption
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    return kwargs


def _file_input(source: FileSource) -> str | Path:
    if source.kind == "path":
        path = Path(source.value)
        if not path.is_file():
            raise FileNotFoundError(
                f"local media file is not readable: {path.name}"
            )
        return path
    return source.value


def _retry_after_seconds(error: RetryAfter) -> float:
    value = error.retry_after
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (value - datetime.now(timezone.utc)).total_seconds()
    return float(value)


def _format_error(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    if not detail:
        detail = type(exc).__name__
    return detail[:500]


__all__ = ["ActionBot", "ActionQueue"]
