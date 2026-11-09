from __future__ import annotations

import pytest

from simple_tg_bot import __version__
from simple_tg_bot.cli import _build_parser
from simple_tg_bot.config import CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH


def test_config_env_var_sets_parser_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CONFIG_ENV_VAR, "/tmp/from-env.json5")

    args = _build_parser().parse_args([])

    assert args.config == "/tmp/from-env.json5"


def test_command_line_config_overrides_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CONFIG_ENV_VAR, "/tmp/from-env.json5")

    args = _build_parser().parse_args(["--config", "/tmp/from-cli.json5"])

    assert args.config == "/tmp/from-cli.json5"


def test_default_config_path_is_used_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)

    args = _build_parser().parse_args([])

    assert args.config == str(DEFAULT_CONFIG_PATH)


def test_version_uses_binary_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"simple-tg-bot {__version__}"
