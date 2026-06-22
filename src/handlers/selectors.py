"""`/mode` and `/model` slash commands + their inline keyboards.

A generic `_dispatch_choice_cb` resolves both `mode:` and `model:` callbacks:
it parses the embedded chat_id, performs the ownership check (defense against
forged callback_data), edits the keyboard away, and calls the per-feature
apply function.
"""

import contextlib
import logging
from collections.abc import Awaitable, Callable

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..ui.markdown import send_md, to_html
from .context import BotContext


def _mode_label(ctx: BotContext, mode: str) -> str:
    """Return the localized button label for ``mode`` (the id if untranslated)."""
    key = f"mode_btn_{mode}"
    label = ctx.tr.t(key)
    return mode if label == key else label


def _mode_keyboard(ctx: BotContext, chat_id: int) -> InlineKeyboardMarkup:
    """Build the inline keyboard of available modes, marking the current one."""
    current = ctx.agent.current_mode(chat_id)
    rows: list[list[InlineKeyboardButton]] = []
    for m in ctx.agent.available_modes():
        label = _mode_label(ctx, m)
        if m == current:
            label = f"● {label}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"mode:{chat_id}:{m}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _model_keyboard(ctx: BotContext, chat_id: int) -> InlineKeyboardMarkup:
    """Build the inline keyboard of available models, marking the current one."""
    current = ctx.agent.current_model(chat_id)  # None == SDK default
    rows: list[list[InlineKeyboardButton]] = []
    for mid, label in ctx.agent.available_models():
        text = label or ctx.tr.t("model_default_label")
        is_current = (mid == current) or (mid == "" and current is None)
        if is_current:
            text = f"● {text}"
        rows.append(
            [InlineKeyboardButton(text=text, callback_data=f"model:{chat_id}:{mid}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _apply_mode(
    ctx: BotContext, message: Message, mode: str, cl: logging.Logger
) -> None:
    """Push ``mode`` to the backend and confirm, or report the failure."""
    try:
        await ctx.agent.set_permission_mode(message.chat.id, mode)
    except Exception as e:
        cl.exception("set_permission_mode failed: %s", e)
        reason = str(e) or type(e).__name__
        await send_md(message, ctx.tr.t("mode_failed", error=reason[:400]))
        return
    cl.info("mode=%s", mode)
    await send_md(message, ctx.tr.t("mode_set", mode=mode))


async def _apply_model(
    ctx: BotContext, message: Message, model_id: str, cl: logging.Logger
) -> None:
    """Push ``model_id`` (empty = default) to the backend and confirm."""
    sdk_arg: str | None = model_id or None
    try:
        await ctx.agent.set_model(message.chat.id, sdk_arg)
    except Exception as e:
        cl.exception("set_model failed: %s", e)
        reason = str(e) or type(e).__name__
        await send_md(message, ctx.tr.t("model_failed", error=reason[:400]))
        return
    display = model_id or ctx.tr.t("model_default_label")
    cl.info("model=%s", display)
    await send_md(
        message,
        ctx.tr.t("model_set", provider=ctx.agent.provider, model=display),
    )


async def _dispatch_choice_cb(
    cq: CallbackQuery,
    ctx: BotContext,
    cl: logging.Logger,
    valid: frozenset[str] | tuple[str, ...] | set[str] | None,
    apply: Callable[[BotContext, Message, str, logging.Logger], Awaitable[None]],
) -> None:
    """Shared handler for `/mode` and `/model` choice-button taps.

    Parses `<prefix>:<chat_id>:<value>` callback_data, enforces that the
    embedded ``chat_id`` matches the firing chat (anti-forgery — ACL middleware
    does not cover gate-style callbacks), validates ``value`` against ``valid``,
    clears the keyboard, then runs ``apply`` to push the choice to the backend.
    """
    data = cq.data or ""
    try:
        _, chat_id_s, value = data.split(":", 2)
    except ValueError:
        await cq.answer(ctx.tr.t("callback_outdated"), show_alert=False)
        return
    # Ownership check — embedded chat_id must match the chat the callback fires
    # in. Defense against forged callback_data; the ACL middleware does not
    # cover this invariant.
    if cq.message is None or int(chat_id_s) != cq.message.chat.id:
        await cq.answer(ctx.tr.t("unauthorized_callback"), show_alert=True)
        return
    if valid is not None and value and value not in valid:
        await cq.answer(ctx.tr.t("callback_outdated"), show_alert=False)
        return
    await cq.answer(ctx.tr.t("callback_received"))
    msg = cq.message
    if not isinstance(msg, Message):
        return
    with contextlib.suppress(TelegramBadRequest):
        await msg.edit_reply_markup(reply_markup=None)
    await apply(ctx, msg, value, cl)


async def set_mode_cmd(
    message: Message,
    command: CommandObject,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    """Apply `/mode <arg>` directly, or show the mode-picker keyboard."""
    arg = (command.args or "").strip()
    mode_values = ctx.agent.available_modes()
    if arg:
        if arg not in mode_values:
            await send_md(
                message,
                ctx.tr.t(
                    "mode_invalid", mode=arg, valid=", ".join(mode_values)
                ),
            )
            return
        await _apply_mode(ctx, message, arg, cl)
        return
    current = ctx.agent.current_mode(message.chat.id)
    await message.answer(
        to_html(ctx.tr.t("mode_pick", current=current)),
        parse_mode=ParseMode.HTML,
        reply_markup=_mode_keyboard(ctx, message.chat.id),
    )


async def set_model_cmd(
    message: Message,
    command: CommandObject,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    """Apply `/model <arg>` directly, or show the model-picker keyboard."""
    arg = (command.args or "").strip()
    model_ids = frozenset(mid for mid, _ in ctx.agent.available_models() if mid)
    if arg:
        if arg.lower() == "default":
            await _apply_model(ctx, message, "", cl)
            return
        if ctx.agent.provider not in {"codex", "pi"} and arg not in model_ids:
            await send_md(message, ctx.tr.t("model_invalid", model=arg))
            return
        await _apply_model(ctx, message, arg, cl)
        return
    if ctx.agent.provider == "pi":
        with contextlib.suppress(Exception):
            await ctx.agent.get_server_info(message.chat.id)
    current = ctx.agent.current_model(message.chat.id) or ctx.tr.t(
        "model_default_label"
    )
    await message.answer(
        to_html(
            ctx.tr.t(
                "model_pick", provider=ctx.agent.provider, current=current
            )
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_model_keyboard(ctx, message.chat.id),
    )


async def mode_callback(
    callback: CallbackQuery,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    """Handle a `mode:` button tap via the shared choice dispatcher."""
    await _dispatch_choice_cb(
        callback, ctx, cl, frozenset(ctx.agent.available_modes()), _apply_mode
    )


async def model_callback(
    callback: CallbackQuery,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    """Handle a `model:` button tap via the shared choice dispatcher."""
    # Empty value = default; non-empty must be in _MODEL_IDS.
    model_ids = frozenset(mid for mid, _ in ctx.agent.available_models() if mid)
    await _dispatch_choice_cb(callback, ctx, cl, model_ids, _apply_model)


def register(dp: Dispatcher) -> None:
    """Register the `/mode` and `/model` commands and callbacks on ``dp``."""
    dp.message.register(set_mode_cmd, Command("mode"))
    dp.message.register(set_model_cmd, Command("model"))
    dp.callback_query.register(mode_callback, F.data.startswith("mode:"))
    dp.callback_query.register(model_callback, F.data.startswith("model:"))
