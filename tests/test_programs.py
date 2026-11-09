from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from telegram import Chat, Message, User
from telegram.constants import ChatType

from bottg.action_queue import ActionQueue
from bottg.actions import SendMessageAction, SleepAction
from bottg.config import (
    HOOK_EDITED_MESSAGE,
    HOOK_MESSAGE,
    HOOK_SHUTDOWN,
    HOOK_STARTUP,
    MessageHookConfig,
    ProgramConfig,
)
from bottg.errors import ProgramExecutionError
from bottg.programs import (
    HOOK_PROTOCOL,
    ProgramDispatcher,
    ProgramResult,
    ProgramRunner,
    build_hook_payload,
)


@dataclass(frozen=True, slots=True)
class Group:
    id: int = -1001234567890
    title: str = "Ops"
    username: str | None = "ops_group"
    is_forum: bool = True


def make_program(
    name: str = "hook",
    *,
    hooks: frozenset[str] = frozenset({HOOK_MESSAGE}),
    message: MessageHookConfig | None = None,
    command: tuple[str, ...] = ("/bin/true",),
    cwd: Path = Path("."),
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = 5.0,
    max_output_bytes: int = 65536,
) -> ProgramConfig:
    return ProgramConfig(
        name=name,
        command=command,
        hooks=hooks,
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        env=MappingProxyType(dict(env or {})),
        message=message or MessageHookConfig(),
        max_output_bytes=max_output_bytes,
    )


def make_message(
    *,
    text: str = "hello",
    message_thread_id: int | None = 7,
    chat_id: int = -1001234567890,
    edit_date: datetime | None = None,
    from_user: User | None = User(
        id=42, first_name="Alice", is_bot=False, username="alice"
    ),
    sender_chat: Chat | None = None,
) -> Message:
    chat = Chat(
        id=chat_id,
        type=ChatType.SUPERGROUP,
        title="Ops",
        username="ops_group",
        is_forum=True,
    )
    return Message(
        message_id=99,
        date=datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc),
        chat=chat,
        edit_date=edit_date,
        from_user=from_user,
        text=text,
        sender_chat=sender_chat,
        message_thread_id=message_thread_id,
        is_topic_message=message_thread_id is not None,
    )


