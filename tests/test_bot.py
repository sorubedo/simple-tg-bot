from __future__ import annotations

import logging

import pytest
from telegram import Chat
from telegram.constants import ChatType

from bottg.bot import classify_target_chat
from bottg.errors import StartupError


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
