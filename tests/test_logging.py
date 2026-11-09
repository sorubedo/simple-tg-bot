from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import Any
from uuid import uuid4

from bottg.logging_setup import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramLogHandler,
)


class FakeBot:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.messages: list[tuple[int, str, dict[str, Any]]] = []

    async def send_message(
        self, chat_id: int, text: str, **kwargs: Any
    ) -> object:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("simulated transport failure")
        self.messages.append((chat_id, text, kwargs))
        return object()


def make_handler(**kwargs: Any) -> tuple[logging.Logger, TelegramLogHandler]:
    handler = TelegramLogHandler(level=logging.DEBUG, **kwargs)
    logger = logging.getLogger(f"tests.logging.{uuid4().hex}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers[:] = [handler]
    return logger, handler


async def start_and_close(
    handler: TelegramLogHandler,
    bot: FakeBot,
    *,
    chat_id: int = -1001234567890,
) -> None:
    await handler.start(bot, chat_id)
    await handler.aclose(timeout=2)


def test_batches_records_into_one_general_topic_message() -> None:
    bot = FakeBot()
    logger, handler = make_handler(
        batch_seconds=0.01,
        min_interval_seconds=0.0,
    )
    try:
        logger.info("first line")
        logger.warning("second line")
        asyncio.run(start_and_close(handler, bot))
    finally:
        logger.removeHandler(handler)
        handler.detach()

    assert len(bot.messages) == 1
    chat_id, text, kwargs = bot.messages[0]
    assert chat_id == -1001234567890
    assert "first line" in text
    assert "second line" in text
    # No message_thread_id means the General topic in a forum supergroup.
    assert "message_thread_id" not in kwargs
    assert kwargs["disable_notification"] is True


def test_library_loggers_are_excluded_and_secrets_are_redacted() -> None:
    bot = FakeBot()
    logger, handler = make_handler(
        batch_seconds=0.01,
        min_interval_seconds=0.0,
        redact=("123:secret",),
    )
    httpx_logger = logging.getLogger("httpx")
    telegram_logger = logging.getLogger("telegram.Bot")
    updater_logger = logging.getLogger("telegram.ext.Updater")
    saved = [
        (httpx_logger, httpx_logger.level, httpx_logger.handlers[:]),
        (telegram_logger, telegram_logger.level, telegram_logger.handlers[:]),
        (updater_logger, updater_logger.level, updater_logger.handlers[:]),
    ]

    try:
        for library_logger in (httpx_logger, telegram_logger, updater_logger):
            library_logger.setLevel(logging.DEBUG)
            library_logger.propagate = False
            library_logger.handlers[:] = [handler]

        logger.info("token=123:secret")
        httpx_logger.info("HTTP Request: POST https://api.telegram.org")
        telegram_logger.warning("library chatter")
        updater_logger.error("polling conflict")

        asyncio.run(start_and_close(handler, bot))
    finally:
        logger.removeHandler(handler)
        handler.detach()
        for library_logger, level, handlers in saved:
            library_logger.setLevel(level)
            library_logger.handlers[:] = handlers

    assert len(bot.messages) == 1
    text = bot.messages[0][1]
    assert "<redacted>" in text
    assert "123:secret" not in text
    assert "HTTP Request" not in text
    assert "library chatter" not in text
    assert "polling conflict" in text


def test_queue_overflow_drops_oldest_and_reports_count() -> None:
    bot = FakeBot()
    logger, handler = make_handler(
        capacity=2,
        batch_seconds=0.01,
        min_interval_seconds=0.0,
    )
    try:
        for index in range(4):
            logger.info("record-%d", index)
        asyncio.run(start_and_close(handler, bot))
    finally:
        logger.removeHandler(handler)
        handler.detach()

    assert len(bot.messages) == 1
    text = bot.messages[0][1]
    assert "2 log record(s) dropped" in text
    assert "record-2" in text
    assert "record-3" in text
    assert "record-0" not in text
    assert "record-1" not in text


def test_long_record_is_truncated_to_telegram_limit() -> None:
    bot = FakeBot()
    logger, handler = make_handler(
        batch_seconds=0.01,
        min_interval_seconds=0.0,
    )
    try:
        logger.info("x" * 5000)
        asyncio.run(start_and_close(handler, bot))
    finally:
        logger.removeHandler(handler)
        handler.detach()

    assert len(bot.messages) == 1
    text = bot.messages[0][1]
    assert len(text.encode("utf-16-le")) // 2 <= TELEGRAM_MESSAGE_LIMIT
    assert text.endswith("...[truncated]")


def test_transport_failure_drops_batch_but_sender_keeps_running(
    capsys: Any,
) -> None:
    bot = FakeBot(failures=1)
    logger, handler = make_handler(
        batch_seconds=0.01,
        min_interval_seconds=0.0,
    )

    async def scenario() -> None:
        logger.info("lost batch")
        await handler.start(bot, -1001234567890)
        # Let the sender pick up and fail the first batch.
        await asyncio.sleep(0.05)
        logger.info("next batch")
        await handler.aclose(timeout=2)

    try:
        asyncio.run(scenario())
    finally:
        logger.removeHandler(handler)
        handler.detach()

    assert len(bot.messages) == 1
    assert "next batch" in bot.messages[0][1]
    output = capsys.readouterr().out
    assert "delivery failed" in output


def test_configure_logging_writes_to_stdout_and_can_disable_telegram() -> None:
    script = (
        "import logging\n"
        "from bottg.logging_setup import configure_logging\n"
        "runtime = configure_logging('INFO', None, token='123:secret')\n"
        "assert runtime.telegram_handler is None\n"
        "logging.getLogger('bottg.test').info('hello stdout token=123:secret')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "hello stdout" in result.stdout
    assert "bottg.test" in result.stdout
    assert "123:secret" not in result.stdout
    assert "<redacted>" in result.stdout