class FakeRunner:
    def __init__(self, outcomes: dict[str, Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run(
        self, program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        self.calls.append((program.name, dict(payload)))
        outcome = self.outcomes[program.name]
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome(program, payload)
        return outcome


class FakeSink:
    def __init__(self) -> None:
        self.batches: list[list[Any]] = []

    async def enqueue(self, actions: Any) -> None:
        self.batches.append(list(actions))


def test_message_payload_contains_trigger_context() -> None:
    program = make_program()
    message = make_message()

    payload = build_hook_payload(
        program=program,
        group=Group(),
        hook=HOOK_MESSAGE,
        message=message,
        update_id=123,
    )

    assert payload["protocol"] == HOOK_PROTOCOL
    assert payload["hook"] == HOOK_MESSAGE
    assert payload["program"] == "hook"
    assert payload["group"] == {
        "id": -1001234567890,
        "title": "Ops",
        "username": "ops_group",
        "is_forum": True,
    }
    assert payload["topic"] == {"id": 7}
    assert payload["update_id"] == 123
    assert payload["reason"] is None
    assert payload["message"] == {
        "id": 99,
        "date": "2026-11-09T12:00:00+00:00",
        "edit_date": None,
        "text": "hello",
        "caption": None,
        "sender": {
            "type": "user",
            "id": 42,
            "is_bot": False,
            "username": "alice",
            "first_name": "Alice",
            "last_name": None,
        },
        "reply_to_message_id": None,
        "message_thread_id": 7,
        "is_topic_message": True,
    }
    json.dumps(payload)


def test_edited_message_payload_contains_only_new_content() -> None:
    edited_at = datetime(2026, 11, 9, 12, 5, tzinfo=timezone.utc)
    payload = build_hook_payload(
        program=make_program(),
        group=Group(),
        hook=HOOK_EDITED_MESSAGE,
        message=make_message(text="new text", edit_date=edited_at),
        update_id=456,
    )

    assert payload["hook"] == HOOK_EDITED_MESSAGE
    assert payload["message"]["text"] == "new text"
    assert payload["message"]["edit_date"] == "2026-11-09T12:05:00+00:00"


def test_startup_payload_has_no_message_or_topic() -> None:
    payload = build_hook_payload(
        program=make_program(),
        group=Group(),
        hook=HOOK_STARTUP,
    )

    assert payload["hook"] == HOOK_STARTUP
    assert payload["topic"] == {"id": None}
    assert payload["message"] is None
    assert payload["update_id"] is None


def test_chat_sender_takes_precedence() -> None:
    sender = Chat(
        id=-1009999999999,
        type=ChatType.CHANNEL,
        title="Announcements",
        username="announcements",
    )
    payload = build_hook_payload(
        program=make_program(),
        group=Group(),
        hook=HOOK_MESSAGE,
        message=make_message(from_user=None, sender_chat=sender),
    )

    assert payload["message"]["sender"] == {
        "type": "chat",
        "id": -1009999999999,
        "title": "Announcements",
        "username": "announcements",
    }


def test_dispatcher_enqueues_actions_with_trigger_defaults() -> None:
    program = make_program("reply")
    runner = FakeRunner(
        {
            "reply": ProgramResult(
                returncode=0,
                stdout='{"action": "send_message", "text": "hi"}',
                stderr="",
            )
        }
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"reply": program}, group=Group(), sink=sink, runner=runner
    )

    asyncio.run(dispatcher.dispatch_message(make_message(), update_id=5))

    assert sink.batches == [
        [SendMessageAction(text="hi", topic_id=7)]
    ]
    assert runner.calls[0][1]["update_id"] == 5


def test_edited_message_dispatcher_uses_new_content() -> None:
    program = make_program(
        "edit", hooks=frozenset({HOOK_EDITED_MESSAGE})
    )
    runner = FakeRunner(
        {
            "edit": ProgramResult(
                returncode=0,
                stdout='{"action": "send_message", "text": "new text"}',
                stderr="",
            )
        }
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"edit": program}, group=Group(), sink=sink, runner=runner
    )
    edited_at = datetime(2026, 11, 9, 12, 5, tzinfo=timezone.utc)

    asyncio.run(
        dispatcher.dispatch_edited_message(
            make_message(text="new text", edit_date=edited_at),
            update_id=7,
        )
    )

    assert sink.batches == [
        [SendMessageAction(text="new text", topic_id=7)]
    ]
    payload = runner.calls[0][1]
    assert payload["hook"] == HOOK_EDITED_MESSAGE
    assert payload["update_id"] == 7
    assert payload["message"]["edit_date"] == "2026-11-09T12:05:00+00:00"


def test_required_update_types_follow_program_hooks() -> None:
    dispatcher = ProgramDispatcher(
        {
            "messages": make_program("messages"),
            "edits": make_program(
                "edits", hooks=frozenset({HOOK_EDITED_MESSAGE})
            ),
            "startup": make_program(
                "startup", hooks=frozenset({HOOK_STARTUP})
            ),
        },
        group=Group(),
        sink=FakeSink(),
        runner=FakeRunner({}),
    )

    assert dispatcher.required_update_types == frozenset(
        {HOOK_MESSAGE, HOOK_EDITED_MESSAGE}
    )
    assert dispatcher.has_message_hooks is True


def test_empty_stdout_means_no_actions() -> None:
    program = make_program("silent")
    runner = FakeRunner(
        {"silent": ProgramResult(returncode=0, stdout="", stderr="")}
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"silent": program}, group=Group(), sink=sink, runner=runner
    )

    asyncio.run(dispatcher.dispatch_message(make_message()))

    assert sink.batches == []


def test_nonzero_exit_discards_stdout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    program = make_program("broken")
    runner = FakeRunner(
        {
            "broken": ProgramResult(
                returncode=3,
                stdout='{"action": "send_message", "text": "hi"}',
                stderr="something went wrong",
            )
        }
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"broken": program}, group=Group(), sink=sink, runner=runner
    )

    with caplog.at_level(logging.WARNING, logger="bottg.programs"):
        asyncio.run(dispatcher.dispatch_message(make_message()))

    assert sink.batches == []
    assert "stdout actions discarded" in caplog.text
    assert "something went wrong" in caplog.text


def test_invalid_action_json_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    program = make_program("invalid")
    runner = FakeRunner(
        {"invalid": ProgramResult(returncode=0, stdout="not json", stderr="")}
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"invalid": program}, group=Group(), sink=sink, runner=runner
    )

    with caplog.at_level(logging.ERROR, logger="bottg.programs"):
        asyncio.run(dispatcher.dispatch_message(make_message()))

    assert sink.batches == []
    assert "invalid action JSON" in caplog.text


