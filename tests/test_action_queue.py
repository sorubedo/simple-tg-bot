from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import Bot
from telegram.error import RetryAfter, TelegramError
from telegram.request import BaseRequest

from bottg.action_queue import ActionQueue
from bottg.actions import (
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
)
from bottg.errors import ActionQueueError


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, Any, dict[str, Any]]] = []
        self.fail_delete = False
        self.fail_photo = False
        self.retry_once = False
        self.topics: list[tuple[str, int | str]] = []

    async def send_message(
        self, chat_id: int, text: str, **kwargs: Any
    ) -> object:
        if self.retry_once:
            self.retry_once = False
            raise RetryAfter(0)
        self.calls.append(("send", chat_id, text, kwargs))
        return object()

    async def send_photo(
        self, chat_id: int, photo: Any, **kwargs: Any
    ) -> object:
        if self.fail_photo:
            self.fail_photo = False
            raise TelegramError("photo failed")
        self.calls.append(("photo", chat_id, photo, kwargs))
        return object()

    async def send_document(
        self, chat_id: int, document: Any, **kwargs: Any
    ) -> object:
        self.calls.append(("document", chat_id, document, kwargs))
        return object()

    async def delete_message(
        self, chat_id: int, message_id: int, **kwargs: Any
    ) -> bool:
        if self.fail_delete:
            self.fail_delete = False
            raise TelegramError("delete failed")
        self.calls.append(("delete", chat_id, message_id, kwargs))
        return True

    async def set_message_reaction(
        self,
        chat_id: int,
        message_id: int,
        reaction: Any = None,
        **kwargs: Any,
    ) -> bool:
        self.calls.append(("react", chat_id, message_id, reaction, kwargs))
        return True

    async def create_forum_topic(
        self, chat_id: int, name: str, **kwargs: Any
    ) -> Any:
        topic_id = 100 + len(self.topics)
        self.topics.append(("create", name))
        return SimpleNamespace(message_thread_id=topic_id)

    async def delete_forum_topic(
        self, chat_id: int, message_thread_id: int, **kwargs: Any
    ) -> bool:
        self.topics.append(("delete", message_thread_id))
        return True

    async def edit_forum_topic(
        self,
        chat_id: int,
        message_thread_id: int,
        name: str | None = None,
        **kwargs: Any,
    ) -> bool:
        self.topics.append(("edit", f"{message_thread_id}:{name}"))
        return True


class CaptureRequest(BaseRequest):
    """Capture PTB request data instead of calling the Telegram API."""

    def __init__(self) -> None:
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
        self.calls.append((url, method, request_data))
        result = {
            "message_id": 1,
            "date": 0,
            "chat": {"id": -1001234567890, "type": "supergroup"},
        }
        return 200, json.dumps({"ok": True, "result": result}).encode()


def test_queue_executes_batches_in_order() -> None:
    async def scenario() -> tuple[FakeBot, ActionQueue]:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890, capacity=5)
        await queue.start()
        await queue.enqueue(
            [
                SendMessageAction(text="hello", topic_id=7),
                DeleteMessageAction(message_id=9),
            ]
        )
        await queue.enqueue(
            [SetReactionAction(message_id=9, emoji="👍", is_big=True)]
        )
        await queue.close(timeout=2)
        return bot, queue

    bot, queue = asyncio.run(scenario())

    assert [call[0] for call in bot.calls] == ["send", "delete", "react"]
    assert queue.processed_actions == 3
    assert queue.failed_actions == 0


def test_send_message_maps_all_fields() -> None:
    async def scenario() -> FakeBot:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                SendMessageAction(
                    text="hello",
                    topic_id=3,
                    reply_to_message_id=4,
                    parse_mode="HTML",
                    disable_notification=True,
                    disable_web_page_preview=True,
                )
            ]
        )
        await queue.close(timeout=2)
        return bot

    bot = asyncio.run(scenario())

    _, chat_id, text, kwargs = bot.calls[0]
    assert chat_id == -1001234567890
    assert text == "hello"
    assert kwargs["message_thread_id"] == 3
    assert kwargs["reply_parameters"].message_id == 4
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["disable_notification"] is True
    assert kwargs["link_preview_options"].is_disabled is True


def test_send_photo_maps_source_and_fields(tmp_path: Path) -> None:
    local_photo = tmp_path / "photo.jpg"
    local_photo.write_bytes(b"jpeg")

    async def scenario() -> FakeBot:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                SendPhotoAction(
                    photo=FileSource("file_id", "AgAC-photo"),
                    caption="hello",
                    topic_id=3,
                    reply_to_message_id=4,
                    parse_mode="HTML",
                    disable_notification=True,
                    has_spoiler=True,
                ),
                SendPhotoAction(
                    photo=FileSource("path", str(local_photo)),
                    topic_id=None,
                ),
            ]
        )
        await queue.close(timeout=2)
        return bot

    bot = asyncio.run(scenario())

    first, second = bot.calls
    assert first[0] == "photo"
    assert first[1] == -1001234567890
    assert first[2] == "AgAC-photo"
    assert first[3]["caption"] == "hello"
    assert first[3]["message_thread_id"] == 3
    assert first[3]["reply_parameters"].message_id == 4
    assert first[3]["parse_mode"] == "HTML"
    assert first[3]["disable_notification"] is True
    assert first[3]["has_spoiler"] is True
    assert second[2] == local_photo
    assert "message_thread_id" not in second[3]


