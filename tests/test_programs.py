from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from telegram import (
    Animation,
    Audio,
    Bot,
    Chat,
    Document,
    Message,
    PhotoSize,
    Sticker,
    User,
    Video,
    VideoNote,
    Voice,
)
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.request import BaseRequest

from bottg.action_queue import ActionQueue
from bottg.actions import (
    FileSource,
    SendDocumentAction,
    SendMessageAction,
    SleepAction,
)
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
    text: str | None = "hello",
    caption: str | None = None,
    photo: tuple[PhotoSize, ...] | None = None,
    document: Document | None = None,
    animation: Animation | None = None,
    video: Video | None = None,
    audio: Audio | None = None,
    voice: Voice | None = None,
    video_note: VideoNote | None = None,
    sticker: Sticker | None = None,
    media_group_id: str | None = None,
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
        caption=caption,
        photo=photo,
        document=document,
        animation=animation,
        video=video,
        audio=audio,
        voice=voice,
        video_note=video_note,
        sticker=sticker,
        media_group_id=media_group_id,
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


class FakeMediaFile:
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def download_to_drive(self, custom_path: Path) -> Path:
        custom_path.write_bytes(self.content)
        return custom_path


class FakeMediaBot:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.file_ids: list[str] = []

    async def get_file(self, file_id: str) -> FakeMediaFile:
        self.file_ids.append(file_id)
        return FakeMediaFile(self.contents[file_id])


class FailingMediaBot:
    async def get_file(self, file_id: str) -> FakeMediaFile:
        raise TelegramError("download failed")


class SlowMediaBot:
    async def get_file(self, file_id: str) -> FakeMediaFile:
        await asyncio.sleep(10)
        return FakeMediaFile(b"too late")


class CaptureBotRequest(BaseRequest):
    """Serve Telegram API responses from memory for a real PTB Bot."""

    def __init__(self, file_content: bytes) -> None:
        self.file_content = file_content
        self.calls: list[tuple[str, str, Any]] = []

    @property
    def read_timeout(self) -> float:
        return 5.0

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: Any = None,
        **kwargs: Any,
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, request_data))
        if method == "GET":
            return 200, self.file_content
        if url.endswith("/getFile"):
            result = {
                "file_id": "photo-id",
                "file_unique_id": "photo-unique",
                "file_path": "photos/photo.jpg",
                "file_size": len(self.file_content),
            }
            return 200, json.dumps({"ok": True, "result": result}).encode()
        if url.endswith("/sendPhoto"):
            result = {
                "message_id": 1,
                "date": 0,
                "chat": {"id": -1001234567890, "type": "supergroup"},
            }
            return 200, json.dumps({"ok": True, "result": result}).encode()
        raise AssertionError(f"unexpected request: {method} {url}")


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
        "attachments": [],
        "media_group_id": None,
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


def test_photo_payload_exposes_the_largest_size() -> None:
    small = PhotoSize(
        file_id="small-photo",
        file_unique_id="small-unique",
        width=90,
        height=90,
        file_size=100,
    )
    large = PhotoSize(
        file_id="large-photo",
        file_unique_id="large-unique",
        width=1280,
        height=720,
        file_size=123456,
    )

    payload = build_hook_payload(
        program=make_program(),
        group=Group(),
        hook=HOOK_MESSAGE,
        message=make_message(
            text=None,
            caption="look at this",
            photo=(small, large),
            media_group_id="album-1",
        ),
    )

    assert payload["message"]["text"] is None
    assert payload["message"]["caption"] == "look at this"
    assert payload["message"]["media_group_id"] == "album-1"
    assert payload["message"]["attachments"] == [
        {
            "type": "photo",
            "file_id": "large-photo",
            "file_unique_id": "large-unique",
            "file_name": None,
            "mime_type": "image/jpeg",
            "file_size": 123456,
            "width": 1280,
            "height": 720,
            "duration": None,
            "local_path": None,
            "download_error": None,
        }
    ]
    json.dumps(payload)


