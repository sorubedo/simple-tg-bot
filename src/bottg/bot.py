"""Telegram lifecycle: connect, verify the target supergroup, and poll."""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import Chat
from telegram.constants import ChatType
from telegram.error import InvalidToken, TelegramError
from telegram.ext import Application

from . import __version__
from .config import AppConfig
from .errors import StartupError

if TYPE_CHECKING:
    from .logging_setup import TelegramLogHandler

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TargetGroup:
    """Verified target chat details."""

    id: int
    title: str
    username: str | None
    is_forum: bool


def classify_target_chat(chat: Chat) -> TargetGroup:
    """Validate that *chat* is the supergroup bottg is allowed to manage."""

    if chat.type != ChatType.SUPERGROUP:
        raise StartupError(
            f"configured chat {chat.id} is not a supergroup "
            f"(type={chat.type!r}, title={chat.title!r}); "
            "bottg requires a Telegram supergroup"
        )

    is_forum = bool(chat.is_forum)
    if not is_forum:
        LOGGER.warning(
            "target supergroup %r (id=%s) does not have Topics enabled; "
            "topic-aware functionality will be unavailable",
            chat.title,
            chat.id,
        )

    title = chat.title or chat.full_name or f"chat {chat.id}"
    return TargetGroup(
        id=chat.id,
        title=title,
        username=chat.username,
        is_forum=is_forum,
    )


def build_application(config: AppConfig) -> Application:
    """Create the PTB application without registering any handlers.

    Updates are processed strictly one at a time. The in-memory update queue is
    bounded so a slow handler cannot grow memory without limit; when it is
    full, PTB's polling loop waits, and Telegram keeps unconfirmed updates on
    its side.
    """

    update_queue: asyncio.Queue[object] = asyncio.Queue(
        maxsize=config.polling.max_pending_updates
    )
    return (
        Application.builder()
        .token(config.telegram.token)
        .update_queue(update_queue)
        .concurrent_updates(False)
        .build()
    )


async def run_bot(
    config: AppConfig,
    telegram_logs: TelegramLogHandler | None = None,
) -> None:
    """Initialize the bot, verify the group, then run long polling until stopped."""

    app = build_application(config)
    stop_event = asyncio.Event()
    _install_stop_handlers(asyncio.get_running_loop(), stop_event)

    try:
        async with app:
            target = await _resolve_target_group(app, config.telegram.group_id)

            # Bind only after the chat has been verified as the configured
            # supergroup. Logs produced before this point are buffered in the
            # handler and flushed now.
            if telegram_logs is not None:
                await telegram_logs.start(app.bot, target.id)

            LOGGER.info(
                "verified target group %r (id=%s, topics=%s)",
                target.title,
                target.id,
                "on" if target.is_forum else "off",
            )

            updater = app.updater
            if updater is None:  # Defensive: builder always creates one.
                raise StartupError("Telegram updater is unavailable")

            try:
                await app.start()
                await updater.start_polling(
                    poll_interval=config.polling.poll_interval,
                    timeout=config.polling.timeout_seconds,
                    drop_pending_updates=config.polling.drop_pending_updates,
                    allowed_updates=config.polling.allowed_updates,
                )
                LOGGER.info("%s", _ready_message(config, target))
                await stop_event.wait()
            finally:
                try:
                    if updater.running:
                        await updater.stop()
                finally:
                    LOGGER.info("bottg stopped")
                    try:
                        if telegram_logs is not None:
                            await telegram_logs.aclose()
                    finally:
                        if app.running:
                            await app.stop()
    finally:
        # Handles failures before the sender was started, and makes cleanup
        # idempotent if the inner shutdown path was skipped.
        if telegram_logs is not None:
            await telegram_logs.aclose()


async def _resolve_target_group(
    app: Application, group_id: int
) -> TargetGroup:
    try:
        chat = await app.bot.get_chat(group_id)
    except InvalidToken as exc:
        raise StartupError("Telegram rejected the configured bot token") from exc
    except TelegramError as exc:
        raise StartupError(
            f"cannot access Telegram group {group_id}: {exc}"
        ) from exc
    return classify_target_chat(chat)


def _ready_message(config: AppConfig, target: TargetGroup) -> str:
    username = f" @{target.username}" if target.username else ""
    topics = "on" if target.is_forum else "off"
    return (
        f"bottg {__version__} initialized | "
        f"target supergroup {target.title!r} (id={target.id}{username}) | "
        f"topics={topics} | "
        f"long polling started (timeout={config.polling.timeout_seconds}s)"
    )


def _install_stop_handlers(
    loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event
) -> None:
    """Turn SIGINT/SIGTERM into a graceful polling shutdown."""

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows' ProactorEventLoop does not support add_signal_handler.
            signal.signal(
                sig,
                lambda _signum, _frame, _loop=loop, _event=stop_event: (
                    _loop.call_soon_threadsafe(_event.set)
                ),
            )