def test_send_document_maps_source_and_fields() -> None:
    async def scenario() -> FakeBot:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                SendDocumentAction(
                    document=FileSource("url", "https://example.com/a.pdf"),
                    caption="report",
                    topic_id=5,
                    parse_mode="MarkdownV2",
                )
            ]
        )
        await queue.close(timeout=2)
        return bot

    bot = asyncio.run(scenario())

    _, chat_id, document, kwargs = bot.calls[0]
    assert chat_id == -1001234567890
    assert document == "https://example.com/a.pdf"
    assert kwargs["caption"] == "report"
    assert kwargs["message_thread_id"] == 5
    assert kwargs["parse_mode"] == "MarkdownV2"


def test_real_ptb_bot_uploads_local_media(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg-content")
    document = tmp_path / "report.pdf"
    document.write_bytes(b"pdf-content")

    async def scenario() -> CaptureRequest:
        request = CaptureRequest()
        bot = Bot(
            token="123456:abcdefghijklmnopqrstuvwxyzABCDE",
            request=request,
        )
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                SendPhotoAction(
                    photo=FileSource("path", str(photo)),
                    caption="photo caption",
                    topic_id=7,
                    reply_to_message_id=9,
                    parse_mode="HTML",
                    disable_notification=True,
                    has_spoiler=True,
                ),
                SendDocumentAction(
                    document=FileSource("path", str(document)),
                    caption="document caption",
                    topic_id=8,
                ),
            ]
        )
        await queue.close(timeout=2)
        return request

    request = asyncio.run(scenario())

    assert [url.rsplit("/", 1)[-1] for url, _, _ in request.calls] == [
        "sendPhoto",
        "sendDocument",
    ]

    _, _, photo_data = request.calls[0]
    photo_params = photo_data.parameters
    assert photo_params["chat_id"] == -1001234567890
    assert photo_params["caption"] == "photo caption"
    assert photo_params["message_thread_id"] == 7
    assert photo_params["reply_parameters"] == {"message_id": 9}
    assert photo_params["parse_mode"] == "HTML"
    assert photo_params["disable_notification"] is True
    assert photo_params["has_spoiler"] is True
    photo_file = next(
        param for param in photo_data._parameters if param.name == "photo"
    ).input_files[0]
    assert photo_file.filename == "photo.jpg"
    assert photo_file.input_file_content == b"jpeg-content"

    _, _, document_data = request.calls[1]
    document_params = document_data.parameters
    assert document_params["chat_id"] == -1001234567890
    assert document_params["caption"] == "document caption"
    assert document_params["message_thread_id"] == 8
    document_file = next(
        param
        for param in document_data._parameters
        if param.name == "document"
    ).input_files[0]
    assert document_file.filename == "report.pdf"
    assert document_file.input_file_content == b"pdf-content"


def test_real_ptb_bot_sends_remote_media_without_upload() -> None:
    async def scenario() -> CaptureRequest:
        request = CaptureRequest()
        bot = Bot(
            token="123456:abcdefghijklmnopqrstuvwxyzABCDE",
            request=request,
        )
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                SendPhotoAction(
                    photo=FileSource("file_id", "AgAC-photo")
                ),
                SendDocumentAction(
                    document=FileSource(
                        "url", "https://example.com/report.pdf"
                    )
                ),
            ]
        )
        await queue.close(timeout=2)
        return request

    request = asyncio.run(scenario())

    assert [url.rsplit("/", 1)[-1] for url, _, _ in request.calls] == [
        "sendPhoto",
        "sendDocument",
    ]
    _, _, photo_data = request.calls[0]
    assert photo_data.contains_files is False
    assert photo_data.parameters["photo"] == "AgAC-photo"
    _, _, document_data = request.calls[1]
    assert document_data.contains_files is False
    assert (
        document_data.parameters["document"]
        == "https://example.com/report.pdf"
    )


def test_general_topic_omits_message_thread_id() -> None:
    async def scenario() -> FakeBot:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue([SendMessageAction(text="hello", topic_id=None)])
        await queue.close(timeout=2)
        return bot

    bot = asyncio.run(scenario())

    assert "message_thread_id" not in bot.calls[0][3]
    assert "reply_parameters" not in bot.calls[0][3]


def test_sleep_pauses_the_worker() -> None:
    async def scenario() -> tuple[FakeBot, float]:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        started = time.monotonic()
        await queue.enqueue(
            [
                SleepAction(seconds=0.05),
                SendMessageAction(text="after sleep"),
            ]
        )
        await queue.close(timeout=2)
        return bot, time.monotonic() - started

    bot, elapsed = asyncio.run(scenario())

    assert [call[0] for call in bot.calls] == ["send"]
    assert elapsed >= 0.04