def test_document_payload_exposes_file_metadata() -> None:
    document = Document(
        file_id="document-id",
        file_unique_id="document-unique",
        file_name="report.pdf",
        mime_type="application/pdf",
        file_size=9876,
    )

    payload = build_hook_payload(
        program=make_program(),
        group=Group(),
        hook=HOOK_MESSAGE,
        message=make_message(text=None, document=document),
    )

    assert payload["message"]["attachments"] == [
        {
            "type": "document",
            "file_id": "document-id",
            "file_unique_id": "document-unique",
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
            "file_size": 9876,
            "width": None,
            "height": None,
            "duration": None,
            "local_path": None,
            "download_error": None,
        }
    ]
    json.dumps(payload)


def test_animation_is_not_reported_as_document_twice() -> None:
    animation = Animation(
        file_id="animation-id",
        file_unique_id="animation-unique",
        width=320,
        height=240,
        duration=3,
        file_name="loop.mp4",
        mime_type="video/mp4",
        file_size=555,
    )
    document = Document(
        file_id="animation-id",
        file_unique_id="animation-unique",
        file_name="loop.mp4",
        mime_type="video/mp4",
        file_size=555,
    )

    payload = build_hook_payload(
        program=make_program(),
        group=Group(),
        hook=HOOK_MESSAGE,
        message=make_message(
            text=None, animation=animation, document=document
        ),
    )

    assert [item["type"] for item in payload["message"]["attachments"]] == [
        "animation"
    ]


def test_all_supported_attachment_types_are_reported() -> None:
    cases = {
        "video": Video(
            file_id="video-id",
            file_unique_id="video-unique",
            width=640,
            height=480,
            duration=5,
            mime_type="video/mp4",
            file_size=100,
        ),
        "audio": Audio(
            file_id="audio-id",
            file_unique_id="audio-unique",
            duration=10,
            mime_type="audio/mpeg",
            file_size=200,
            file_name="track.mp3",
        ),
        "voice": Voice(
            file_id="voice-id",
            file_unique_id="voice-unique",
            duration=2,
            mime_type="audio/ogg",
            file_size=50,
        ),
        "video_note": VideoNote(
            file_id="note-id",
            file_unique_id="note-unique",
            length=240,
            duration=4,
            file_size=60,
        ),
        "sticker": Sticker(
            file_id="sticker-id",
            file_unique_id="sticker-unique",
            width=512,
            height=512,
            is_animated=False,
            is_video=False,
            type="regular",
            emoji="😀",
            file_size=70,
        ),
    }

    for expected_type, item in cases.items():
        payload = build_hook_payload(
            program=make_program(),
            group=Group(),
            hook=HOOK_MESSAGE,
            message=make_message(text=None, **{expected_type: item}),
        )
        [attachment] = payload["message"]["attachments"]
        assert attachment["type"] == expected_type
        assert attachment["file_id"] == item.file_id
        assert attachment["file_unique_id"] == item.file_unique_id
        assert attachment["file_size"] == item.file_size
        json.dumps(payload)

    video_payload = build_hook_payload(
        program=make_program(),
        group=Group(),
        hook=HOOK_MESSAGE,
        message=make_message(text=None, video=cases["video"]),
    )
    [video_attachment] = video_payload["message"]["attachments"]
    assert video_attachment["width"] == 640
    assert video_attachment["height"] == 480
    assert video_attachment["duration"] == 5
    assert video_attachment["mime_type"] == "video/mp4"


