from __future__ import annotations

from pathlib import Path

import pytest

from bottg.actions import (
    ACTION_PROTOCOL,
    MAX_CAPTION_UTF16,
    MAX_MESSAGE_UTF16,
    MAX_TOPIC_NAME_LENGTH,
    ActionContext,
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
    parse_actions,
)
from bottg.errors import ActionPayloadError


@pytest.mark.parametrize("text", ["", "   \n\t", "\ufeff"])
def test_empty_stdout_means_no_actions(text: str) -> None:
    assert parse_actions(text) == []


def test_single_action_defaults_to_the_trigger_context() -> None:
    actions = parse_actions(
        '{"action": "send_message", "text": "hello"}',
        context=ActionContext(topic_id=17, message_id=99),
    )

    assert actions == [
        SendMessageAction(text="hello", topic_id=17)
    ]


def test_explicit_null_topic_means_general_topic() -> None:
    actions = parse_actions(
        '{"action": "send_message", "text": "hello", "topic_id": null}',
        context=ActionContext(topic_id=17, message_id=99),
    )

    assert actions == [SendMessageAction(text="hello", topic_id=None)]


def test_send_message_optional_fields_are_parsed() -> None:
    actions = parse_actions(
        """
        {
          "action": "send_message",
          "text": "<b>hello</b>",
          "topic_id": 3,
          "reply_to_message_id": 12,
          "parse_mode": "html",
          "disable_notification": true,
          "disable_web_page_preview": true
        }
        """,
        context=ActionContext(topic_id=17, message_id=99),
    )

    assert actions == [
        SendMessageAction(
            text="<b>hello</b>",
            topic_id=3,
            reply_to_message_id=12,
            parse_mode="HTML",
            disable_notification=True,
            disable_web_page_preview=True,
        )
    ]


def test_send_photo_accepts_file_id_url_and_local_path(
    tmp_path: Path,
) -> None:
    context = ActionContext(topic_id=17, base_dir=tmp_path)

    assert parse_actions(
        '{"action": "send_photo", "photo": "AgAC-photo"}', context=context
    ) == [
        SendPhotoAction(
            photo=FileSource("file_id", "AgAC-photo"), topic_id=17
        )
    ]
    assert parse_actions(
        '{"action": "send_photo", "photo": "https://example.com/a.jpg"}',
        context=context,
    ) == [
        SendPhotoAction(
            photo=FileSource("url", "https://example.com/a.jpg"),
            topic_id=17,
        )
    ]
    assert parse_actions(
        '{"action": "send_photo", "photo": {"path": "images/a.jpg"}}',
        context=context,
    ) == [
        SendPhotoAction(
            photo=FileSource("path", str((tmp_path / "images/a.jpg").resolve())),
            topic_id=17,
        )
    ]


def test_send_photo_optional_fields_are_parsed() -> None:
    actions = parse_actions(
        """
        {
          "action": "send_photo",
          "photo": {"file_id": "AgAC-photo"},
          "caption": "<b>look</b>",
          "topic_id": 3,
          "reply_to_message_id": 12,
          "parse_mode": "html",
          "disable_notification": true,
          "has_spoiler": true
        }
        """,
        context=ActionContext(topic_id=17, message_id=99),
    )

    assert actions == [
        SendPhotoAction(
            photo=FileSource("file_id", "AgAC-photo"),
            caption="<b>look</b>",
            topic_id=3,
            reply_to_message_id=12,
            parse_mode="HTML",
            disable_notification=True,
            has_spoiler=True,
        )
    ]


def test_send_document_accepts_local_path_and_caption(
    tmp_path: Path,
) -> None:
    actions = parse_actions(
        """
        {
          "action": "send_document",
          "document": {"path": "reports/final.pdf"},
          "caption": "report",
          "topic_id": null,
          "reply_to_message_id": 4,
          "parse_mode": "MarkdownV2",
          "disable_notification": false
        }
        """,
        context=ActionContext(topic_id=17, base_dir=tmp_path),
    )

    assert actions == [
        SendDocumentAction(
            document=FileSource(
                "path", str((tmp_path / "reports/final.pdf").resolve())
            ),
            caption="report",
            topic_id=None,
            reply_to_message_id=4,
            parse_mode="MarkdownV2",
        )
    ]


