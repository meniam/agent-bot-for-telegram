"""Two helpers used by every handler that drives an agent turn.

- `react_to` — set an emoji reaction on the user's message.
- `reply_with_agent` — drain pending uploads, stream the agent's response
  through the `DraftStreamer`, send the final reply.
"""

import asyncio
import logging
from pathlib import Path

from aiogram.types import Message, ReactionTypeEmoji, User

from ..handlers.context import BotContext
from ..infra.agent_types import AgentEventStreamTimeout, AgentTurnReset
from ..infra.message_db import ROLE_BOT
from ..services.upload_store import format_attachment_prompt
from .file_delivery import parse_file_delivery, send_file_delivery
from .markdown import send_md
from .questionnaire import parse_questionnaire, render_questionnaire

# Strong refs to background title-generation tasks so they are not GC'd
# mid-flight (asyncio only keeps weak references to running tasks).
_bg_tasks: set[asyncio.Task[None]] = set()


def _user_context_prefix(user: User | None, chat_id: int) -> str:
    """Build a bracketed identity line prepended to a new session's prompt."""
    if user is None:
        return f"[Telegram user: chat_id={chat_id}]\n\n"
    parts = [f"chat_id={chat_id}"]
    if user.username:
        parts.append(f"username=@{user.username}")
    name = " ".join(filter(None, [user.first_name, user.last_name]))
    if name:
        parts.append(f"name={name}")
    if user.language_code:
        parts.append(f"lang={user.language_code}")
    return f"[Telegram user: {', '.join(parts)}]\n\n"


async def react_to(ctx: BotContext, message: Message, text: str) -> None:
    """Set an emoji reaction on the user's message, swallowing failures."""
    emoji = ctx.reaction_picker.pick(text or "")
    try:
        await ctx.bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        ctx.glog.exception("[%s] reaction failed", ctx.cfg.name)


async def _name_session_in_background(
    ctx: BotContext, chat_id: int, user_text: str, cl: logging.Logger
) -> None:
    """If the current session is still unnamed, ask the LLM for a title."""
    session = await ctx.agent.current_session(chat_id)
    if session is None or session.auto_titled or not user_text.strip():
        return

    async def _run() -> None:
        """Generate and store the session title in the background."""
        try:
            title = await ctx.agent.generate_title(user_text)
        except Exception as e:
            cl.warning("session title generation failed: %s", e)
            return
        if title:
            await ctx.sessions.set_title(chat_id, session.id, title)
            cl.info("session %s titled: %s", session.id, title)

    task = asyncio.create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def reply_with_agent(
    ctx: BotContext, message: Message, prompt: str, cl: logging.Logger
) -> None:
    """Run one agent turn for a chat message and deliver the reply.

    The shared pipeline behind text, voice, custom commands, and `/plan`. It:
    injects a user-context prefix on a brand-new session; drains pending
    uploads into the prompt; streams the turn through `DraftStreamer` under an
    `agent_timeout_sec` deadline; then renders the final answer. Each failure
    mode (timeout, `AgentTurnReset`, `AgentEventStreamTimeout`, any other
    exception) is logged and turned into a user-facing message or a silent
    return. A final answer may instead be a `bot_files` delivery or a
    `bot_questionnaire` prompt, which are dispatched here. Session titling runs
    as a fire-and-forget background task. Returns nothing — all output is I/O.
    """
    user_text = prompt
    if not ctx.agent.has_session(message.chat.id):
        prefix = _user_context_prefix(message.from_user, message.chat.id)
        prompt = prefix + prompt
        cl.info("injected user context for new session")
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
    # Always clear the live tool-status line once the turn ends — every exit
    # path (answer, delivery, questionnaire, timeout, reset, error) runs the
    # finally so no stale status survives into the chat.
    try:
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
        except AgentTurnReset as e:
            cl.info("agent turn reset: %s", e)
            return
        except AgentEventStreamTimeout as e:
            ctx.glog.warning("[%s] agent event stream timeout: %s", ctx.cfg.name, e)
            cl.warning("agent event stream timeout: %s", e)
            await send_md(message, ctx.tr.t("agent_stalled"))
            return
        except Exception as e:
            ctx.glog.exception("[%s] agent error", ctx.cfg.name)
            cl.exception("agent error: %s", e)
            await send_md(
                message, ctx.tr.t("error_internal", error=type(e).__name__)
            )
            return
        await _name_session_in_background(ctx, message.chat.id, user_text, cl)
        final = answer.strip() or ctx.tr.t("empty_answer")
        delivery = parse_file_delivery(final)
        if delivery is not None:
            roots = [
                path
                for path in (ctx.cfg.working_dir, ctx.cfg.uploads_dir)
                if path is not None
            ]
            cl.info(
                "bot file delivery: %d file(s)",
                len(delivery.files),
                extra={"role": ROLE_BOT},
            )
            await send_file_delivery(
                message,
                delivery,
                roots=[Path(path) for path in roots],
                t=ctx.tr,
                cl=cl,
            )
            return
        questionnaire = parse_questionnaire(final)
        if questionnaire is not None:
            cl.info(
                "bot questionnaire: %d question(s)",
                len(questionnaire.questions),
                extra={"role": ROLE_BOT},
            )
            await render_questionnaire(message, questionnaire, ctx.tr)
            return
        cl.info("bot: %s", final, extra={"role": ROLE_BOT})
        await send_md(message, final)
    finally:
        await ctx.tool_mirror.end_turn(message.chat.id)
