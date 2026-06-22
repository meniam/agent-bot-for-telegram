"""ExitPlanMode SDK tool flow.

Sends the proposed plan as Markdown, then posts a compact two-button
Approve / Reject prompt. Plain-text input while the prompt is on screen
counts as rejection-with-feedback (Claude is told to address the feedback
and call ExitPlanMode again).
"""

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
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

if TYPE_CHECKING:
    from .gate import TelegramInteractionGate

log = logging.getLogger(__name__)


async def handle(
    gate: TelegramInteractionGate,
    chat_id: int,
    tool_input: dict[str, Any],
) -> PermissionResultAllow | PermissionResultDeny:
    """Render the plan, post Approve/Reject, and await the verdict.

    Stores the pending future in ``gate._plan_pending[chat_id]`` so a button tap
    (`on_callback`) or a freeform text reply (`consume_text`) can resolve it.
    Approve → Allow; reject/timeout → Deny whose message carries the user's
    feedback so the model revises and calls ExitPlanMode again.
    """
    t = gate._t
    plan = str(tool_input.get("plan", "") or "").strip()
    log.info(
        "ExitPlanMode: chat_id=%s plan_len=%d preview=%r",
        chat_id,
        len(plan),
        plan[:200].replace("\n", " ⏎ "),
    )

    # 1. Render the plan body with Markdown so headings/lists survive.
    if plan:
        if gate._send_md is not None:
            try:
                await gate._send_md(chat_id, plan)
            except Exception:
                log.exception("ExitPlanMode: failed to send plan markdown")
        else:
            try:
                await gate._bot.send_message(chat_id, plan, parse_mode=None)
            except Exception:
                log.exception("ExitPlanMode: failed to send plan plain text")

    # 2. Post the approve / reject buttons as a separate compact message so
    # we can delete it cleanly without losing the plan above.
    request_id = secrets.token_hex(8)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t.t("plan_btn_approve"),
                    callback_data=f"plan:{request_id}:approve",
                ),
                InlineKeyboardButton(
                    text=t.t("plan_btn_reject"),
                    callback_data=f"plan:{request_id}:reject",
                ),
            ]
        ]
    )
    try:
        prompt = await gate._bot.send_message(
            chat_id, t.t("plan_header"), reply_markup=kb, parse_mode=None
        )
    except Exception:
        log.exception("ExitPlanMode: failed to send button prompt")
        return PermissionResultDeny(
            message=t.t("permission_failed_prompt"),
        )

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[tuple[str, str]] = loop.create_future()
    gate._plan_pending[chat_id] = (fut, request_id, prompt.message_id)

    try:
        decision, feedback = await asyncio.wait_for(fut, timeout=gate._timeout)
    except TimeoutError:
        with contextlib.suppress(Exception):
            await gate._bot.send_message(
                chat_id, t.t("plan_timeout"), parse_mode=None
            )
        return PermissionResultDeny(message=t.t("plan_rejected_default"))
    finally:
        gate._plan_pending.pop(chat_id, None)
        await gate._delete_prompt(chat_id, prompt.message_id)

    if decision == "approve":
        gate._cl(chat_id).info("ExitPlanMode: approved → agent continues")
        with contextlib.suppress(Exception):
            await gate._bot.send_message(
                chat_id, t.t("plan_started"), parse_mode=None
            )
        return PermissionResultAllow()
    gate._cl(chat_id).info(
        "ExitPlanMode: rejected (feedback_len=%d)", len(feedback)
    )
    with contextlib.suppress(Exception):
        if feedback:
            await gate._bot.send_message(
                chat_id,
                t.t("plan_rejected_with_feedback"),
                parse_mode=None,
            )
        else:
            await gate._bot.send_message(
                chat_id, t.t("plan_rejected_msg"), parse_mode=None
            )
    if feedback:
        deny_message = (
            "User rejected the plan and provided the following feedback. "
            "Stay in plan mode, address every point, and call "
            "ExitPlanMode again with the revised plan.\n\n"
            f"User feedback:\n{feedback}"
        )
    else:
        deny_message = t.t("plan_rejected_default")
    return PermissionResultDeny(message=deny_message)


async def on_callback(
    gate: TelegramInteractionGate, callback: CallbackQuery
) -> None:
    """Resolve a `plan:` Approve/Reject tap by setting the pending future.

    No-op for stale callbacks (deletes the orphaned prompt).
    """
    t = gate._t
    data = callback.data or ""
    if not data.startswith("plan:"):
        return
    try:
        _, request_id, action = data.split(":", 2)
    except ValueError:
        await callback.answer()
        return

    msg = callback.message if isinstance(callback.message, Message) else None
    entry = (
        gate._plan_pending.get(msg.chat.id) if msg is not None else None
    )
    if (
        entry is None
        or entry[1] != request_id
        or entry[0].done()
    ):
        await callback.answer(t.t("callback_outdated"), show_alert=False)
        if msg is not None:
            with contextlib.suppress(Exception):
                await msg.delete()
        return

    fut, _rid, _msg_id = entry
    chat_id = msg.chat.id if msg is not None else 0
    if action == "approve":
        gate._cl(chat_id).info("ExitPlanMode: approved via button")
        fut.set_result(("approve", ""))
        await callback.answer(t.t("plan_approved_toast"))
        return
    if action == "reject":
        gate._cl(chat_id).info("ExitPlanMode: rejected via button")
        fut.set_result(("reject", ""))
        await callback.answer(t.t("plan_rejected_toast"))
        return
    await callback.answer()


def consume_text(
    gate: TelegramInteractionGate, chat_id: int, text: str
) -> bool:
    """Resolve a pending plan prompt with a freeform text reply as rejection.

    Returns True if a pending, unresolved plan future existed and the text was
    consumed as rejection-with-feedback; False otherwise (caller treats the
    text as a normal message).
    """
    entry = gate._plan_pending.get(chat_id)
    if entry is None:
        return False
    fut, _rid, _msg_id = entry
    if fut.done():
        return False
    feedback = (text or "").strip()
    gate._cl(chat_id).info(
        "ExitPlanMode: rejected via text feedback: %r", feedback[:300]
    )
    fut.set_result(("reject", feedback))
    return True