def test_one_program_failure_does_not_stop_other_programs() -> None:
    bad = make_program("bad")
    good = make_program("good")
    runner = FakeRunner(
        {
            "bad": ProgramExecutionError("boom"),
            "good": ProgramResult(
                returncode=0,
                stdout='{"action": "sleep", "seconds": 0.1}',
                stderr="",
            ),
        }
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"bad": bad, "good": good},
        group=Group(),
        sink=sink,
        runner=runner,
    )

    asyncio.run(dispatcher.dispatch_message(make_message()))

    assert [name for name, _ in runner.calls] == ["bad", "good"]
    assert sink.batches == [[SleepAction(seconds=0.1)]]


def test_message_hook_filters_bots_and_topics() -> None:
    bot_message = make_message(
        from_user=User(id=43, first_name="Bot", is_bot=True)
    )
    filtered = make_program(
        "filtered", message=MessageHookConfig(topics=(8,))
    )
    all_topics = make_program("all")
    bots_allowed = make_program(
        "bots", message=MessageHookConfig(ignore_bots=False)
    )
    runner = FakeRunner(
        {
            "filtered": ProgramResult(0, "", ""),
            "all": ProgramResult(0, "", ""),
            "bots": ProgramResult(0, "", ""),
        }
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {
            "filtered": filtered,
            "all": all_topics,
            "bots": bots_allowed,
        },
        group=Group(),
        sink=sink,
        runner=runner,
    )

    # Bot message: filtered/all ignore it, bots_allowed accepts it.
    asyncio.run(dispatcher.dispatch_message(bot_message))
    assert [name for name, _ in runner.calls] == ["bots"]

    runner.calls.clear()

    # Topic 7 does not match filtered's topic 8, but matches all.
    asyncio.run(dispatcher.dispatch_message(make_message()))
    assert [name for name, _ in runner.calls] == ["all", "bots"]

    runner.calls.clear()

    # Topic 8 matches filtered and all.
    asyncio.run(
        dispatcher.dispatch_message(make_message(message_thread_id=8))
    )
    assert [name for name, _ in runner.calls] == [
        "filtered",
        "all",
        "bots",
    ]


def test_general_topic_filter_matches_no_thread() -> None:
    program = make_program(
        "general", message=MessageHookConfig(topics=(None,))
    )
    runner = FakeRunner({"general": ProgramResult(0, "", "")})
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"general": program}, group=Group(), sink=sink, runner=runner
    )

    asyncio.run(
        dispatcher.dispatch_message(make_message(message_thread_id=None))
    )
    assert [name for name, _ in runner.calls] == ["general"]

    runner.calls.clear()
    asyncio.run(
        dispatcher.dispatch_message(make_message(message_thread_id=7))
    )
    assert runner.calls == []


def test_dispatcher_rejects_messages_from_other_chats() -> None:
    program = make_program("other")
    runner = FakeRunner({"other": ProgramResult(0, "", "")})
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"other": program}, group=Group(), sink=sink, runner=runner
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(chat_id=-1009999999999)
        )
    )

    assert runner.calls == []


def test_startup_and_shutdown_hooks_run_only_for_subscribers() -> None:
    startup = make_program(
        "startup", hooks=frozenset({HOOK_STARTUP})
    )
    shutdown = make_program(
        "shutdown", hooks=frozenset({HOOK_SHUTDOWN})
    )
    runner = FakeRunner(
        {
            "startup": ProgramResult(0, "", ""),
            "shutdown": ProgramResult(0, "", ""),
        }
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"startup": startup, "shutdown": shutdown},
        group=Group(),
        sink=sink,
        runner=runner,
    )

    asyncio.run(dispatcher.run_hook(HOOK_STARTUP))
    assert [name for name, _ in runner.calls] == ["startup"]
    assert runner.calls[0][1]["message"] is None

    runner.calls.clear()
    asyncio.run(dispatcher.run_hook(HOOK_SHUTDOWN, reason="signal"))
    assert [name for name, _ in runner.calls] == ["shutdown"]
    assert runner.calls[0][1]["reason"] == "signal"


