"""Handlers for structured questionnaire buttons and native poll answers."""

import contextlib
import logging

from aiogram import Dispatcher, F
from aiogram.types import CallbackQuery, Message, PollAnswer

from ..ui.agent_reply import reply_with_agent
from ..ui.questionnaire import on_callback, on_poll_answer
from .context import BotContext


async def questionnaire_callback(
    callback: CallbackQuery,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    """Handle a `qq:` questionnaire tap; fire an agent turn once submitted."""
    agent_prompt = await on_callback(callback, ctx.tr)
    if agent_prompt is None:
        return
    msg = callback.message
    if not isinstance(msg, Message):
        return
    cl.info("questionnaire submitted")
    await reply_with_agent(ctx, msg, agent_prompt, cl)


async def questionnaire_poll_answer(
    poll_answer: PollAnswer,
    ctx: BotContext,
    **_: object,
) -> None:
    """Handle a native poll answer; fire an agent turn once all polls are answered."""
    result = await on_poll_answer(poll_answer, ctx.tr)
    if result is None:
        return
    chat_id, summary, agent_prompt, poll_message_ids = result
    cl = ctx.bot_logs.for_chat(chat_id)
    for message_id in poll_message_ids:
        with contextlib.suppress(Exception):
            await ctx.bot.stop_poll(chat_id=chat_id, message_id=message_id)
    sent = await ctx.bot.send_message(chat_id, summary, parse_mode=None)
    cl.info("questionnaire poll submitted")
    await reply_with_agent(ctx, sent, agent_prompt, cl)


def register(dp: Dispatcher) -> None:
    """Register questionnaire callback and native poll handlers on ``dp``."""
    dp.callback_query.register(questionnaire_callback, F.data.startswith("qq:"))
    dp.poll_answer.register(questionnaire_poll_answer)
