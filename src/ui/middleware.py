"""ACL middleware + `deny_access` helper.

Replaces the 14 copies of `if not is_allowed(...): await deny_access(...)` in
the old `bot.py`. Runs once per inbound message / callback, injecting
`chat_id` and `cl` (per-chat logger) into the handler's kwargs. Poll-answer
updates carry no chat id, so they receive only `ctx`; their handlers must bind
answers to a previously authorized chat/session.

Gate-managed callbacks (`perm:`, `aq:`, `plan:`) bypass the allowlist —
`TelegramInteractionGate` validates ownership itself.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from ..handlers.context import BotContext
from .markdown import send_md

_GATE_CALLBACK_PREFIXES = ("perm:", "aq:", "plan:")


async def deny_access(message: Message, ctx: BotContext) -> None:
    """Log the rejection and tell the chat it is not on the allowlist."""
    ctx.bot_logs.for_chat(message.chat.id).warning(
        "access denied for chat_id=%s user=%s",
        message.chat.id,
        message.from_user.id if message.from_user else None,
    )
    await send_md(message, ctx.tr.t("access_denied", chat_id=message.chat.id))


def _chat_id_of(event: TelegramObject) -> int | None:
    """Extract the chat id from a message or callback event, else None."""
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery):
        msg = event.message
        if msg is not None and hasattr(msg, "chat"):
            return msg.chat.id
    return None


class AclMiddleware(BaseMiddleware):
    """Allowlist gate run once per inbound message/callback."""

    def __init__(self, ctx: BotContext) -> None:
        """Store the bot context shared with every handler invocation."""
        self._ctx = ctx

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Inject ctx/chat_id/cl, enforce the allowlist, then call the handler."""
        data["ctx"] = self._ctx
        chat_id = _chat_id_of(event)
        if chat_id is None:
            return await handler(event, data)
        data["chat_id"] = chat_id
        data["cl"] = self._ctx.bot_logs.for_chat(chat_id)

        # Gate-managed callbacks validate ownership inside the gate; ACL is
        # bypassed so callbacks fired from chats outside the allowlist (e.g.
        # legitimate ones added mid-session) still resolve.
        if isinstance(event, CallbackQuery) and (event.data or "").startswith(
            _GATE_CALLBACK_PREFIXES
        ):
            return await handler(event, data)

        if not self._ctx.is_allowed(chat_id):
            if isinstance(event, Message):
                await deny_access(event, self._ctx)
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    self._ctx.tr.t("unauthorized_callback"), show_alert=True
                )
            return None

        return await handler(event, data)
