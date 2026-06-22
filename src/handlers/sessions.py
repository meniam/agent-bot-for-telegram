"""`/sess` — paged, tap-to-switch session list, or `/sess <n>` to switch by
ordinal (created order) for keyboard-less clients.

Sessions are the bot-level meta layer over the SDK's persisted conversation
history (see `infra/session_store.py`). `/new` (in `basic.py`) starts a fresh
one; this module lists/switches/deletes them.

UI:
- Sessions are shown newest-interaction-first (by ``last_used``), paged 15 at a
  time; the page indicator + ◀/▶ sit at the bottom.
- The action button (delete-session / back) sits at the **top**.
- Each session is a full-width button so long titles stay readable.
- Callbacks carry the session **id**, so they survive reordering/paging.

Callback grammar:
- ``sess:<id>``            switch to a session
- ``sessnav:<mode>:<pg>``  paginate / toggle mode (``l`` list, ``d`` delete)
- ``sessdel:<id>:<pg>``    ask delete confirmation (pg = page to return to)
- ``sessok:<id>``          confirm delete
- ``sessnoop``             inert page indicator
"""

import contextlib
import logging

from aiogram import Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..ui.markdown import send_md
from .context import BotContext

_BTN_TITLE_MAX = 48
_PAGE_SIZE = 15


def _short(title: str) -> str:
    return title if len(title) <= _BTN_TITLE_MAX else title[: _BTN_TITLE_MAX - 1] + "…"


