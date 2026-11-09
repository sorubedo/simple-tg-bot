"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence

from telegram.error import InvalidToken, TelegramError

from . import __version__
from .bot import run_bot
from .config import (
    CONFIG_ENV_VAR,
    DEFAULT_CONFIG_PATH,
    load_config,
)
from .errors import BottgError, ConfigError
from .logging_setup import configure_logging

LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"bottg: configuration error: {exc}", file=sys.stderr)
        return 2

    logging_runtime = configure_logging(
        config.runtime.log_level,
        config.runtime.telegram_log_level,
        token=config.telegram.token,
    )
    LOGGER.debug("loaded configuration from %s", config.source_path)

    try:
        asyncio.run(
            run_bot(config, telegram_logs=logging_runtime.telegram_handler)
        )
    except KeyboardInterrupt:
        LOGGER.info("interrupted")
        return 130
    except BottgError as exc:
        LOGGER.error("%s", exc)
        return 1
    except InvalidToken:
        # PTB's message can contain the token itself, so do not log it.
        LOGGER.error("Telegram rejected the configured bot token")
        return 1
    except TelegramError as exc:
        LOGGER.error("Telegram API error: %s", exc)
        return 1
    except Exception:
        LOGGER.exception("unexpected failure")
        return 1
    finally:
        logging_runtime.close()

    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bottg",
        description=(
            "Stateless, config-driven Telegram bot for one supergroup. "
            "This milestone only verifies the group and maintains long polling."
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        default=os.environ.get(CONFIG_ENV_VAR, str(DEFAULT_CONFIG_PATH)),
        help=(
            "path to the JSON5 config file "
            f"(default: ${CONFIG_ENV_VAR} or {DEFAULT_CONFIG_PATH})"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


__all__ = ["main"]
