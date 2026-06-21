"""Callback handler for structured questionnaire buttons."""

import logging

from aiogram import Dispatcher, F
from aiogram.types import CallbackQuery, Message

from ..ui.agent_reply import reply_with_agent
from ..ui.questionnaire import on_callback
from .context import BotContext


async def questionnaire_callback(
    callback: CallbackQuery,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    agent_prompt = await on_callback(callback, ctx.tr)
    if agent_prompt is None:
        return
    msg = callback.message
    if not isinstance(msg, Message):
        return
    cl.info("questionnaire submitted")
    await reply_with_agent(ctx, msg, agent_prompt, cl)


def register(dp: Dispatcher) -> None:
    dp.callback_query.register(questionnaire_callback, F.data.startswith("qq:"))