def _build_view(
    ctx: BotContext, chat_id: int, *, delete_mode: bool, page: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Return (header_text, keyboard) for the list at a given mode/page."""
    sessions = ctx.sessions.list_by_recency(chat_id)
    if not sessions:
        return ctx.tr.t("sess_list_empty"), None

    pages = max(1, (len(sessions) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    window = sessions[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]
    current_id = ctx.sessions.current_id(chat_id)
    marker = ctx.tr.t("sess_current_marker")

    rows: list[list[InlineKeyboardButton]] = []
    # Action row on top.
    if delete_mode:
        rows.append(
            [InlineKeyboardButton(text=ctx.tr.t("sess_back_btn"), callback_data="sessnav:l:0")]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text=ctx.tr.t("sess_delete_mode_btn"), callback_data="sessnav:d:0"
                )
            ]
        )
    # One full-width button per session.
    for s in window:
        if delete_mode:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 {_short(s.title)}", callback_data=f"sessdel:{s.id}:{page}"
                    )
                ]
            )
        else:
            prefix = f"{marker} " if s.id == current_id else ""
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{prefix}{_short(s.title)}", callback_data=f"sess:{s.id}"
                    )
                ]
            )
    # Pager row at the bottom.
    if pages > 1:
        mode = "d" if delete_mode else "l"
        nav: list[InlineKeyboardButton] = []
        nav.append(
            InlineKeyboardButton(
                text="◀", callback_data=f"sessnav:{mode}:{page - 1}" if page > 0 else "sessnoop"
            )
        )
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="sessnoop"))
        nav.append(
            InlineKeyboardButton(
                text="▶",
                callback_data=f"sessnav:{mode}:{page + 1}" if page < pages - 1 else "sessnoop",
            )
        )
        rows.append(nav)

    header = ctx.tr.t("sess_delete_header" if delete_mode else "sess_list_header")
    return header, InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_keyboard(ctx: BotContext, sid: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=ctx.tr.t("sess_delete_yes"), callback_data=f"sessok:{sid}"
                ),
                InlineKeyboardButton(
                    text=ctx.tr.t("sess_delete_no"), callback_data=f"sessnav:d:{page}"
                ),
            ]
        ]
    )


async def _rerender(
    ctx: BotContext, callback: CallbackQuery, chat_id: int, *, delete_mode: bool, page: int
) -> None:
    if not isinstance(callback.message, Message):
        return
    text, kb = _build_view(ctx, chat_id, delete_mode=delete_mode, page=page)
    with contextlib.suppress(Exception):
        if kb is None:
            await callback.message.edit_text(text)
        else:
            await callback.message.edit_text(text, reply_markup=kb)


async def sessions_cmd(
    message: Message,
    ctx: BotContext,
    command: CommandObject,
    cl: logging.Logger,
    **_: object,
) -> None:
    chat_id = message.chat.id
    arg = (command.args or "").strip()
    if arg:
        # `/sess <n>` — ordinal in created order, for keyboard-less clients.
        await _switch_by_ordinal(ctx, message, chat_id, arg, cl)
        return
    text, kb = _build_view(ctx, chat_id, delete_mode=False, page=0)
    if kb is None:
        await send_md(message, text)
        return
    await message.answer(text, reply_markup=kb)


async def _switch_by_ordinal(
    ctx: BotContext, message: Message, chat_id: int, arg: str, cl: logging.Logger
) -> None:
    try:
        ordinal = int(arg)
    except ValueError:
        await send_md(message, ctx.tr.t("sess_usage"))
        return
    target = ctx.sessions.get_by_ordinal(chat_id, ordinal)
    session = await ctx.agent.switch_session(chat_id, target.id) if target else None
    if session is None:
        cl.info("/sess switch failed: ordinal=%s", ordinal)
        await send_md(message, ctx.tr.t("sess_not_found", ordinal=ordinal))
        return
    ctx.plan_router.disarm(chat_id)
    await ctx.gate.cancel_active_aq(chat_id)
    cl.info("/sess switched to %s (%s)", ordinal, session.id)
    await send_md(message, ctx.tr.t("sess_switched", title=session.title))


async def sessions_switch_callback(
    callback: CallbackQuery, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    chat_id = callback.message.chat.id if callback.message else None
    sid = (callback.data or "").split(":", 1)[1] if ":" in (callback.data or "") else ""
    if chat_id is None:
        await callback.answer()
        return
    session = await ctx.agent.switch_session(chat_id, sid) if sid else None
    if session is None:
        await callback.answer(ctx.tr.t("sess_not_found", ordinal="?"), show_alert=True)
        return
    ctx.plan_router.disarm(chat_id)
    await ctx.gate.cancel_active_aq(chat_id)
    cl.info("/sess switched via button to %s", session.id)
    await _rerender(ctx, callback, chat_id, delete_mode=False, page=0)
    await callback.answer(ctx.tr.t("sess_switched", title=session.title))


async def sessions_nav_callback(
    callback: CallbackQuery, ctx: BotContext, **_: object
) -> None:
    """Pagination + mode toggle + delete-confirm cancel all land here."""
    chat_id = callback.message.chat.id if callback.message else None
    parts = (callback.data or "").split(":")
    if chat_id is None or len(parts) != 3:
        await callback.answer()
        return
    delete_mode = parts[1] == "d"
    page = int(parts[2]) if parts[2].isdigit() else 0
    await _rerender(ctx, callback, chat_id, delete_mode=delete_mode, page=page)
    await callback.answer()


async def sessions_ask_delete_callback(
    callback: CallbackQuery, ctx: BotContext, **_: object
) -> None:
    """Delete-row tap → inline confirmation."""
    chat_id = callback.message.chat.id if callback.message else None
    parts = (callback.data or "").split(":")
    if chat_id is None or len(parts) != 3:
        await callback.answer()
        return
    sid, page = parts[1], (int(parts[2]) if parts[2].isdigit() else 0)
    target = ctx.sessions.get_by_id(chat_id, sid)
    if target is None:
        await callback.answer(ctx.tr.t("sess_not_found", ordinal="?"), show_alert=True)
        return
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                ctx.tr.t("sess_delete_confirm", title=_short(target.title)),
                reply_markup=_confirm_keyboard(ctx, sid, page),
            )
    await callback.answer()


async def sessions_confirm_delete_callback(
    callback: CallbackQuery, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    chat_id = callback.message.chat.id if callback.message else None
    sid = (callback.data or "").split(":", 1)[1] if ":" in (callback.data or "") else ""
    if chat_id is None:
        await callback.answer()
        return
    deleted = await ctx.agent.delete_session(chat_id, sid) if sid else None
    if deleted is None:
        await callback.answer(ctx.tr.t("sess_not_found", ordinal="?"), show_alert=True)
        await _rerender(ctx, callback, chat_id, delete_mode=True, page=0)
        return
    cl.info("/sess deleted %s", deleted.id)
    # Back to the switch list — deletion done; marker reflects the new current.
    await _rerender(ctx, callback, chat_id, delete_mode=False, page=0)
    await callback.answer(ctx.tr.t("sess_deleted", title=_short(deleted.title)))


async def sessions_noop_callback(callback: CallbackQuery, **_: object) -> None:
    await callback.answer()


def register(dp: Dispatcher) -> None:
    dp.message.register(sessions_cmd, Command("sess"))
    dp.callback_query.register(sessions_switch_callback, F.data.startswith("sess:"))
    dp.callback_query.register(sessions_nav_callback, F.data.startswith("sessnav:"))
    dp.callback_query.register(sessions_ask_delete_callback, F.data.startswith("sessdel:"))
    dp.callback_query.register(sessions_confirm_delete_callback, F.data.startswith("sessok:"))
    dp.callback_query.register(sessions_noop_callback, F.data == "sessnoop")