def test_attachment_download_exposes_temp_path_during_the_hook() -> None:
    photo = PhotoSize(
        file_id="photo-id",
        file_unique_id="photo-unique",
        width=100,
        height=100,
        file_size=4,
    )
    program = make_program(
        "download",
        message=MessageHookConfig(download_attachments=True),
    )
    captured: dict[str, Any] = {}

    def outcome(
        _program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        attachment = payload["message"]["attachments"][0]
        path = Path(attachment["local_path"])
        captured["path"] = path
        captured["existed"] = path.is_file()
        captured["content"] = path.read_bytes()
        return ProgramResult(0, "", "")

    media_bot = FakeMediaBot({"photo-id": b"jpeg"})
    dispatcher = ProgramDispatcher(
        {"download": program},
        group=Group(),
        sink=FakeSink(),
        runner=FakeRunner({"download": outcome}),
        media_bot=media_bot,
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(text=None, photo=(photo,))
        )
    )

    assert media_bot.file_ids == ["photo-id"]
    assert captured["existed"] is True
    assert captured["content"] == b"jpeg"
    assert captured["path"].exists() is False


def test_attachment_filename_cannot_escape_temp_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    document = Document(
        file_id="document-id",
        file_unique_id="document-unique",
        file_name="../../etc/passwd",
        mime_type="text/plain",
        file_size=4,
    )
    program = make_program(
        "download",
        message=MessageHookConfig(download_attachments=True),
    )
    captured: dict[str, Any] = {}

    def outcome(
        _program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        captured.update(payload["message"]["attachments"][0])
        captured["existed"] = Path(captured["local_path"]).is_file()
        return ProgramResult(0, "", "")

    dispatcher = ProgramDispatcher(
        {"download": program},
        group=Group(),
        sink=FakeSink(),
        runner=FakeRunner({"download": outcome}),
        media_bot=FakeMediaBot({"document-id": b"data"}),
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(text=None, document=document)
        )
    )

    local_path = Path(captured["local_path"])
    assert local_path.name == "00-passwd"
    assert local_path.parent.parent == tmp_path
    assert captured["existed"] is True
    assert list(tmp_path.glob("bottg-media-*")) == []


def test_attachment_download_is_off_by_default() -> None:
    photo = PhotoSize(
        file_id="photo-id",
        file_unique_id="photo-unique",
        width=100,
        height=100,
        file_size=4,
    )
    program = make_program("plain")
    captured: dict[str, Any] = {}

    def outcome(
        _program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        captured.update(payload["message"]["attachments"][0])
        return ProgramResult(0, "", "")

    media_bot = FakeMediaBot({"photo-id": b"jpeg"})
    dispatcher = ProgramDispatcher(
        {"plain": program},
        group=Group(),
        sink=FakeSink(),
        runner=FakeRunner({"plain": outcome}),
        media_bot=media_bot,
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(text=None, photo=(photo,))
        )
    )

    assert media_bot.file_ids == []
    assert captured["local_path"] is None
    assert captured["download_error"] is None


def test_attachment_download_reports_unavailable_client() -> None:
    photo = PhotoSize(
        file_id="photo-id",
        file_unique_id="photo-unique",
        width=100,
        height=100,
        file_size=4,
    )
    program = make_program(
        "download",
        message=MessageHookConfig(download_attachments=True),
    )
    captured: dict[str, Any] = {}

    def outcome(
        _program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        captured.update(payload["message"]["attachments"][0])
        return ProgramResult(0, "", "")

    dispatcher = ProgramDispatcher(
        {"download": program},
        group=Group(),
        sink=FakeSink(),
        runner=FakeRunner({"download": outcome}),
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(text=None, photo=(photo,))
        )
    )

    assert captured["local_path"] is None
    assert captured["download_error"] == "unavailable"


def test_attachment_over_limit_is_not_downloaded() -> None:
    photo = PhotoSize(
        file_id="photo-id",
        file_unique_id="photo-unique",
        width=100,
        height=100,
        file_size=100,
    )
    program = make_program(
        "download",
        message=MessageHookConfig(
            download_attachments=True,
            max_attachment_bytes=50,
        ),
    )
    captured: dict[str, Any] = {}

    def outcome(
        _program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        captured.update(payload["message"]["attachments"][0])
        return ProgramResult(0, "", "")

    media_bot = FakeMediaBot({"photo-id": b"x" * 100})
    dispatcher = ProgramDispatcher(
        {"download": program},
        group=Group(),
        sink=FakeSink(),
        runner=FakeRunner({"download": outcome}),
        media_bot=media_bot,
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(text=None, photo=(photo,))
        )
    )

    assert media_bot.file_ids == []
    assert captured["local_path"] is None
    assert captured["download_error"] == "too_large"


