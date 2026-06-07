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
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..ui.markdown import send_md, to_html
from .context import BotContext

_MODE_VALUES: tuple[str, ...] = (
    "default", "acceptEdits", "plan",
)

# (model_id, display_label). Empty id == back to SDK default.
_MODELS: tuple[tuple[str, str], ...] = (
    ("claude-opus-4-7",   "Opus 4.7"),
    ("claude-sonnet-4-6", "Sonnet 4.6"),
    ("claude-haiku-4-5",  "Haiku 4.5"),
    ("",                  ""),  # default — label from `model_default_label`
)
_MODEL_IDS: frozenset[str] = frozenset(mid for mid, _ in _MODELS if mid)


def _mode_keyboard(ctx: BotContext, chat_id: int) -> InlineKeyboardMarkup:
    current = ctx.agent.current_mode(chat_id)
    rows: list[list[InlineKeyboardButton]] = []
    for m in _MODE_VALUES:
        label = ctx.tr.t(f"mode_btn_{m}")
        if m == current:
            label = f"● {label}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"mode:{chat_id}:{m}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _model_keyboard(ctx: BotContext, chat_id: int) -> InlineKeyboardMarkup:
    current = ctx.agent.current_model(chat_id)  # None == SDK default
    rows: list[list[InlineKeyboardButton]] = []
    for mid, label in _MODELS:
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
    await send_md(message, ctx.tr.t("model_set", model=display))


async def _dispatch_choice_cb(
    cq: CallbackQuery,
    ctx: BotContext,
    cl: logging.Logger,
    valid: frozenset[str] | tuple[str, ...] | set[str] | None,
    apply: Callable[[BotContext, Message, str, logging.Logger], Awaitable[None]],
) -> None:
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
    arg = (command.args or "").strip()
    if arg:
        if arg not in _MODE_VALUES:
            await send_md(
                message,
                ctx.tr.t(
                    "mode_invalid", mode=arg, valid=", ".join(_MODE_VALUES)
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
    arg = (command.args or "").strip()
    if arg:
        if arg.lower() == "default":
            await _apply_model(ctx, message, "", cl)
            return
        if arg not in _MODEL_IDS:
            await send_md(message, ctx.tr.t("model_invalid", model=arg))
            return
        await _apply_model(ctx, message, arg, cl)
        return
    current = ctx.agent.current_model(message.chat.id) or ctx.tr.t(
        "model_default_label"
    )
    await message.answer(
        to_html(ctx.tr.t("model_pick", current=current)),
        parse_mode=ParseMode.HTML,
        reply_markup=_model_keyboard(ctx, message.chat.id),
    )


async def mode_callback(
    callback: CallbackQuery,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    await _dispatch_choice_cb(
        callback, ctx, cl, frozenset(_MODE_VALUES), _apply_mode
    )


async def model_callback(
    callback: CallbackQuery,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    # Empty value = default; non-empty must be in _MODEL_IDS.
    await _dispatch_choice_cb(callback, ctx, cl, _MODEL_IDS, _apply_model)


def register(dp: Dispatcher) -> None:
    dp.message.register(set_mode_cmd, Command("mode"))
    dp.message.register(set_model_cmd, Command("model"))
    dp.callback_query.register(mode_callback, F.data.startswith("mode:"))
    dp.callback_query.register(model_callback, F.data.startswith("model:"))
