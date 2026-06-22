"""Generic text handler — the catch-all `F.text` registered after every
exact-command filter.

Three branches:
1. A pending `ExitPlanMode` prompt swallows the text as rejection-with-
   feedback (gate consumes it; we do NOT also fire an agent turn).
2. `/plan` was armed and waiting — this text becomes the plan prompt.
3. Regular user message — react + agent turn.
"""

import logging
from functools import partial

from aiogram import Dispatcher, F
from aiogram.types import Message

from ..infra.message_db import ROLE_USER
from ..ui.agent_reply import react_to, reply_with_agent
from .context import BotContext


async def handle_text(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    # If a plan-approval prompt is on screen, treat the text as
    # rejection-with-feedback for ExitPlanMode and do NOT also fire a
    # fresh agent turn — the existing turn is still running and will
    # consume our reply.
    if ctx.gate.consume_plan_text(message.chat.id, message.text or ""):
        cl.info(
            "plan rejected via text: %s",
            (message.text or "")[:200],
            extra={"role": ROLE_USER},
        )
        await react_to(ctx, message, message.text or "")
        return
    # `/plan` without args armed plan mode for this chat — the very next
    # text becomes the plan prompt.
    if ctx.plan_router.is_armed(message.chat.id):
        ctx.plan_router.disarm(message.chat.id)
        cl.info(
            "plan-armed text: %s",
            (message.text or "")[:200],
            extra={"role": ROLE_USER},
        )
        await ctx.plan_router.fire(
            message,
            message.text or "",
            cl,
            partial(react_to, ctx),
            partial(reply_with_agent, ctx),
        )
        return
    cl.info("user: %s", message.text, extra={"role": ROLE_USER})
    await ctx.gate.cancel_active_aq(message.chat.id)
    await react_to(ctx, message, message.text or "")
    await reply_with_agent(ctx, message, message.text or "", cl)


def register(dp: Dispatcher) -> None:
    dp.message.register(handle_text, F.text)
