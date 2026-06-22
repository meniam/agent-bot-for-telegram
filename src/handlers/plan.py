"""`/plan` slash command + gate callback wiring.

`/plan` with no arg arms the chat (next text or voice becomes the prompt).
`/plan <prompt>` fires immediately. Three callback handlers delegate the
`perm:`, `aq:`, and `plan:` button taps to the interaction gate.
"""

import logging
from functools import partial

from aiogram import Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from ..ui.agent_reply import react_to, reply_with_agent
from ..ui.markdown import send_md
from .context import BotContext


async def start_plan_mode(
    message: Message,
    command: CommandObject,
    ctx: BotContext,
    cl: logging.Logger,
    **_: object,
) -> None:
    """Arm plan mode for `/plan` with no arg, or fire `/plan <prompt>` now."""
    args = (command.args or "").strip()
    if not args:
        ctx.plan_router.arm(message.chat.id, cl)
        await send_md(message, ctx.tr.t("plan_mode_armed"))
        return
    ctx.plan_router.disarm(message.chat.id)
    await ctx.plan_router.fire(
        message,
        args,
        cl,
        partial(react_to, ctx),
        partial(reply_with_agent, ctx),
    )


async def permission_callback(
    callback: CallbackQuery, ctx: BotContext, **_: object
) -> None:
    """Forward a `perm:` button tap to the interaction gate."""
    await ctx.gate.handle_callback(callback)


async def ask_user_question_callback(
    callback: CallbackQuery, ctx: BotContext, **_: object
) -> None:
    """Forward an `aq:` button tap to the interaction gate."""
    await ctx.gate.handle_aq_callback(callback)


async def plan_callback(
    callback: CallbackQuery, ctx: BotContext, **_: object
) -> None:
    """Forward a `plan:` button tap to the interaction gate."""
    await ctx.gate.handle_plan_callback(callback)


def register(dp: Dispatcher) -> None:
    """Register the `/plan` command and its gate callback handlers on ``dp``."""
    dp.message.register(start_plan_mode, Command("plan"))
    dp.callback_query.register(permission_callback, F.data.startswith("perm:"))
    dp.callback_query.register(
        ask_user_question_callback, F.data.startswith("aq:")
    )
    dp.callback_query.register(plan_callback, F.data.startswith("plan:"))
