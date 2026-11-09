"""Logging channels: stdout and the target group's General topic.

Application logs are written to stdout synchronously. A second, independent
channel mirrors selected records into Telegram through the Bot API. The
Telegram channel is deliberately *not* part of the update/action pipeline: it
has its own bounded queue and background task, so a slow or failing Telegram
API can never block polling or future action handlers.

Messages are sent without ``message_thread_id``. In a forum supergroup that
addresses the default General topic; in a regular supergroup it is simply the
main chat.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from telegram.error import RetryAfter

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

#: Bot API messages are limited to 4096 UTF-16 code units. Leave a small margin.
TELEGRAM_MESSAGE_LIMIT = 4000
#: Collect records for this long before sending one aggregate message.
TELEGRAM_BATCH_SECONDS = 2.0
#: Telegram allows roughly 20 messages per minute per group.
TELEGRAM_MIN_INTERVAL_SECONDS = 3.0
#: Bound the log transport so a broken network cannot grow memory forever.
TELEGRAM_QUEUE_CAPACITY = 1000
#: Maximum time spent flushing the final log batch on shutdown.
TELEGRAM_CLOSE_TIMEOUT_SECONDS = 10.0

_TELEGRAM_BACKOFF_MAX_SECONDS = 60.0
_TRUNCATED_SUFFIX = " ...[truncated]"

# Records from these loggers are excluded from the Telegram channel. They
# either describe the transport itself (and would create a feedback loop) or
# are too noisy for a chat. They remain visible on stdout.
_EXCLUDED_LOGGER_PREFIXES = ("httpx", "httpcore", "telegram")


class _SendMessageBot(Protocol):
    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Any: ...


def _utf16_length(text: str) -> int:
    """Return the number of UTF-16 code units Telegram counts."""

    return len(text.encode("utf-16-le")) // 2


def _truncate_utf16(text: str, limit: int) -> str:
    if _utf16_length(text) <= limit:
        return text
    budget = max(limit - _utf16_length(_TRUNCATED_SUFFIX), 0)
    encoded = text.encode("utf-16-le")[: budget * 2]
    return encoded.decode("utf-16-le", errors="ignore") + _TRUNCATED_SUFFIX


def _console_timestamp() -> str:
    return datetime.now().astimezone().strftime(LOG_DATE_FORMAT)


def _write_transport_warning(message: str) -> None:
    """Write a transport diagnostic without going through ``logging``.

    Using the logging framework here could feed the failure back into the
    Telegram queue. A direct write also keeps working when the logging system
    itself is shutting down.
    """

    try:
        print(
            f"{_console_timestamp()} WARNING  bottg.logging: {message}",
            file=sys.stdout,
            flush=True,
        )
    except Exception:  # pragma: no cover - stdout is gone or broken
        pass


def _retry_after_seconds(error: RetryAfter) -> float:
    value = error.retry_after
    if isinstance(value, timedelta):
        return value.total_seconds()
    return float(value)


class _RedactingFormatter(logging.Formatter):
    """Format a record and replace known secrets in the final text."""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        *,
        redact: Iterable[str] = (),
    ) -> None:
        super().__init__(fmt, datefmt)
        self._redact = tuple(secret for secret in redact if secret)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._redact:
            text = text.replace(secret, "<redacted>")
        return text


class TelegramLogHandler(logging.Handler):
    """A batching, non-blocking handler that posts logs to one Telegram chat.

    ``emit`` only formats and appends to an in-memory queue. A single asyncio
    task drains that queue, so logging never waits for the network. When the
    queue is full, the oldest record is discarded and the next batch reports
    how many records were lost.
    """

    def __init__(
        self,
        level: int | str = logging.INFO,
        *,
        capacity: int = TELEGRAM_QUEUE_CAPACITY,
        batch_seconds: float = TELEGRAM_BATCH_SECONDS,
        min_interval_seconds: float = TELEGRAM_MIN_INTERVAL_SECONDS,
        redact: Iterable[str] = (),
    ) -> None:
        super().__init__(level)
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if batch_seconds < 0:
            raise ValueError("batch_seconds must not be negative")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")

        self._capacity = capacity
        self._batch_seconds = batch_seconds
        self._min_interval_seconds = min_interval_seconds
        self._pending: deque[str] = deque()
        self._dropped = 0
        self._lock = threading.Lock()
        self._redact = tuple(secret for secret in redact if secret)
        self._formatter = _RedactingFormatter(
            LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT,
            redact=self._redact,
        )

        self._bot: _SendMessageBot | None = None
        self._chat_id: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._closing = False
        self._closed = False
        self._last_send = 0.0
        self._blocked_until = 0.0
        self._failure_count = 0

    # -- logging.Handler ---------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        if record.name.startswith(_EXCLUDED_LOGGER_PREFIXES):
            return

        try:
            text = self._formatter.format(record)
        except Exception:
            self.handleError(record)
            return

        try:
            text = self._redact_text(text)
            if _utf16_length(text) > TELEGRAM_MESSAGE_LIMIT:
                text = _truncate_utf16(text, TELEGRAM_MESSAGE_LIMIT)
        except (UnicodeError, ValueError):
            self.handleError(record)
            return

        with self._lock:
            if len(self._pending) >= self._capacity:
                self._pending.popleft()
                self._dropped += 1
            self._pending.append(text)

        self._wake_sender()

    def close(self) -> None:
        self._closed = True
        self._closing = True
        logging.getLogger().removeHandler(self)
        task = self._task
        loop = self._loop
        if (
            task is not None
            and not task.done()
            and loop is not None
            and not loop.is_closed()
        ):
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)
        super().close()

    # -- lifecycle ---------------------------------------------------------

    async def start(self, bot: _SendMessageBot, chat_id: int) -> None:
        """Bind the handler to a Bot and start the sender task.

        Records collected before this call (for example while the target group
        is being verified) are kept and sent once the task starts.
        """

        if self._task is not None:
            raise RuntimeError("Telegram log handler has already been started")
        if self._closed:
            raise RuntimeError("Telegram log handler has already been closed")

        self._bot = bot
        self._chat_id = chat_id
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task = self._loop.create_task(self._run(), name="bottg:telegram-log")
        self._wake.set()

    async def aclose(
        self, timeout: float = TELEGRAM_CLOSE_TIMEOUT_SECONDS
    ) -> None:
        """Flush pending records, stop the sender, and detach the handler."""

        if self._closed:
            return

        self._closed = True
        self._closing = True
        self._wake_sender()

        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                _write_transport_warning(
                    "Telegram log transport did not finish flushing before "
                    "the shutdown timeout; pending records were dropped"
                )
            except asyncio.CancelledError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise
            except Exception as exc:
                _write_transport_warning(
                    "Telegram log sender stopped with an error: "
                    f"{type(exc).__name__}: {self._redact_text(str(exc))}"
                )

        self._task = None
        self.detach()

    def detach(self) -> None:
        """Remove the handler from the root logger without awaiting a flush."""

        self._closed = True
        logging.getLogger().removeHandler(self)

    # -- sender ------------------------------------------------------------

    async def _run(self) -> None:
        assert self._wake is not None

        while True:
            try:
                await self._wake.wait()
                self._wake.clear()

                if self._closing:
                    await self._drain()
                    if not self._has_pending():
                        return
                    continue

                # Wait briefly so a burst of records becomes one chat message.
                await asyncio.sleep(self._batch_seconds)
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # Defensive: keep the channel alive.
                _write_transport_warning(
                    "Telegram log sender hit an unexpected error: "
                    f"{type(exc).__name__}: {self._redact_text(str(exc))}"
                )
                if self._closing:
                    return
                await asyncio.sleep(self._batch_seconds)
                if self._has_pending():
                    self._wake.set()

    def _wake_sender(self) -> None:
        loop = self._loop
        wake = self._wake
        if loop is None or wake is None:
            return
        if loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            # The loop was closed between the check and the call.
            return

    async def _drain(self) -> None:
        while True:
            text = self._take_batch()
            if text is None:
                return
            await self._wait_before_send()
            await self._send(text)

    async def _wait_before_send(self) -> None:
        if self._closing:
            min_interval_delay = 0.0
        else:
            min_interval_delay = (
                self._last_send + self._min_interval_seconds - time.monotonic()
            )
        delay = max(min_interval_delay, self._blocked_until - time.monotonic())
        if delay > 0:
            await asyncio.sleep(delay)

    async def _send(self, text: str) -> None:
        bot = self._bot
        chat_id = self._chat_id
        if bot is None or chat_id is None:
            return

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                disable_notification=True,
                disable_web_page_preview=True,
            )
        except asyncio.CancelledError:
            raise
        except RetryAfter as exc:
            delay = _retry_after_seconds(exc)
            if self._closing or delay > _TELEGRAM_BACKOFF_MAX_SECONDS:
                _write_transport_warning(
                    "Telegram log message dropped because the bot is rate "
                    f"limited for another {delay:.1f}s"
                )
                self._last_send = time.monotonic()
                return

            _write_transport_warning(
                f"Telegram log rate limited; retrying in {delay:.1f}s"
            )
            try:
                await asyncio.sleep(delay)
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    disable_notification=True,
                    disable_web_page_preview=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as retry_exc:
                self._note_failure(retry_exc)
            else:
                self._note_success()
        except Exception as exc:
            self._note_failure(exc)
        else:
            self._note_success()

    def _note_success(self) -> None:
        self._last_send = time.monotonic()
        self._blocked_until = 0.0
        self._failure_count = 0

    def _note_failure(self, exc: Exception) -> None:
        self._last_send = time.monotonic()
        self._failure_count += 1
        delay = min(
            2.0 ** min(self._failure_count, 6),
            _TELEGRAM_BACKOFF_MAX_SECONDS,
        )
        self._blocked_until = time.monotonic() + delay
        detail = " ".join(self._redact_text(str(exc)).split())
        _write_transport_warning(
            "Telegram log delivery failed "
            f"({type(exc).__name__}: {detail}); dropping this batch and "
            f"retrying in {delay:.0f}s"
        )

    def _take_batch(self) -> str | None:
        with self._lock:
            dropped = self._dropped
            self._dropped = 0

            if not self._pending and not dropped:
                return None

            parts: list[str] = []
            used = 0

            if dropped:
                notice = (
                    f"[bottg] {dropped} log record(s) dropped because the "
                    "Telegram log buffer was full"
                )
                parts.append(notice)
                used = _utf16_length(notice)

            while self._pending:
                line = self._pending[0]
                size = _utf16_length(line) + (1 if parts else 0)
                if used + size > TELEGRAM_MESSAGE_LIMIT:
                    if not parts:
                        self._pending.popleft()
                        parts.append(
                            _truncate_utf16(line, TELEGRAM_MESSAGE_LIMIT)
                        )
                    break
                self._pending.popleft()
                parts.append(line)
                used += size

            return "\n".join(parts) if parts else None

    def _has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending) or self._dropped > 0

    def _redact_text(self, text: str) -> str:
        for secret in self._redact:
            text = text.replace(secret, "<redacted>")
        return text


@dataclass(frozen=True, slots=True)
class LoggingRuntime:
    """Handles created by :func:`configure_logging`."""

    telegram_handler: TelegramLogHandler | None = None

    def close(self) -> None:
        """Synchronously detach the Telegram handler."""

        if self.telegram_handler is not None:
            self.telegram_handler.detach()


def configure_logging(
    log_level: str,
    telegram_log_level: str | None,
    *,
    token: str | None = None,
) -> LoggingRuntime:
    """Install stdout logging and, optionally, the Telegram channel.

    ``log_level`` controls stdout. ``telegram_log_level`` controls the
    Telegram mirror and may be ``None``/``"OFF"`` to disable it.
    """

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        with suppress(Exception):
            handler.close()

    redact = (token,) if token else ()
    formatter = _RedactingFormatter(
        LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        redact=redact,
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(_level_number(log_level))
    console.setFormatter(formatter)
    root.addHandler(console)

    levels = [console.level]
    telegram_handler: TelegramLogHandler | None = None
    if telegram_log_level is not None:
        telegram_handler = TelegramLogHandler(
            level=_level_number(telegram_log_level),
            redact=redact,
        )
        root.addHandler(telegram_handler)
        levels.append(telegram_handler.level)

    root.setLevel(min(levels))

    for noisy_logger in ("httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    return LoggingRuntime(telegram_handler=telegram_handler)


def _level_number(level_name: str) -> int:
    normalized = level_name.upper()
    try:
        return logging.getLevelNamesMapping()[normalized]
    except KeyError as exc:  # Defensive: config validation catches this.
        raise ValueError(f"unknown logging level: {level_name!r}") from exc


__all__ = [
    "LOG_DATE_FORMAT",
    "LOG_FORMAT",
    "LoggingRuntime",
    "TelegramLogHandler",
    "configure_logging",
]