def test_action_failure_does_not_stop_the_batch() -> None:
    async def scenario() -> tuple[FakeBot, ActionQueue]:
        bot = FakeBot()
        bot.fail_delete = True
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                SendMessageAction(text="before"),
                DeleteMessageAction(message_id=9),
                SendMessageAction(text="after"),
            ]
        )
        await queue.close(timeout=2)
        return bot, queue

    bot, queue = asyncio.run(scenario())

    assert [call[0] for call in bot.calls] == ["send", "send"]
    assert [call[2] for call in bot.calls] == ["before", "after"]
    assert queue.processed_actions == 2
    assert queue.failed_actions == 1


def test_media_action_failure_does_not_stop_the_batch() -> None:
    async def scenario() -> tuple[FakeBot, ActionQueue]:
        bot = FakeBot()
        bot.fail_photo = True
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                SendPhotoAction(
                    photo=FileSource("file_id", "AgAC-photo")
                ),
                SendMessageAction(text="after"),
            ]
        )
        await queue.close(timeout=2)
        return bot, queue

    bot, queue = asyncio.run(scenario())

    assert [call[0] for call in bot.calls] == ["send"]
    assert bot.calls[0][2] == "after"
    assert queue.processed_actions == 1
    assert queue.failed_actions == 1


def test_missing_local_media_file_does_not_stop_the_batch(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[FakeBot, ActionQueue]:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                SendPhotoAction(
                    photo=FileSource(
                        "path", str(tmp_path / "missing.jpg")
                    )
                ),
                SendMessageAction(text="after"),
            ]
        )
        await queue.close(timeout=2)
        return bot, queue

    bot, queue = asyncio.run(scenario())

    assert [call[0] for call in bot.calls] == ["send"]
    assert bot.calls[0][2] == "after"
    assert queue.processed_actions == 1
    assert queue.failed_actions == 1


def test_retry_after_is_retried() -> None:
    async def scenario() -> FakeBot:
        bot = FakeBot()
        bot.retry_once = True
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue([SendMessageAction(text="retry")])
        await queue.close(timeout=2)
        return bot

    bot = asyncio.run(scenario())

    assert [call[0] for call in bot.calls] == ["send"]


def test_enqueue_times_out_when_the_queue_is_full() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        queue = ActionQueue(
            bot,
            -1001234567890,
            capacity=1,
            enqueue_timeout_seconds=0.05,
        )
        await queue.start()
        await queue.enqueue([SleepAction(seconds=0.3)])
        await asyncio.sleep(0.02)
        await queue.enqueue([SleepAction(seconds=0.3)])

        with pytest.raises(ActionQueueError, match="full"):
            await queue.enqueue([SendMessageAction(text="dropped")])

        await queue.close(timeout=0.05)

    asyncio.run(scenario())


def test_close_drains_queued_batches() -> None:
    async def scenario() -> FakeBot:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890, capacity=10)
        await queue.start()
        for index in range(5):
            await queue.enqueue(
                [SendMessageAction(text=f"message {index}")]
            )
        await queue.close(timeout=2)
        return bot

    bot = asyncio.run(scenario())

    assert [call[2] for call in bot.calls] == [
        "message 0",
        "message 1",
        "message 2",
        "message 3",
        "message 4",
    ]


def test_close_timeout_cancels_a_long_sleep() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue([SleepAction(seconds=10)])
        await asyncio.sleep(0.02)

        started = time.monotonic()
        await queue.close(timeout=0.05)
        assert time.monotonic() - started < 1

    asyncio.run(scenario())


def test_queue_rejects_new_work_after_close() -> None:
    async def scenario() -> None:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.close(timeout=1)

        with pytest.raises(ActionQueueError, match="closed"):
            await queue.enqueue([SendMessageAction(text="late")])

    asyncio.run(scenario())


def test_queue_executes_topic_actions() -> None:
    async def scenario() -> FakeBot:
        bot = FakeBot()
        queue = ActionQueue(bot, -1001234567890)
        await queue.start()
        await queue.enqueue(
            [
                CreateTopicAction(name="公告"),
                EditTopicAction(topic_id=17, name="新公告"),
                DeleteTopicAction(topic_id=18),
            ]
        )
        await queue.close(timeout=2)
        return bot

    bot = asyncio.run(scenario())

    assert bot.topics == [
        ("create", "公告"),
        ("edit", "17:新公告"),
        ("delete", 18),
    ]


def test_close_with_zero_timeout_on_an_idle_queue() -> None:
    async def scenario() -> None:
        queue = ActionQueue(FakeBot(), -1001234567890)
        await queue.start()
        await queue.close(timeout=0)

    asyncio.run(scenario())


def test_min_interval_is_applied_between_telegram_actions() -> None:
    async def scenario() -> float:
        bot = FakeBot()
        queue = ActionQueue(
            bot,
            -1001234567890,
            min_interval_seconds=0.05,
        )
        await queue.start()
        started = time.monotonic()
        await queue.enqueue(
            [
                SendMessageAction(text="first"),
                SendMessageAction(text="second"),
            ]
        )
        await queue.close(timeout=2)
        return time.monotonic() - started

    elapsed = asyncio.run(scenario())

    assert elapsed >= 0.04
