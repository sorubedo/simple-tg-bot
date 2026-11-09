from __future__ import annotations

from pathlib import Path

import pytest

from bottg.config import ConfigError, load_config


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.json5"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_json5_features_and_defaults(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          // JSON5 comments and trailing commas are supported.
          telegram: {
            group_id: -1001234567890,
          },
          polling: {
            timeout_seconds: 15,
            allowed_updates: ["message", "callback_query",],
          },
          runtime: { log_level: "debug" },
          actions: {
            max_pending: 5,
            enqueue_timeout_seconds: 1,
            drain_timeout_seconds: 2,
            min_interval_seconds: 0.1,
          },
          programs: {
            sample: {
              command: ["/bin/true"],
              hooks: ["message"],
              timeout_seconds: 5,
              message: {
                topics: [null, 3],
                ignore_bots: false,
                download_attachments: true,
                max_attachment_bytes: 1048576,
              },
            },
          },
        }
        """,
    )

    config = load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})

    assert config.telegram.token == "123456:test-secret"
    assert config.telegram.group_id == -1001234567890
    assert config.polling.timeout_seconds == 15
    assert config.polling.poll_interval == 0.0
    assert config.polling.drop_pending_updates is False
    assert config.polling.allowed_updates == ("message", "callback_query")
    assert config.polling.max_pending_updates == 100
    assert config.runtime.log_level == "DEBUG"
    assert config.runtime.telegram_log_level == "INFO"
    assert config.actions.max_pending == 5
    assert config.actions.enqueue_timeout_seconds == 1.0
    assert config.actions.drain_timeout_seconds == 2.0
    assert config.actions.min_interval_seconds == 0.1
    sample = config.programs["sample"]
    assert sample.name == "sample"
    assert sample.command == ("/bin/true",)
    assert sample.hooks == frozenset({"message"})
    assert sample.timeout_seconds == 5.0
    assert sample.cwd == path.parent.resolve()
    assert sample.message.topics == (None, 3)
    assert sample.message.ignore_bots is False
    assert sample.message.download_attachments is True
    assert sample.message.max_attachment_bytes == 1048576
    assert config.source_path == path


def test_environment_token_wins_over_file_token(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: {
            token: "111:file-secret",
            group_id: -1001234567890,
          },
        }
        """,
    )

    config = load_config(path, env={"BOTTG_TOKEN": "222:env-secret"})

    assert config.telegram.token == "222:env-secret"


def test_file_token_is_used_without_environment_token(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: {
            token: "333:file-secret",
            group_id: -1001234567890,
          },
        }
        """,
    )

    config = load_config(path, env={})

    assert config.telegram.token == "333:file-secret"


def test_missing_token_is_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
        }
        """,
    )

    with pytest.raises(ConfigError, match="BOTTG_TOKEN"):
        load_config(path, env={})


def test_group_id_must_be_negative(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: {
            group_id: 1234567890,
          },
        }
        """,
    )

    with pytest.raises(ConfigError, match="must be negative"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: {
            group_id: -1001234567890,
            group: "typo",
          },
        }
        """,
    )

    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_invalid_log_level_is_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          runtime: { log_level: "LOUD" },
        }
        """,
    )

    with pytest.raises(ConfigError, match="log_level"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_telegram_log_level_can_be_configured(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          runtime: { telegram_log_level: "warning" },
          polling: { max_pending_updates: 7 },
        }
        """,
    )

    config = load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})

    assert config.runtime.telegram_log_level == "WARNING"
    assert config.polling.max_pending_updates == 7


@pytest.mark.parametrize("disabled", ["null", '"off"'])
def test_telegram_logging_can_be_disabled(
    tmp_path: Path, disabled: str
) -> None:
    path = write_config(
        tmp_path,
        f"""
        {{
          telegram: {{ group_id: -1001234567890 }},
          runtime: {{ telegram_log_level: {disabled} }},
        }}
        """,
    )

    config = load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})

    assert config.runtime.telegram_log_level is None


def test_invalid_telegram_log_level_is_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          runtime: { telegram_log_level: "LOUD" },
        }
        """,
    )

    with pytest.raises(ConfigError, match="telegram_log_level"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_negative_max_pending_updates_is_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          polling: { max_pending_updates: -1 },
        }
        """,
    )

    with pytest.raises(ConfigError, match="max_pending_updates"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_invalid_allowed_updates_is_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          polling: { allowed_updates: ["message", 42] },
        }
        """,
    )

    with pytest.raises(ConfigError, match="allowed_updates"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_program_cwd_is_relative_to_the_config_file(tmp_path: Path) -> None:
    script_dir = tmp_path / "hooks"
    script_dir.mkdir()
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          programs: {
            sample: {
              command: ["/bin/true"],
              hooks: ["startup", "shutdown"],
              cwd: "hooks",
              env: { HOOK_MODE: "test" },
              max_output_bytes: 1024,
            },
          },
        }
        """,
    )

    config = load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})

    sample = config.programs["sample"]
    assert sample.cwd == script_dir.resolve()
    assert sample.env == {"HOOK_MODE": "test"}
    assert sample.hooks == frozenset({"startup", "shutdown"})
    assert sample.max_output_bytes == 1024
    assert sample.message.topics is None
    assert sample.message.ignore_bots is True
    assert sample.message.download_attachments is False
    assert sample.message.max_attachment_bytes == 20_000_000