def test_runner_returns_stdout_and_stderr(tmp_path: Path) -> None:
    program = make_program(
        command=(
            sys.executable,
            "-c",
            "import json, sys; "
            "data = json.load(sys.stdin); "
            "sys.stderr.write('diag'); "
            "print(json.dumps({'hook': data['hook']}))",
        ),
        cwd=tmp_path,
    )

    result = asyncio.run(
        ProgramRunner().run(program, {"hook": HOOK_STARTUP})
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"hook": HOOK_STARTUP}
    assert result.stderr == "diag"


def test_runner_uses_cwd_and_environment(tmp_path: Path) -> None:
    program = make_program(
        command=(
            sys.executable,
            "-c",
            "import os; print(os.getcwd()); print(os.environ['HOOK_VALUE'])",
        ),
        cwd=tmp_path,
        env={"HOOK_VALUE": "yes"},
    )

    result = asyncio.run(ProgramRunner().run(program, {}))

    lines = result.stdout.splitlines()
    assert lines == [str(tmp_path), "yes"]


def test_runner_reports_exit_status_and_empty_stdout(tmp_path: Path) -> None:
    program = make_program(
        command=(sys.executable, "-c", "raise SystemExit(3)"),
        cwd=tmp_path,
    )

    result = asyncio.run(ProgramRunner().run(program, {}))

    assert result.returncode == 3
    assert result.stdout == ""


def test_runner_enforces_timeout(tmp_path: Path) -> None:
    program = make_program(
        command=(
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ),
        cwd=tmp_path,
        timeout_seconds=0.2,
    )

    with pytest.raises(ProgramExecutionError, match="timed out"):
        asyncio.run(ProgramRunner().run(program, {}))


def test_runner_enforces_stdout_limit(tmp_path: Path) -> None:
    program = make_program(
        command=(sys.executable, "-c", "print('x' * 100)"),
        cwd=tmp_path,
        max_output_bytes=10,
    )

    with pytest.raises(ProgramExecutionError, match="exceeded"):
        asyncio.run(ProgramRunner().run(program, {}))


def test_runner_rejects_invalid_utf8_stdout(tmp_path: Path) -> None:
    program = make_program(
        command=(
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'\\xff')",
        ),
        cwd=tmp_path,
    )

    with pytest.raises(ProgramExecutionError, match="UTF-8"):
        asyncio.run(ProgramRunner().run(program, {}))


def test_runner_reports_missing_executable(tmp_path: Path) -> None:
    program = make_program(
        command=("/definitely/not/a/real/program",), cwd=tmp_path
    )

    with pytest.raises(ProgramExecutionError, match="executable not found"):
        asyncio.run(ProgramRunner().run(program, {}))


def test_example_echo_hook_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    program = make_program(
        command=(sys.executable, str(root / "examples" / "echo_hook.py")),
        cwd=root,
    )

    result = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 5, "text": "!echo hello"}},
        )
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "action": "send_message",
        "text": "hello",
        "reply_to_message_id": 5,
    }

    slash = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 7, "text": "/echo hello"}},
        )
    )
    assert json.loads(slash.stdout)["text"] == "hello"

    addressed = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 8, "text": "/echo@some_bot hello"}},
        )
    )
    assert json.loads(addressed.stdout)["text"] == "hello"

    silent = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 6, "text": "hello"}},
        )
    )
    assert silent.returncode == 0
    assert silent.stdout == ""


class RecordingBot:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def send_message(
        self, chat_id: int, text: str, **kwargs: Any
    ) -> object:
        self.messages.append((text, kwargs))
        return object()

    async def delete_message(
        self, chat_id: int, message_id: int, **kwargs: Any
    ) -> bool:
        return True

    async def set_message_reaction(
        self,
        chat_id: int,
        message_id: int,
        reaction: Any = None,
        **kwargs: Any,
    ) -> bool:
        return True


def test_end_to_end_program_to_action_queue(tmp_path: Path) -> None:
    program = make_program(
        "reply",
        command=(
            sys.executable,
            "-c",
            "import json, sys; "
            "event = json.load(sys.stdin); "
            "print(json.dumps({"
            "'action': 'send_message', "
            "'text': event['message']['text']"
            "}))",
        ),
        cwd=tmp_path,
    )

    async def scenario() -> RecordingBot:
        bot = RecordingBot()
        queue = ActionQueue(bot, -1001234567890, capacity=2)
        await queue.start()
        dispatcher = ProgramDispatcher(
            {"reply": program}, group=Group(), sink=queue
        )
        await dispatcher.dispatch_message(make_message(text="hello"))
        await queue.close(timeout=2)
        return bot

    bot = asyncio.run(scenario())

    assert len(bot.messages) == 1
    text, kwargs = bot.messages[0]
    assert text == "hello"
    assert kwargs["message_thread_id"] == 7


