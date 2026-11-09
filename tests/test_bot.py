from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from telegram import Chat, Message, Update, User
from telegram.constants import ChatType

from simple_tg_bot.bot import (
    TargetGroup,
    _make_message_handler,
    build_application,
    classify_target_chat,
    run_bot,
)
from simple_tg_bot.config import (
    ActionsConfig,
    AppConfig,
    HOOK_SHUTDOWN,
    HOOK_STARTUP,
    PollingConfig,
    ProgramConfig,
    TelegramConfig,
)
from simple_tg_bot.errors import StartupError
from simple_tg_bot.logging_setup import TelegramLogHandler


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

    with caplog.at_level(logging.WARNING, logger="simple_tg_bot.bot"):
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
        self.id = 123456
        self.messages: list[tuple[int, str, dict[str, Any]]] = []
        self.can_read_all_group_messages = False
        self.group_member_status = "administrator"

    async def send_message(
        self, chat_id: int, text: str, **kwargs: Any
    ) -> object:
        self.messages.append((chat_id, text, kwargs))
        return object()

    async def get_chat_member(
        self, chat_id: int, user_id: int, **kwargs: Any
    ) -> SimpleNamespace:
        return SimpleNamespace(status=self.group_member_status)


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
        self.handlers: list[Any] = []

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

    def add_handler(self, handler: Any) -> None:
        self.handlers.append(handler)