def test_caption_length_is_measured_in_utf16_code_units() -> None:
    action = parse_actions(
        '{"action": "send_photo", "photo": "f", "caption": "%s"}'
        % ("a" * MAX_CAPTION_UTF16)
    )
    assert isinstance(action[0], SendPhotoAction)

    with pytest.raises(ActionPayloadError, match="too long"):
        parse_actions(
            '{"action": "send_photo", "photo": "f", "caption": "%s"}'
            % ("a" * (MAX_CAPTION_UTF16 + 1))
        )


def test_caption_length_counts_emoji_as_two_utf16_units() -> None:
    emoji = "😀"
    accepted = parse_actions(
        '{"action": "send_document", "document": "f", "caption": "%s"}'
        % (emoji * (MAX_CAPTION_UTF16 // 2))
    )
    assert isinstance(accepted[0], SendDocumentAction)

    with pytest.raises(ActionPayloadError, match="too long"):
        parse_actions(
            '{"action": "send_document", "document": "f", "caption": "%s"}'
            % (emoji * (MAX_CAPTION_UTF16 // 2 + 1))
        )


def test_array_and_wrapper_action_groups() -> None:
    payload = (
        "["
        '{"action": "delete_message", "message_id": 1},'
        '{"action": "set_reaction", "message_id": 2, "emoji": "👍", '
        '"is_big": true},'
        '{"action": "sleep", "seconds": 1.5}'
        "]"
    )
    expected = [
        DeleteMessageAction(message_id=1),
        SetReactionAction(message_id=2, emoji="👍", is_big=True),
        SleepAction(seconds=1.5),
    ]

    assert parse_actions(payload) == expected
    assert (
        parse_actions(
            '{"protocol": "%s", "actions": %s}'
            % (ACTION_PROTOCOL, payload)
        )
        == expected
    )


def test_delete_and_reaction_default_to_the_trigger_message() -> None:
    actions = parse_actions(
        '[{"action": "delete_message"}, '
        '{"action": "set_reaction", "emoji": "🔥"}]',
        context=ActionContext(topic_id=17, message_id=99),
    )

    assert actions == [
        DeleteMessageAction(message_id=99),
        SetReactionAction(message_id=99, emoji="🔥"),
    ]


def test_topic_actions_default_to_the_trigger_topic() -> None:
    actions = parse_actions(
        '[{"action": "delete_topic"}, '
        '{"action": "edit_topic", "name": "新名字"}]',
        context=ActionContext(topic_id=17, message_id=99),
    )

    assert actions == [
        DeleteTopicAction(topic_id=17),
        EditTopicAction(topic_id=17, name="新名字"),
    ]


def test_topic_actions_accept_explicit_topic_and_name() -> None:
    assert parse_actions(
        '{"action": "create_topic", "name": "公告"}'
    ) == [CreateTopicAction(name="公告")]
    assert parse_actions(
        '{"action": "delete_topic", "topic_id": 3}'
    ) == [DeleteTopicAction(topic_id=3)]
    assert parse_actions(
        '{"action": "edit_topic", "topic_id": 3, "name": "改名"}'
    ) == [EditTopicAction(topic_id=3, name="改名")]


def test_topic_actions_require_a_trigger_topic() -> None:
    with pytest.raises(ActionPayloadError, match="topic_id is required"):
        parse_actions('{"action": "delete_topic"}')

    with pytest.raises(ActionPayloadError, match="topic_id is required"):
        parse_actions('{"action": "edit_topic", "name": "x"}')


def test_topic_name_validation() -> None:
    assert parse_actions(
        '{"action": "create_topic", "name": "%s"}'
        % ("名" * MAX_TOPIC_NAME_LENGTH)
    ) == [CreateTopicAction(name="名" * MAX_TOPIC_NAME_LENGTH)]

    for name in ("", "   ", "名" * (MAX_TOPIC_NAME_LENGTH + 1)):
        with pytest.raises(ActionPayloadError, match="name"):
            parse_actions(
                '{"action": "create_topic", "name": "%s"}' % name
            )


def test_missing_message_id_without_a_trigger_is_rejected() -> None:
    with pytest.raises(ActionPayloadError, match="message_id is required"):
        parse_actions('{"action": "delete_message"}')

    with pytest.raises(ActionPayloadError, match="message_id is required"):
        parse_actions('{"action": "set_reaction", "emoji": "👍"}')


def test_unknown_action_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(ActionPayloadError, match="unknown action"):
        parse_actions('{"action": "forward_message"}')

    with pytest.raises(ActionPayloadError, match="unknown field"):
        parse_actions(
            '{"action": "send_message", "text": "hi", "chat_id": 1}'
        )

    with pytest.raises(ActionPayloadError, match="unknown field"):
        parse_actions('[{"action": "sleep", "seconds": 1, "jitter": 1}]')


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "42",
        '"send_message"',
        "{}",
        '{"actions": {}}',
        '{"action": 1}',
        '{"action": "send_message"}',
        '{"action": "send_message", "text": ""}',
        '{"action": "send_message", "text": 1}',
        '{"action": "send_message", "text": "hi", "topic_id": 0}',
        '{"action": "send_message", "text": "hi", "topic_id": "3"}',
        '{"action": "send_message", "text": "hi", "parse_mode": "XML"}',
        '{"action": "send_photo"}',
        '{"action": "send_photo", "photo": ""}',
        '{"action": "send_photo", "photo": "   "}',
        '{"action": "send_photo", "photo": 3}',
        '{"action": "send_photo", "photo": {"path": ""}}',
        '{"action": "send_photo", "photo": {"url": "ftp://example.com/a"}}',
        '{"action": "send_photo", "photo": {"file_id": "x", "path": "a"}}',
        '{"action": "send_photo", "photo": {"file_id": "x", "extra": "a"}}',
        '{"action": "send_photo", "photo": "x", "caption": ""}',
        '{"action": "send_photo", "photo": "x", "caption": 3}',
        '{"action": "send_photo", "photo": "x", "has_spoiler": "yes"}',
        '{"action": "send_document"}',
        '{"action": "send_document", "document": "x", "has_spoiler": true}',
        '{"action": "delete_message", "message_id": null}',
        '{"action": "delete_message", "message_id": true}',
        '{"action": "set_reaction", "message_id": 1, "emoji": ""}',
        '{"action": "set_reaction", "message_id": 1, "emoji": 3}',
        '{"action": "create_topic"}',
        '{"action": "create_topic", "name": 3}',
        '{"action": "create_topic", "name": "x", "topic_id": 1}',
        '{"action": "delete_topic", "topic_id": 0}',
        '{"action": "delete_topic", "topic_id": null}',
        '{"action": "delete_topic", "extra": 1}',
        '{"action": "edit_topic", "topic_id": 1}',
        '{"action": "edit_topic", "topic_id": 1, "name": ""}',
        '{"action": "edit_topic", "topic_id": null, "name": "x"}',
        '{"action": "edit_topic", "topic_id": 1, "name": 3}',
        '{"action": "sleep"}',
        '{"action": "sleep", "seconds": "1"}',
        '{"action": "sleep", "seconds": 0}',
        '{"action": "sleep", "seconds": -1}',
        '{"action": "sleep", "seconds": NaN}',
        '{"action": "sleep", "seconds": Infinity}',
        '{"action": "sleep", "seconds": 3601}',
        '{"actions": [], "protocol": "other/1"}',
    ],
)
def test_invalid_payloads_are_rejected(payload: str) -> None:
    with pytest.raises(ActionPayloadError):
        parse_actions(payload, context=ActionContext(message_id=1))


def test_message_length_is_measured_in_utf16_code_units() -> None:
    action = parse_actions(
        '{"action": "send_message", "text": "%s"}'
        % ("a" * MAX_MESSAGE_UTF16)
    )
    assert isinstance(action[0], SendMessageAction)

    with pytest.raises(ActionPayloadError, match="too long"):
        parse_actions(
            '{"action": "send_message", "text": "%s"}'
            % ("a" * (MAX_MESSAGE_UTF16 + 1))
        )


def test_parse_mode_accepts_markdown_variants() -> None:
    actions = parse_actions(
        '{"action": "send_message", "text": "hi", '
        '"parse_mode": "markdownv2"}'
    )
    assert actions[0].parse_mode == "MarkdownV2"


def test_describe_action_does_not_include_message_text() -> None:
    description = describe_action(
        SendMessageAction(text="secret", topic_id=4)
    )
    assert "send_message" in description
    assert "topic=4" in description
    assert "secret" not in description


def test_describe_media_action_does_not_include_source_value() -> None:
    description = describe_action(
        SendPhotoAction(
            photo=FileSource("path", "/home/alice/private/photo.jpg"),
            caption="secret caption",
        )
    )
    assert "send_photo" in description
    assert "source=path" in description
    assert "/home/alice" not in description
    assert "secret caption" not in description
