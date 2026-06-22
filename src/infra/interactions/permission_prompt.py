"""Generic Allow / Deny / Always-allow-this-session inline-button flow."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from typing import TYPE_CHECKING, Any

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from claude_agent_sdk.types import PermissionRuleValue, PermissionUpdate

if TYPE_CHECKING:
    from .gate import TelegramInteractionGate

log = logging.getLogger(__name__)


async def handle(
    gate: TelegramInteractionGate,
    chat_id: int,
    tool_name: str,
    tool_input: dict[str, Any],
    ctx: ToolPermissionContext,
) -> PermissionResultAllow | PermissionResultDeny:
    """Prompt the user with Allow/Deny/Always buttons and await the verdict.

    Registers the request in ``gate._pending`` keyed by a fresh id, sends the
    inline keyboard, then blocks on the future until `on_callback` resolves it
    or the timeout fires (timeout → deny, prompt deleted). "Always" returns an
    Allow carrying a session-scoped `addRules` permission update.
    """
    t = gate._t
    request_id = secrets.token_hex(8)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    gate._pending[request_id] = (fut, tool_name, chat_id, None)

    text = gate._format_request(tool_name, tool_input, ctx)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t.t("btn_allow"),
                    callback_data=f"perm:{request_id}:allow",
                ),
                InlineKeyboardButton(
                    text=t.t("btn_deny"),
                    callback_data=f"perm:{request_id}:deny",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t.t("btn_always"),
                    callback_data=f"perm:{request_id}:always",
                ),
            ],
        ]
    )

    try:
        sent = await gate._bot.send_message(
            chat_id, text, reply_markup=kb, parse_mode=None
        )
    except Exception:
        log.exception("permission prompt failed")
        gate._pending.pop(request_id, None)
        return PermissionResultDeny(message=t.t("permission_failed_prompt"))
    gate._pending[request_id] = (fut, tool_name, chat_id, sent.message_id)

    decision = "deny"
    try:
        decision = await asyncio.wait_for(fut, timeout=gate._timeout)
    except TimeoutError:
        # Drop the stale prompt before announcing the timeout so the chat
        # does not keep an orphaned set of buttons around.
        await gate._delete_prompt(chat_id, sent.message_id)
        with contextlib.suppress(Exception):
            await gate._bot.send_message(
                chat_id, t.t("approval_timeout"), parse_mode=None
            )
    finally:
        gate._pending.pop(request_id, None)

    if decision == "always":
        return PermissionResultAllow(
            updated_permissions=[
                PermissionUpdate(
                    type="addRules",
                    behavior="allow",
                    rules=[PermissionRuleValue(tool_name=tool_name)],
                    destination="session",
                )
            ]
        )
    if decision == "allow":
        return PermissionResultAllow()
    return PermissionResultDeny(message=t.t("permission_denied_via_telegram"))


async def on_callback(
    gate: TelegramInteractionGate, callback: CallbackQuery
) -> None:
    """Resolve a `perm:` button tap into the pending future's decision.

    Validates freshness and chat ownership, sets the decision, and deletes the
    prompt. No-op for stale or cross-chat callbacks.
    """
    t = gate._t
    data = callback.data or ""
    if not data.startswith("perm:"):
        return
    try:
        _, request_id, decision = data.split(":", 2)
    except ValueError:
        await callback.answer()
        return

    msg = callback.message if isinstance(callback.message, Message) else None

    entry = gate._pending.get(request_id)
    if entry is None or entry[0].done():
        await callback.answer(t.t("callback_outdated"), show_alert=False)
        # Outdated prompt — delete it so the chat does not accumulate
        # orphaned button rows.
        if msg is not None:
            with contextlib.suppress(Exception):
                await msg.delete()
        return

    fut, _tool_name, expected_chat_id, _prompt_msg_id = entry

    # Authz: only callbacks from the chat the request was issued to are honored.
    actual_chat_id = msg.chat.id if msg is not None else None
    if actual_chat_id != expected_chat_id:
        await callback.answer(t.t("unauthorized_callback"), show_alert=True)
        return

    if decision not in {"allow", "deny", "always"}:
        await callback.answer()
        return

    fut.set_result(decision)
    gate._cl(expected_chat_id).info(
        "permission %s for tool %r (request %s)",
        decision,
        _tool_name,
        request_id,
    )

    verdict = t.t(f"verdict_{decision}")
    # Delete the prompt message itself — verdict goes in the callback
    # toast, so there is no need to keep the bubble.
    if msg is not None:
        try:
            await msg.delete()
        except Exception:
            log.debug("could not delete permission prompt", exc_info=True)
    await callback.answer(verdict)
