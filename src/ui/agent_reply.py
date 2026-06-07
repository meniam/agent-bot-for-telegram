"""Two helpers used by every handler that drives an agent turn:

- `react_to` — set an emoji reaction on the user's message.
- `reply_with_agent` — drain pending uploads, stream the agent's response
  through the `DraftStreamer`, send the final reply.
"""

import asyncio
import logging

from aiogram.types import Message, ReactionTypeEmoji

from ..handlers.context import BotContext
from ..services.upload_store import format_attachment_prompt
from .markdown import send_md


async def react_to(ctx: BotContext, message: Message, text: str) -> None:
    emoji = ctx.reaction_picker.pick(text or "")
    try:
        await ctx.bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        ctx.glog.exception("[%s] reaction failed", ctx.cfg.name)


async def reply_with_agent(
    ctx: BotContext, message: Message, prompt: str, cl: logging.Logger
) -> None:
    if ctx.uploads is not None:
        pending = ctx.uploads.pop_pending(message.chat.id)
        if pending:
            cl.info(
                "draining %d pending upload(s): %s",
                len(pending),
                ", ".join(str(p.path) for p in pending),
            )
            prompt = format_attachment_prompt(pending, prompt)
    await ctx.bot.send_chat_action(message.chat.id, "typing")
    ctx.tool_mirror.begin_turn(message.chat.id)
    try:
        chunks = ctx.agent.ask_stream(message.chat.id, prompt)
        answer = await asyncio.wait_for(
            ctx.streamer.stream(message.chat.id, chunks),
            timeout=ctx.cfg.agent_timeout_sec,
        )
    except TimeoutError:
        ctx.glog.warning(
            "[%s] agent timeout (chat_id=%s)", ctx.cfg.name, message.chat.id
        )
        cl.warning("agent timeout after %ss", ctx.cfg.agent_timeout_sec)
        await send_md(
            message,
            ctx.tr.t("agent_timeout", seconds=ctx.cfg.agent_timeout_sec),
        )
        return
    except Exception as e:
        ctx.glog.exception("[%s] agent error", ctx.cfg.name)
        cl.exception("agent error: %s", e)
        await send_md(
            message, ctx.tr.t("error_internal", error=type(e).__name__)
        )
        return
    final = answer.strip() or ctx.tr.t("empty_answer")
    cl.info("bot: %s", final)
    await send_md(message, final)