class FakeDispatcher:
    instances: list["FakeDispatcher"] = []
    required_types: frozenset[str] = frozenset({"message"})

    def __init__(
        self,
        programs: Any,
        *,
        group: Any,
        sink: Any,
        media_bot: Any = None,
    ) -> None:
        self.programs = programs
        self.group = group
        self.sink = sink
        self.media_bot = media_bot
        self.hooks: list[tuple[str, dict[str, Any]]] = []
        FakeDispatcher.instances.append(self)

    @property
    def has_message_hooks(self) -> bool:
        return bool(self.required_update_types)

    @property
    def required_update_types(self) -> frozenset[str]:
        return self.required_types

    async def run_hook(self, hook: str, **kwargs: Any) -> None:
        self.hooks.append((hook, kwargs))

    async def dispatch_message(
        self, message: Any, *, update_id: int | None = None
    ) -> None:
        pass

    async def dispatch_edited_message(
        self, message: Any, *, update_id: int | None = None
    ) -> None:
        pass


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

    monkeypatch.setattr("simple_tg_bot.bot.build_application", lambda _config: app)

    async def resolve_target(
        _app: Any, _group_id: int
    ) -> TargetGroup:
        return TargetGroup(
            id=-1001234567890,
            title="Ops",
            username="ops_group",
            is_forum=True,
        )

    monkeypatch.setattr("simple_tg_bot.bot._resolve_target_group", resolve_target)

    def install_stop(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        # Release run_bot as soon as it reaches the stop_event wait.
        loop.call_soon(event.set)

    monkeypatch.setattr("simple_tg_bot.bot._install_stop_handlers", install_stop)

    bot_logger = logging.getLogger("simple_tg_bot.bot")
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
    assert app.updater.polling_kwargs["allowed_updates"] == []
    assert app.updater.running is False
    assert app.running is False

    text = "\n".join(message[1] for message in app.bot.messages)
    assert "verified target group" in text
    assert "initialized" in text
    assert "Simple-TG-Bot stopped" in text


def test_run_bot_runs_startup_and_shutdown_hooks(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = AppConfig(
        telegram=TelegramConfig(
            token="123456:test-secret",
            group_id=-1001234567890,
        ),
        polling=PollingConfig(allowed_updates=("callback_query",)),
    )
    app = FakeApplication()
    FakeDispatcher.instances.clear()

    monkeypatch.setattr("simple_tg_bot.bot.build_application", lambda _config: app)
    monkeypatch.setattr("simple_tg_bot.bot.ProgramDispatcher", FakeDispatcher)

    async def resolve_target(
        _app: Any, _group_id: int
    ) -> TargetGroup:
        return TargetGroup(
            id=-1001234567890,
            title="Ops",
            username="ops_group",
            is_forum=True,
        )

    monkeypatch.setattr("simple_tg_bot.bot._resolve_target_group", resolve_target)

    def install_stop(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        loop.call_soon(event.set)

    monkeypatch.setattr("simple_tg_bot.bot._install_stop_handlers", install_stop)

    with caplog.at_level(logging.WARNING, logger="simple_tg_bot.bot"):
        asyncio.run(run_bot(config))

    dispatcher = FakeDispatcher.instances[0]
    assert [hook for hook, _ in dispatcher.hooks] == [
        "startup",
        "shutdown",
    ]
    assert dispatcher.hooks[1][1]["reason"] == "signal"
    assert dispatcher.media_bot is app.bot
    assert len(app.handlers) == 1
    assert "excludes 'message'" in caplog.text


def test_run_bot_executes_startup_hook_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    program = ProgramConfig(
        name="startup",
        command=(
            sys.executable,
            "-c",
            "print('{\"action\": \"send_message\", "
            "\"text\": \"started\"}')",
        ),
        hooks=frozenset({HOOK_STARTUP}),
        cwd=tmp_path,
    )
    config = AppConfig(
        telegram=TelegramConfig(
            token="123456:test-secret",
            group_id=-1001234567890,
        ),
        actions=ActionsConfig(drain_timeout_seconds=5),
        programs=MappingProxyType({"startup": program}),
    )
    app = FakeApplication()

    monkeypatch.setattr("simple_tg_bot.bot.build_application", lambda _config: app)

    async def resolve_target(
        _app: Any, _group_id: int
    ) -> TargetGroup:
        return TargetGroup(
            id=-1001234567890,
            title="Ops",
            username="ops_group",
            is_forum=True,
        )

    monkeypatch.setattr("simple_tg_bot.bot._resolve_target_group", resolve_target)

    def install_stop(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        loop.call_soon(event.set)

    monkeypatch.setattr("simple_tg_bot.bot._install_stop_handlers", install_stop)

    asyncio.run(run_bot(config))

    assert [message[1] for message in app.bot.messages] == ["started"]


def test_run_bot_executes_shutdown_hook_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    program = ProgramConfig(
        name="shutdown",
        command=(
            sys.executable,
            "-c",
            "print('{\"action\": \"send_message\", "
            "\"text\": \"stopped\"}')",
        ),
        hooks=frozenset({HOOK_SHUTDOWN}),
        cwd=tmp_path,
    )
    config = AppConfig(
        telegram=TelegramConfig(
            token="123456:test-secret",
            group_id=-1001234567890,
        ),
        actions=ActionsConfig(drain_timeout_seconds=5),
        programs=MappingProxyType({"shutdown": program}),
    )
    app = FakeApplication()

    monkeypatch.setattr("simple_tg_bot.bot.build_application", lambda _config: app)

    async def resolve_target(
        _app: Any, _group_id: int
    ) -> TargetGroup:
        return TargetGroup(
            id=-1001234567890,
            title="Ops",
            username="ops_group",
            is_forum=True,
        )

    monkeypatch.setattr("simple_tg_bot.bot._resolve_target_group", resolve_target)

    def install_stop(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        loop.call_soon(event.set)

    monkeypatch.setattr("simple_tg_bot.bot._install_stop_handlers", install_stop)

    asyncio.run(run_bot(config))

    assert [message[1] for message in app.bot.messages] == ["stopped"]


def test_run_bot_warns_when_privacy_mode_blocks_messages(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = AppConfig(
        telegram=TelegramConfig(
            token="123456:test-secret",
            group_id=-1001234567890,
        ),
    )
    app = FakeApplication()
    app.bot.group_member_status = "member"

    monkeypatch.setattr("simple_tg_bot.bot.build_application", lambda _config: app)

    async def resolve_target(
        _app: Any, _group_id: int
    ) -> TargetGroup:
        return TargetGroup(
            id=-1001234567890,
            title="Ops",
            username="ops_group",
            is_forum=True,
        )

    monkeypatch.setattr("simple_tg_bot.bot._resolve_target_group", resolve_target)

    def install_stop(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        loop.call_soon(event.set)

    monkeypatch.setattr("simple_tg_bot.bot._install_stop_handlers", install_stop)

    with caplog.at_level(logging.WARNING, logger="simple_tg_bot.bot"):
        asyncio.run(run_bot(config))

    assert "privacy mode" in caplog.text


def test_run_bot_registers_edited_message_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AppConfig(
        telegram=TelegramConfig(
            token="123456:test-secret",
            group_id=-1001234567890,
        ),
    )
    app = FakeApplication()
    FakeDispatcher.instances.clear()
    previous_types = FakeDispatcher.required_types
    FakeDispatcher.required_types = frozenset(
        {"message", "edited_message"}
    )

    monkeypatch.setattr("simple_tg_bot.bot.build_application", lambda _config: app)
    monkeypatch.setattr("simple_tg_bot.bot.ProgramDispatcher", FakeDispatcher)

    async def resolve_target(
        _app: Any, _group_id: int
    ) -> TargetGroup:
        return TargetGroup(
            id=-1001234567890,
            title="Ops",
            username="ops_group",
            is_forum=True,
        )

    monkeypatch.setattr("simple_tg_bot.bot._resolve_target_group", resolve_target)

    def install_stop(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        loop.call_soon(event.set)

    monkeypatch.setattr("simple_tg_bot.bot._install_stop_handlers", install_stop)

    try:
        asyncio.run(run_bot(config))
    finally:
        FakeDispatcher.required_types = previous_types

    assert len(app.handlers) == 2


def test_make_message_handler_dispatches_edited_messages() -> None:
    class Dispatcher:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int | None]] = []

        async def dispatch_message(
            self, message: Any, *, update_id: int | None = None
        ) -> None:
            self.calls.append(("message", update_id))

        async def dispatch_edited_message(
            self, message: Any, *, update_id: int | None = None
        ) -> None:
            self.calls.append(("edited_message", update_id))

    dispatcher = Dispatcher()
    handler = _make_message_handler(dispatcher, edited=True)  # type: ignore[arg-type]
    chat = Chat(
        id=-1001234567890,
        type=ChatType.SUPERGROUP,
        title="Ops",
        is_forum=True,
    )
    message = Message(
        message_id=9,
        date=datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc),
        edit_date=datetime(2026, 11, 9, 12, 1, tzinfo=timezone.utc),
        chat=chat,
        from_user=User(id=42, first_name="Alice", is_bot=False),
        text="edited",
        message_thread_id=7,
        is_topic_message=True,
    )
    update = Update(update_id=99, edited_message=message)

    asyncio.run(handler(update, None))  # type: ignore[arg-type]

    assert dispatcher.calls == [("edited_message", 99)]
