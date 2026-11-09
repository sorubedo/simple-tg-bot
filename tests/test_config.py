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
          programs: {
            sample: { command: ["/bin/true"], },
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
    assert config.programs["sample"] == {"command": ["/bin/true"]}
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
