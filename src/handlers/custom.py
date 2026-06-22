"""Register user-defined slash commands from `cfg.commands_dir`.

MUST be registered BEFORE the generic `F.text` handler so `/<name>` is
routed to the custom prompt instead of being treated as a literal user
message.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..infra.commands import CommandDef
from ..ui.agent_reply import react_to, reply_with_agent
from .context import BotContext

_Handler = Callable[..., Awaitable[Any]]


def register(dp: Dispatcher, commands: list[CommandDef]) -> None:
    """Register a handler for each user-defined slash command on ``dp``."""
    for cmd in commands:
        dp.message.register(_make_handler(cmd.body, cmd.name), Command(cmd.name))


def _make_handler(template: str, cmd_name: str) -> _Handler:
    """Build a handler that substitutes args into ``template`` and fires it."""

    async def handler(
        message: Message,
        command: CommandObject,
        ctx: BotContext,
        cl: logging.Logger,
        **_: object,
    ) -> None:
        """Substitute `$ARGUMENTS` into the template and run an agent turn."""
        args = (command.args or "").strip()
        prompt = template.replace("$ARGUMENTS", args)
        cl.info(
            "/%s args=%r -> %s",
            cmd_name,
            args,
            prompt[:200].replace("\n", " ⏎ "),
        )
        await ctx.gate.cancel_active_aq(message.chat.id)
        await react_to(ctx, message, prompt)
        await reply_with_agent(ctx, message, prompt, cl)

    return handler
