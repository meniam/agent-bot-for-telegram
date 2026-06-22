"""AskUserQuestion SDK tool flow.

Each question is rendered as its own inline keyboard. Single-select fires
on the first tap. Multi-select toggles `▫️` / `☑️` until the user taps the
"Done" button. Every question also has a "Skip" button. Answers are
collected sequentially and returned to Claude as a plain-text summary via
`PermissionResultDeny.message` — the SDK feeds that string to the model as
the tool's response.
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
from claude_agent_sdk import PermissionResultDeny

if TYPE_CHECKING:
    from .gate import TelegramInteractionGate

log = logging.getLogger(__name__)


async def handle(
    gate: TelegramInteractionGate,
    chat_id: int,
    tool_input: dict[str, Any],
) -> PermissionResultDeny:
    """Ask each question in turn and return the answers as a text summary.

    Renders one keyboard per question via `_ask_one`, collecting answers
    sequentially. If the user sends a new message mid-flow (`cancel_active_aq`
    sets the abort flag), remaining questions are recorded as skipped. Always
    returns a Deny-shaped result whose message *is* the answer summary — the SDK
    feeds it back to the model as the tool result. Timeout → "no response".
    """
    t = gate._t
    questions = tool_input.get("questions") or []
    if not isinstance(questions, list) or not questions:
        return PermissionResultDeny(
            message="AskUserQuestion called with no questions."
        )

    collected: list[tuple[str, list[str] | None]] = []
    # Fresh start for this turn — drop any leftover abort flag from a
    # superseded prompt that already completed.
    gate._aq_aborted.discard(chat_id)
    try:
        for qidx, q in enumerate(questions):
            if chat_id in gate._aq_aborted:
                # User sent a new message mid-prompt; mark remaining
                # questions as skipped so Claude knows what's missing.
                for remaining in questions[qidx:]:
                    if isinstance(remaining, dict):
                        collected.append(
                            (str(remaining.get("question", "")), None)
                        )
                break
            if not isinstance(q, dict):
                continue
            try:
                answers = await _ask_one(gate, chat_id, qidx, len(questions), q)
            except TimeoutError:
                with contextlib.suppress(Exception):
                    await gate._bot.send_message(
                        chat_id, t.t("aq_timeout"), parse_mode=None
                    )
                return PermissionResultDeny(
                    message=(
                        "User did not answer the AskUserQuestion prompt in time. "
                        "Treat as no response and proceed without those answers."
                    )
                )
            except Exception as e:
                log.exception("AskUserQuestion render failed")
                return PermissionResultDeny(
                    message=f"AskUserQuestion failed to render: {e!r}",
                )
            collected.append((str(q.get("question", "")), answers))
    finally:
        gate._aq_aborted.discard(chat_id)

    summary = _format_answers(collected)
    gate._cl(chat_id).info(
        "AskUserQuestion final: %s",
        summary.replace("\n", " ⏎ ")[:600],
    )
    return PermissionResultDeny(message=summary)


async def _ask_one(
    gate: TelegramInteractionGate,
    chat_id: int,
    qidx: int,
    qtotal: int,
    question: dict[str, Any],
) -> list[str] | None:
    """Render one question's keyboard and await the pick.

    Registers an `_AQSession` in ``gate._aq`` keyed by a token embedded in
    callback_data; `on_callback` mutates its selection and resolves the future.
    Returns the chosen labels, or None for Skip/abort. Raises `TimeoutError`
    when the user does not answer within ``gate._timeout``.
    """
    from .gate import _AQSession

    t = gate._t
    text = question.get("question") or ""
    header = question.get("header") or ""
    options = question.get("options") or []
    if not isinstance(options, list) or not options:
        raise ValueError("question has no options")
    multi = bool(question.get("multiSelect"))

    request_id = secrets.token_hex(6)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[list[int] | None] = loop.create_future()

    prefix = f"❓ [{qidx + 1}/{qtotal}]"
    if header:
        prefix = f"{prefix} {header}"
    body_lines = [prefix, str(text).strip()]
    if multi:
        body_lines.append("")
        body_lines.append(t.t("aq_pick_multi"))
    body = "\n".join(line for line in body_lines if line is not None)

    # Build keyboard with current selection state. Recreated on each
    # callback so the checkmarks redraw without keeping state in the
    # closure.
    def build_kb() -> InlineKeyboardMarkup:
        """Build the options keyboard, redrawing checkmarks from current selection."""
        session = gate._aq.get(request_id)
        selected: set[int] = session.selected if session is not None else set()
        rows: list[list[InlineKeyboardButton]] = []
        for i, opt in enumerate(options):
            if not isinstance(opt, dict):
                continue
            label = str(opt.get("label", f"option {i + 1}"))
            if multi:
                mark = "☑️ " if i in selected else "▫️ "
                label = f"{mark}{label}"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label[:64],
                        callback_data=f"aq:{request_id}:{i}",
                    )
                ]
            )
        footer: list[InlineKeyboardButton] = []
        if multi:
            footer.append(
                InlineKeyboardButton(
                    text=t.t("aq_done"),
                    callback_data=f"aq:{request_id}:done",
                )
            )
        footer.append(
            InlineKeyboardButton(
                text=t.t("aq_skip"),
                callback_data=f"aq:{request_id}:skip",
            )
        )
        rows.append(footer)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    sent = await gate._bot.send_message(
        chat_id, body, reply_markup=build_kb(), parse_mode=None
    )

    gate._aq[request_id] = _AQSession(
        fut=fut,
        options=options,
        multi=multi,
        chat_id=chat_id,
        message_id=sent.message_id,
        build_kb=build_kb,
    )

    try:
        picked_idx = await asyncio.wait_for(fut, timeout=gate._timeout)
    finally:
        gate._aq.pop(request_id, None)
        await gate._delete_prompt(chat_id, sent.message_id)

    if picked_idx is None:
        return None
    return [
        str(options[i].get("label", "")) for i in picked_idx if 0 <= i < len(options)
    ]


async def on_callback(
    gate: TelegramInteractionGate, callback: CallbackQuery
) -> None:
    """Handle an `aq:` tap and advance the question's selection state.

    An option tap toggles (multi-select, redrawing the keyboard) or resolves
    (single-select); Done resolves with the current selection; Skip resolves
    with None. No-op for stale or cross-chat callbacks.
    """
    t = gate._t
    data = callback.data or ""
    if not data.startswith("aq:"):
        return
    try:
        _, request_id, action = data.split(":", 2)
    except ValueError:
        await callback.answer()
        return

    session = gate._aq.get(request_id)
    if session is None or session.fut.done():
        await callback.answer(t.t("callback_outdated"), show_alert=False)
        msg = callback.message if isinstance(callback.message, Message) else None
        if msg is not None:
            with contextlib.suppress(Exception):
                await msg.delete()
        return

    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    actual_chat_id = (
        callback.message.chat.id if isinstance(callback.message, Message) else None
    )
    if actual_chat_id != session.chat_id:
        await callback.answer(t.t("unauthorized_callback"), show_alert=True)
        return

    if action == "done":
        if not session.multi:
            await callback.answer()
            return
        picked = sorted(session.selected)
        picks = [
            str(session.options[i].get("label", "")) for i in picked
            if 0 <= i < len(session.options)
        ]
        gate._cl(session.chat_id).info(
            "AskUserQuestion (multi) picked: %s", picks
        )
        session.fut.set_result(picked)
        await callback.answer(t.t("callback_received"))
        return

    if action == "skip":
        gate._cl(session.chat_id).info("AskUserQuestion: skipped")
        session.fut.set_result(None)
        await callback.answer(t.t("aq_skipped"))
        return

    try:
        idx = int(action)
    except ValueError:
        await callback.answer()
        return
    if idx < 0 or idx >= len(session.options):
        await callback.answer()
        return

    if not session.multi:
        label = str(session.options[idx].get("label", ""))
        gate._cl(session.chat_id).info(
            "AskUserQuestion picked: %r", label
        )
        session.fut.set_result([idx])
        await callback.answer(t.t("callback_received"))
        return

    # Multi-select toggle: flip selection, redraw keyboard.
    if idx in session.selected:
        session.selected.remove(idx)
    else:
        session.selected.add(idx)
    try:
        await gate._bot.edit_message_reply_markup(
            chat_id=session.chat_id,
            message_id=session.message_id,
            reply_markup=session.build_kb(),
        )
    except Exception:
        log.debug("could not redraw AskUserQuestion keyboard", exc_info=True)
    await callback.answer()


def _format_answers(
    collected: list[tuple[str, list[str] | None]],
) -> str:
    """Format collected answers as the text summary returned to the model.

    ``None`` answers render as skipped, an empty list as no selection, and a
    non-empty list as the chosen labels.
    """
    lines = ["User responded to AskUserQuestion via Telegram inline buttons:"]
    for i, (q, answers) in enumerate(collected, 1):
        lines.append("")
        lines.append(f"{i}. {q.strip()}")
        if answers is None:
            lines.append("   → (skipped)")
        elif not answers:
            lines.append("   → (no selection)")
        else:
            joined = ", ".join(repr(a) for a in answers)
            lines.append(f"   → {joined}")
    return "\n".join(lines)