def test_example_lifecycle_hook_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    program = make_program(
        command=(
            sys.executable,
            str(root / "examples" / "lifecycle_hook.py"),
        ),
        cwd=root,
    )

    startup = asyncio.run(
        ProgramRunner().run(
            program,
            {"hook": "startup", "bottg": {"version": "0.3.0"}},
        )
    )
    assert json.loads(startup.stdout) == {
        "action": "send_message",
        "text": "bottg 0.3.0 started",
    }

    shutdown = asyncio.run(
        ProgramRunner().run(
            program,
            {
                "hook": "shutdown",
                "bottg": {"version": "0.3.0"},
                "reason": "signal",
            },
        )
    )
    assert json.loads(shutdown.stdout) == {
        "action": "send_message",
        "text": "bottg stopping (signal)",
    }


def test_example_echo_hook_slowecho_uses_sleep() -> None:
    root = Path(__file__).resolve().parents[1]
    program = make_program(
        command=(sys.executable, str(root / "examples" / "echo_hook.py")),
        cwd=root,
    )

    result = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 9, "text": "!slowecho hello"}},
        )
    )

    assert json.loads(result.stdout) == [
        {"action": "sleep", "seconds": 1.0},
        {
            "action": "send_message",
            "text": "hello",
            "reply_to_message_id": 9,
        },
    ]


def test_example_reaction_hook_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    program = make_program(
        command=(
            sys.executable,
            str(root / "examples" / "reaction_hook.py"),
        ),
        cwd=root,
    )

    explicit = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 5, "text": "!react 🔥"}},
        )
    )
    assert json.loads(explicit.stdout) == {
        "action": "set_reaction",
        "emoji": "🔥",
    }

    default = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 6, "text": "/react"}},
        )
    )
    assert json.loads(default.stdout) == {
        "action": "set_reaction",
        "emoji": "👍",
    }

    silent = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 7, "text": "hello"}},
        )
    )
    assert silent.stdout == ""


def test_example_cleanup_hook_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    program = make_program(
        command=(
            sys.executable,
            str(root / "examples" / "cleanup_hook.py"),
        ),
        cwd=root,
    )

    implicit = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 5, "text": "!delete"}},
        )
    )
    assert json.loads(implicit.stdout) == {"action": "delete_message"}

    explicit = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 6, "text": "/delete 123"}},
        )
    )
    assert json.loads(explicit.stdout) == {
        "action": "delete_message",
        "message_id": 123,
    }

    invalid = asyncio.run(
        ProgramRunner().run(
            program,
            {"message": {"id": 7, "text": "!delete nope"}},
        )
    )
    assert invalid.stdout == ""


def test_example_topic_hook_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    program = make_program(
        command=(sys.executable, str(root / "examples" / "topic_hook.py")),
        cwd=root,
    )

    def run(text: str, topic_id: int | None, message_id: int = 5) -> Any:
        return asyncio.run(
            ProgramRunner().run(
                program,
                {
                    "message": {"id": message_id, "text": text},
                    "topic": {"id": topic_id},
                },
            )
        )

    created = run("/newtopic@some_bot 公告", None)
    assert json.loads(created.stdout) == {
        "action": "create_topic",
        "name": "公告",
    }

    renamed = run("!renametopic 新名字", 17)
    assert json.loads(renamed.stdout) == {
        "action": "edit_topic",
        "topic_id": 17,
        "name": "新名字",
    }

    unconfirmed = run("!deletetopic", 17)
    assert json.loads(unconfirmed.stdout)["action"] == "send_message"

    deleted = run("!deletetopic confirm", 17)
    assert json.loads(deleted.stdout) == {
        "action": "delete_topic",
        "topic_id": 17,
    }

    topic_id = run("!topicid", 17)
    assert "17" in json.loads(topic_id.stdout)["text"]

    general = run("!renametopic 新名字", None)
    assert json.loads(general.stdout)["action"] == "send_message"

    listing = run("!topics", 17)
    assert "没有列出话题" in json.loads(listing.stdout)["text"]
