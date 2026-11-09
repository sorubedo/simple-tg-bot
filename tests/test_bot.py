from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from telegram import Chat
from telegram.constants import ChatType

from bottg.bot import TargetGroup, build_application, classify_target_chat, run_bot
from bottg.config import AppConfig, PollingConfig, TelegramConfig
from bottg.errors import StartupError
from bottg.logging_setup import TelegramLogHandler


def test_supergroup_is_accepted() -> None:
    chat = Chat(
        id=-1001234567890,
        type=ChatType.SUPERGROUP,
        title="Ops",
        username="ops_group",
        is_forum=True,
    )

    target = classify_target_chat(chat)

    assert target.id == -1001234567890
    assert target.title == "Ops"
    assert target.username == "ops_group"
    assert target.is_forum is True


def test_supergroup_without_topics_is_allowed_but_warned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    chat = Chat(
        id=-1001234567890,
        type=ChatType.SUPERGROUP,
        title="No Topics",
        is_forum=None,
    )

    with caplog.at_level(logging.WARNING, logger="bottg.bot"):
        target = classify_target_chat(chat)

    assert target.is_forum is False
    assert "Topics" in caplog.text


@pytest.mark.parametrize("chat_type", [ChatType.GROUP, ChatType.CHANNEL])
def test_non_supergroup_is_rejected(chat_type: ChatType) -> None:
    chat = Chat(id=-1001234567890, type=chat_type, title="Wrong type")

    with pytest.raises(StartupError, match="not a supergroup"):
        classify_target_chat(chat)


def test_application_processes_updates_sequentially_and_bounds_its_queue() -> None:
    config = AppConfig(
        telegram=TelegramConfig(
            token="123456:test-secret",
            group_id=-1001234567890,
        ),
        polling=PollingConfig(max_pending_updates=7),
    )

    app = build_application(config)

    assert app.update_queue.maxsize == 7
    assert app.concurrent_updates == 1


def test_zero_max_pending_updates_means_unbounded() -> None:
    config = AppConfig(
        telegram=TelegramConfig(
            token="123456:test-secret",
            group_id=-1001234567890,
        ),
        polling=PollingConfig(max_pending_updates=0),
    )

    app = build_application(config)

    assert app.update_queue.maxsize == 0


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, dict[str, Any]]] = []

    async def send_message(
        self, chat_id: int, text: str, **kwargs: Any
    ) -> object:
        self.messages.append((chat_id, text, kwargs))
        return object()


class FakeUpdater:
    def __init__(self) -> None:
        self.running = False
        self.polling_kwargs: dict[str, Any] = {}

    async def start_polling(self, **kwargs: Any) -> None:
        self.polling_kwargs = kwargs
        self.running = True

    async def stop(self) -> None:
        self.running = False


class FakeApplication:
    def __init__(self) -> None:
        self.bot = FakeBot()
        self.updater = FakeUpdater()
        self.running = False

    async def __aenter__(self) -> "FakeApplication":
        self.running = True
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        self.running = False
        return False

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False


def test_run_bot_starts_polling_and_flushes_telegram_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(
        telegram=TelegramConfig(
            token="123456:test-secret",
            group_id=-1001234567890,
        ),
        polling=PollingConfig(timeout_seconds=30),
    )
    app = FakeApplication()

    monkeypatch.setattr("bottg.bot.build_application", lambda _config: app)

    async def resolve_target(
        _app: Any, _group_id: int
    ) -> TargetGroup:
        return TargetGroup(
            id=-1001234567890,
            title="Ops",
            username="ops_group",
            is_forum=True,
        )

    monkeypatch.setattr("bottg.bot._resolve_target_group", resolve_target)

    def install_stop(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        # Release run_bot as soon as it reaches the stop_event wait.
        loop.call_soon(event.set)

    monkeypatch.setattr("bottg.bot._install_stop_handlers", install_stop)

    bot_logger = logging.getLogger("bottg.bot")
    previous_level = bot_logger.level
    previous_propagate = bot_logger.propagate
    bot_logger.setLevel(logging.INFO)
    bot_logger.propagate = False

    handler = TelegramLogHandler(
        level=logging.INFO,
        batch_seconds=0.01,
        min_interval_seconds=0.0,
    )
    bot_logger.addHandler(handler)
    try:
        asyncio.run(run_bot(config, telegram_logs=handler))
    finally:
        bot_logger.removeHandler(handler)
        handler.detach()
        bot_logger.setLevel(previous_level)
        bot_logger.propagate = previous_propagate

    assert app.updater.polling_kwargs["timeout"] == 30
    assert app.updater.running is False
    assert app.running is False

    text = "\n".join(message[1] for message in app.bot.messages)
    assert "verified target group" in text
    assert "initialized" in text
    assert "bottg stopped" in text