def test_attachment_actual_size_over_limit_is_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    photo = PhotoSize(
        file_id="photo-id",
        file_unique_id="photo-unique",
        width=100,
        height=100,
    )
    program = make_program(
        "download",
        message=MessageHookConfig(
            download_attachments=True,
            max_attachment_bytes=4,
        ),
    )
    captured: dict[str, Any] = {}

    def outcome(
        _program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        captured.update(payload["message"]["attachments"][0])
        return ProgramResult(0, "", "")

    dispatcher = ProgramDispatcher(
        {"download": program},
        group=Group(),
        sink=FakeSink(),
        runner=FakeRunner({"download": outcome}),
        media_bot=FakeMediaBot({"photo-id": b"12345"}),
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(text=None, photo=(photo,))
        )
    )

    assert captured["local_path"] is None
    assert captured["download_error"] == "too_large"
    assert list(tmp_path.glob("bottg-media-*")) == []


def test_attachment_download_timeout_is_reported() -> None:
    photo = PhotoSize(
        file_id="photo-id",
        file_unique_id="photo-unique",
        width=100,
        height=100,
        file_size=4,
    )
    program = make_program(
        "download",
        message=MessageHookConfig(download_attachments=True),
        timeout_seconds=0.05,
    )
    captured: dict[str, Any] = {}

    def outcome(
        _program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        captured.update(payload["message"]["attachments"][0])
        return ProgramResult(0, "", "")

    dispatcher = ProgramDispatcher(
        {"download": program},
        group=Group(),
        sink=FakeSink(),
        runner=FakeRunner({"download": outcome}),
        media_bot=SlowMediaBot(),
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(text=None, photo=(photo,))
        )
    )

    assert captured["local_path"] is None
    assert captured["download_error"] == "timeout"


def test_attachment_download_failure_does_not_skip_the_hook() -> None:
    photo = PhotoSize(
        file_id="photo-id",
        file_unique_id="photo-unique",
        width=100,
        height=100,
        file_size=4,
    )
    program = make_program(
        "download",
        message=MessageHookConfig(download_attachments=True),
    )
    captured: dict[str, Any] = {}

    def outcome(
        _program: ProgramConfig, payload: Mapping[str, Any]
    ) -> ProgramResult:
        captured.update(payload["message"]["attachments"][0])
        return ProgramResult(
            0,
            '{"action": "send_message", "text": "still ran"}',
            "",
        )

    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"download": program},
        group=Group(),
        sink=sink,
        runner=FakeRunner({"download": outcome}),
        media_bot=FailingMediaBot(),
    )

    asyncio.run(
        dispatcher.dispatch_message(
            make_message(text=None, photo=(photo,))
        )
    )

    assert captured["local_path"] is None
    assert captured["download_error"] == "telegram_error"
    assert sink.batches == [
        [SendMessageAction(text="still ran", topic_id=7)]
    ]


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


def test_relative_media_path_resolves_against_program_cwd(
    tmp_path: Path,
) -> None:
    (tmp_path / "report.pdf").write_bytes(b"pdf")
    program = make_program(
        "document",
        command=(
            sys.executable,
            "-c",
            "print('{\"action\": \"send_document\", "
            "\"document\": {\"path\": \"report.pdf\"}}')",
        ),
        cwd=tmp_path,
    )
    sink = FakeSink()
    dispatcher = ProgramDispatcher(
        {"document": program},
        group=Group(),
        sink=sink,
        runner=ProgramRunner(),
    )

    asyncio.run(dispatcher.dispatch_message(make_message()))

    assert sink.batches == [
        [
            SendDocumentAction(
                document=FileSource(
                    "path", str((tmp_path / "report.pdf").resolve())
                ),
                topic_id=7,
            )
        ]
    ]