@pytest.mark.parametrize(
    "program_body, message",
    [
        ('{ hooks: ["message"] }', "command"),
        ('{ command: ["/bin/true"] }', "hooks"),
        ('{ command: "/bin/true", hooks: ["message"] }', "command"),
        ('{ command: [], hooks: ["message"] }', "command"),
        ('{ command: ["/bin/true"], hooks: [] }', "hooks"),
        ('{ command: ["/bin/true"], hooks: ["typo"] }', "hooks"),
    ],
)
def test_invalid_program_shape_is_rejected(
    tmp_path: Path, program_body: str, message: str
) -> None:
    path = write_config(
        tmp_path,
        f"""
        {{
          telegram: {{ group_id: -1001234567890 }},
          programs: {{ sample: {program_body} }},
        }}
        """,
    )

    with pytest.raises(ConfigError, match=message):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_program_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          programs: {
            sample: {
              command: ["/bin/true"],
              hooks: ["message"],
              timeot_seconds: 5,
            },
          },
        }
        """,
    )

    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_message_filters_require_message_hook(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          programs: {
            sample: {
              command: ["/bin/true"],
              hooks: ["startup"],
              message: { topics: [1] },
            },
          },
        }
        """,
    )

    with pytest.raises(ConfigError, match="only valid"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_message_filters_are_valid_with_edited_message_hook(
    tmp_path: Path,
) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          programs: {
            sample: {
              command: ["/bin/true"],
              hooks: ["edited_message"],
              message: { topics: [null, 3] },
            },
          },
        }
        """,
    )

    config = load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})

    assert config.programs["sample"].hooks == frozenset(
        {"edited_message"}
    )
    assert config.programs["sample"].message.topics == (None, 3)


@pytest.mark.parametrize(
    "topics",
    ["[]", "[0]", "[-1]", '["general"]', "[true]"],
)
def test_invalid_topic_filters_are_rejected(
    tmp_path: Path, topics: str
) -> None:
    path = write_config(
        tmp_path,
        f"""
        {{
          telegram: {{ group_id: -1001234567890 }},
          programs: {{
            sample: {{
              command: ["/bin/true"],
              hooks: ["message"],
              message: {{ topics: {topics} }},
            }},
          }},
        }}
        """,
    )

    with pytest.raises(ConfigError, match="topics"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5"])
def test_invalid_max_attachment_bytes_is_rejected(
    tmp_path: Path, value: str
) -> None:
    path = write_config(
        tmp_path,
        f"""
        {{
          telegram: {{ group_id: -1001234567890 }},
          programs: {{
            sample: {{
              command: ["/bin/true"],
              hooks: ["message"],
              message: {{ max_attachment_bytes: {value} }},
            }},
          }},
        }}
        """,
    )

    with pytest.raises(ConfigError, match="max_attachment_bytes"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


@pytest.mark.parametrize("value", ['"true"', "1", "null"])
def test_invalid_download_attachments_is_rejected(
    tmp_path: Path, value: str
) -> None:
    path = write_config(
        tmp_path,
        f"""
        {{
          telegram: {{ group_id: -1001234567890 }},
          programs: {{
            sample: {{
              command: ["/bin/true"],
              hooks: ["message"],
              message: {{ download_attachments: {value} }},
            }},
          }},
        }}
        """,
    )

    with pytest.raises(ConfigError, match="download_attachments"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_unknown_message_hook_config_key_is_rejected(
    tmp_path: Path,
) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          programs: {
            sample: {
              command: ["/bin/true"],
              hooks: ["message"],
              message: { topic: [1] },
            },
          },
        }
        """,
    )

    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_program_cwd_must_exist(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          programs: {
            sample: {
              command: ["/bin/true"],
              hooks: ["message"],
              cwd: "missing",
            },
          },
        }
        """,
    )

    with pytest.raises(ConfigError, match="not a directory"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_program_env_values_must_be_strings(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        {
          telegram: { group_id: -1001234567890 },
          programs: {
            sample: {
              command: ["/bin/true"],
              hooks: ["message"],
              env: { PORT: 8080 },
            },
          },
        }
        """,
    )

    with pytest.raises(ConfigError, match="env"):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


@pytest.mark.parametrize(
    "actions_body, message",
    [
        ("{ max_pending: 0 }", "max_pending"),
        ("{ enqueue_timeout_seconds: -1 }", "enqueue_timeout_seconds"),
        ("{ drain_timeout_seconds: Infinity }", "finite"),
        ("{ unknown: 1 }", "unknown key"),
    ],
)
def test_invalid_actions_config_is_rejected(
    tmp_path: Path, actions_body: str, message: str
) -> None:
    path = write_config(
        tmp_path,
        f"""
        {{
          telegram: {{ group_id: -1001234567890 }},
          actions: {actions_body},
        }}
        """,
    )

    with pytest.raises(ConfigError, match=message):
        load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})


def test_example_config_is_valid() -> None:
    path = Path(__file__).resolve().parents[1] / "config.example.json5"

    config = load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})

    assert config.telegram.group_id == -1001234567890
    assert config.programs["echo"].hooks == frozenset(
        {"message", "edited_message"}
    )
    assert config.programs["echo"].message.topics is None
    assert config.programs["echo"].message.ignore_bots is True
    assert config.programs["reaction"].hooks == frozenset({"message"})
    assert config.programs["cleanup"].hooks == frozenset({"message"})
    assert config.programs["topics"].hooks == frozenset({"message"})
    assert config.programs["media"].hooks == frozenset({"message"})
    assert config.programs["media"].message.download_attachments is False
    assert config.programs["lifecycle"].hooks == frozenset(
        {"startup", "shutdown"}
    )


def test_example_config_scripts_exist() -> None:
    path = Path(__file__).resolve().parents[1] / "config.example.json5"
    config = load_config(path, env={"BOTTG_TOKEN": "123456:test-secret"})

    for program in config.programs.values():
        assert program.command[0] in {"python", "python3"}
        assert (program.cwd / program.command[1]).is_file()
