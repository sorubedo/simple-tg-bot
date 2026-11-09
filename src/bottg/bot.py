"""Telegram lifecycle: connect, verify the target group, and dispatch hooks."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import Chat, Update
from telegram.constants import ChatType
from telegram.error import InvalidToken, TelegramError
from telegram.ext import (
    Application,
    CallbackContext,
    MessageHandler,
    Updater,
    filters,
)

from . import __version__
from .action_queue import ActionQueue
from .config import AppConfig, HOOK_MESSAGE, HOOK_SHUTDOWN, HOOK_STARTUP
from .errors import StartupError
from .programs import ProgramDispatcher

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
    """Create the PTB application without registering any handlers yet.

    Updates are processed strictly one at a time. The in-memory update queue is
    bounded so a slow hook program cannot grow memory without limit; when it is
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
    """Initialize the bot, verify the group, and run hooks until stopped."""

    app = build_application(config)
    stop_event = asyncio.Event()
    _install_stop_handlers(asyncio.get_running_loop(), stop_event)

    action_queue: ActionQueue | None = None
    dispatcher: ProgramDispatcher | None = None

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

                action_queue = ActionQueue(
                    app.bot,
                    target.id,
                    capacity=config.actions.max_pending,
                    min_interval_seconds=(
                        config.actions.min_interval_seconds
                    ),
                    enqueue_timeout_seconds=(
                        config.actions.enqueue_timeout_seconds
                    ),
                )
                await action_queue.start()

                dispatcher = ProgramDispatcher(
                    config.programs,
                    group=target,
                    sink=action_queue,
                )
                app.add_handler(
                    MessageHandler(
                        filters.UpdateType.MESSAGE
                        & filters.Chat(target.id),
                        _make_message_handler(dispatcher),
                    )
                )
                _warn_if_message_hooks_disabled(config, dispatcher)

                await dispatcher.run_hook(HOOK_STARTUP)
                await updater.start_polling(
                    poll_interval=config.polling.poll_interval,
                    timeout=config.polling.timeout_seconds,
                    drop_pending_updates=(
                        config.polling.drop_pending_updates
                    ),
                    allowed_updates=config.polling.allowed_updates,
                )
                LOGGER.info("%s", _ready_message(config, target))
                await stop_event.wait()
            finally:
                await _shutdown(
                    app=app,
                    updater=updater,
                    dispatcher=dispatcher,
                    action_queue=action_queue,
                    telegram_logs=telegram_logs,
                    drain_timeout=config.actions.drain_timeout_seconds,
                    reason="signal" if stop_event.is_set() else "error",
                )
    finally:
        # Handles failures before the started section was entered, and makes
        # cleanup idempotent if the inner shutdown path was skipped.
        if action_queue is not None:
            await action_queue.close(
                timeout=config.actions.drain_timeout_seconds
            )
        if telegram_logs is not None:
            await telegram_logs.aclose()


async def _shutdown(
    *,
    app: Application,
    updater: Updater,
    dispatcher: ProgramDispatcher | None,
    action_queue: ActionQueue | None,
    telegram_logs: TelegramLogHandler | None,
    drain_timeout: float,
    reason: str,
) -> None:
    """Stop polling, run shutdown hooks, and drain the action queue.

    ``Application.stop()`` is called before the shutdown hook so no update
    callback can enqueue actions while the queue is closing. The bot itself
    stays initialized until the surrounding ``async with`` exits, so shutdown
    hook actions and the final log flush can still use the Telegram API.
    """

    try:
        if updater.running:
            await updater.stop()
    finally:
        try:
            if app.running:
                await app.stop()
        finally:
            try:
                if dispatcher is not None:
                    await dispatcher.run_hook(
                        HOOK_SHUTDOWN, reason=reason
                    )
            finally:
                try:
                    if action_queue is not None:
                        await action_queue.close(timeout=drain_timeout)
                finally:
                    try:
                        LOGGER.info("bottg stopped")
                    finally:
                        if telegram_logs is not None:
                            await telegram_logs.aclose()


def _make_message_handler(
    dispatcher: ProgramDispatcher,
) -> Callable[[Update, CallbackContext], Awaitable[None]]:
    async def handle_message(
        update: Update, _context: CallbackContext
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        try:
            await dispatcher.dispatch_message(
                message, update_id=update.update_id
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("message hook dispatch failed")

    return handle_message


def _warn_if_message_hooks_disabled(
    config: AppConfig, dispatcher: ProgramDispatcher
) -> None:
    allowed = config.polling.allowed_updates
    if (
        dispatcher.has_message_hooks
        and allowed
        and HOOK_MESSAGE not in allowed
    ):
        LOGGER.warning(
            "at least one program subscribes to the message hook, but "
            "polling.allowed_updates excludes 'message'; message hooks will "
            "not fire"
        )


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
        f"programs={len(config.programs)} | "
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