def test_end_to_end_attachment_download_and_repost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    photo = PhotoSize(
        file_id="photo-id",
        file_unique_id="photo-unique",
        width=100,
        height=80,
        file_size=10,
    )
    program = make_program(
        "vision",
        command=(
            sys.executable,
            "-c",
            "import json, pathlib, sys; "
            "event = json.load(sys.stdin); "
            "a = event['message']['attachments'][0]; "
            "p = pathlib.Path(a['local_path']); "
            "assert p.read_bytes() == b'jpeg-bytes'; "
            "print(json.dumps({"
            "'action': 'send_photo', "
            "'photo': a['file_id'], "
            "'caption': a['type'] + ':' + str(p.stat().st_size)"
            "}))",
        ),
        cwd=tmp_path,
        message=MessageHookConfig(download_attachments=True),
    )

    async def scenario() -> CaptureBotRequest:
        request = CaptureBotRequest(b"jpeg-bytes")
        bot = Bot(
            token="123456:abcdefghijklmnopqrstuvwxyzABCDE",
            request=request,
        )
        queue = ActionQueue(bot, -1001234567890, capacity=2)
        await queue.start()
        dispatcher = ProgramDispatcher(
            {"vision": program},
            group=Group(),
            sink=queue,
            media_bot=bot,
        )
        await dispatcher.dispatch_message(
            make_message(text=None, photo=(photo,))
        )
        await queue.close(timeout=2)
        return request

    request = asyncio.run(scenario())

    posts = [
        (url, data)
        for method, url, data in request.calls
        if method == "POST"
    ]
    assert [url.rsplit("/", 1)[-1] for url, _ in posts] == [
        "getFile",
        "sendPhoto",
    ]
    assert posts[0][1].parameters == {"file_id": "photo-id"}
    send_params = posts[1][1].parameters
    assert send_params["photo"] == "photo-id"
    assert send_params["caption"] == "photo:10"
    assert send_params["message_thread_id"] == 7
    assert any(
        method == "GET" and "/photos/photo.jpg" in url
        for method, url, _ in request.calls
    )
    assert list(tmp_path.glob("bottg-media-*")) == []


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


def test_example_media_hook_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    program = make_program(
        command=(sys.executable, str(root / "examples" / "media_hook.py")),
        cwd=root,
    )

    def run(message: dict[str, Any]) -> Any:
        return asyncio.run(
            ProgramRunner().run(program, {"message": message})
        )

    photo = run(
        {
            "id": 5,
            "text": None,
            "caption": "!repost 新标题",
            "attachments": [
                {"type": "photo", "file_id": "photo-file-id"}
            ],
        }
    )
    assert json.loads(photo.stdout) == {
        "action": "send_photo",
        "photo": "photo-file-id",
        "caption": "新标题",
    }

    document = run(
        {
            "id": 6,
            "text": None,
            "caption": "/repost",
            "attachments": [
                {"type": "document", "file_id": "document-file-id"}
            ],
        }
    )
    assert json.loads(document.stdout) == {
        "action": "send_document",
        "document": {"file_id": "document-file-id"},
        "caption": "repost",
    }

    info = run(
        {
            "id": 7,
            "text": None,
            "caption": "!attachmentinfo",
            "attachments": [
                {
                    "type": "photo",
                    "file_id": "photo-file-id",
                    "width": 100,
                    "height": 50,
                }
            ],
        }
    )
    info_text = json.loads(info.stdout)["text"]
    assert "type: photo" in info_text
    assert "file_id: photo-file-id" in info_text
    assert "width: 100" in info_text

    missing = run({"id": 8, "text": "!repost", "attachments": []})
    assert "没有附件" in json.loads(missing.stdout)["text"]
