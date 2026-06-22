"""Static-command handlers that don't drive an agent turn directly.

Covers `/start`, `/new`, `/cancel`, `/context`, `/stop`, `/mcp`, `/info`,
`/whoami`, and `/help`.
"""

import logging

from aiogram import Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, Message

from ..ui.markdown import send_md
from ..ui.sdk_views import (
    format_context_usage,
    format_mcp_status,
    format_server_info,
)
from .context import BotContext

# Commands worth including a usage example next to in `/help`.
_HELP_EXAMPLES = frozenset(
    ("plan", "mode", "model", "stop", "context", "mcp", "info", "task", "tasks")
)

# `/help` layout: ordered groups of built-in commands. Anything not listed
# here (i.e. user-defined custom commands) is rendered under a final group.
# `task` only appears when the tasks feature is enabled (it is absent from the
# command list otherwise), so listing it here is harmless when disabled.
_HELP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("help_group_sessions", ("new", "sess", "tasks", "task")),
    ("help_group_agent", ("plan", "mode", "model", "stop", "context", "cancel")),
    ("help_group_info", ("start", "mcp", "info", "whoami", "help")),
)


async def start(message: Message, ctx: BotContext, **_: object) -> None:
    """Reply to `/start` with the greeting message."""
    await send_md(message, ctx.tr.t("start_greeting"))


async def new_session(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    """Start a fresh agent session and clear any armed plan / pending question."""
    session = await ctx.agent.new_session(message.chat.id)
    ctx.plan_router.disarm(message.chat.id)
    await ctx.gate.cancel_active_aq(message.chat.id)
    cl.info("new session created by /new: %s", session.id)
    await send_md(message, ctx.tr.t("new_session_confirmation"))


async def cancel_pending(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    """Clear an armed `/plan` prompt, or report there is nothing to cancel."""
    if ctx.plan_router.is_armed(message.chat.id):
        ctx.plan_router.disarm(message.chat.id)
        cl.info("/cancel: pending plan input cleared")
        await send_md(message, ctx.tr.t("plan_canceled"))
        return
    await send_md(message, ctx.tr.t("nothing_to_cancel"))


async def show_context(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    """Report the session's context-window usage for `/context`."""
    await ctx.gate.cancel_active_aq(message.chat.id)
    await ctx.bot.send_chat_action(message.chat.id, "typing")
    try:
        usage = await ctx.agent.get_context_usage(message.chat.id)
    except Exception as e:
        ctx.glog.exception("[%s] context usage failed", ctx.cfg.name)
        cl.exception("context usage failed: %s", e)
        await send_md(
            message, ctx.tr.t("context_error", error=type(e).__name__)
        )
        return
    cl.info(
        "context: %.1f%% (%s/%s tokens, model=%s)",
        float(usage.get("percentage") or 0.0),
        usage.get("totalTokens"),
        usage.get("maxTokens"),
        usage.get("model"),
    )
    await send_md(
        message,
        format_context_usage(
            usage,
            ctx.tr,
            provider=ctx.agent.provider,
            model=ctx.agent.current_model(message.chat.id),
        ),
    )


async def stop_query(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    """Interrupt the running agent turn for `/stop`."""
    try:
        interrupted = await ctx.agent.interrupt(message.chat.id)
    except Exception as e:
        cl.exception("interrupt failed: %s", e)
        await send_md(
            message, ctx.tr.t("error_internal", error=type(e).__name__)
        )
        return
    cl.info("/stop interrupted=%s", interrupted)
    await send_md(
        message, ctx.tr.t("stop_ok" if interrupted else "stop_idle")
    )


async def show_mcp(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    """Report connected MCP servers for `/mcp`."""
    await ctx.bot.send_chat_action(message.chat.id, "typing")
    try:
        status = await ctx.agent.get_mcp_status(message.chat.id)
    except Exception as e:
        cl.exception("get_mcp_status failed: %s", e)
        await send_md(
            message, ctx.tr.t("mcp_error", error=type(e).__name__)
        )
        return
    cl.info("/mcp servers=%d", len(status.get("mcpServers") or []))
    await send_md(message, format_mcp_status(status, ctx.tr))


async def show_info(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    """Report agent/server info for `/info`."""
    await ctx.bot.send_chat_action(message.chat.id, "typing")
    try:
        info = await ctx.agent.get_server_info(message.chat.id)
    except Exception as e:
        cl.exception("get_server_info failed: %s", e)
        await send_md(
            message, ctx.tr.t("info_error", error=type(e).__name__)
        )
        return
    if info is None:
        await send_md(message, ctx.tr.t("info_unavailable"))
        return
    cl.info("/info commands=%d", len(info.get("commands") or []))
    await send_md(message, format_server_info(info, ctx.tr))


async def whoami(message: Message, ctx: BotContext, **_: object) -> None:
    """Report the sender's chat/user identity, access, mode, and session."""
    chat_id = message.chat.id
    if ctx.cfg.allowed_for_all:
        access = ctx.tr.t("whoami_access_open")
    else:
        access = ctx.tr.t("whoami_access_allowed")
    mode = ctx.agent.current_mode(chat_id)
    session_key = (
        "whoami_session_yes" if ctx.agent.has_session(chat_id)
        else "whoami_session_no"
    )
    user = message.from_user
    none = ctx.tr.t("whoami_none")
    if user is not None:
        user_id = str(user.id)
        username = f"@{user.username}" if user.username else none
        name = " ".join(
            filter(None, [user.first_name, user.last_name])
        ) or none
        lang = user.language_code or none
        premium = (
            ctx.tr.t("whoami_yes") if user.is_premium
            else ctx.tr.t("whoami_no")
        )
    else:
        user_id = username = name = lang = none
        premium = none
    await send_md(
        message,
        ctx.tr.t(
            "whoami_body",
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            name=name,
            lang=lang,
            premium=premium,
            access=access,
            mode=mode,
            session=ctx.tr.t(session_key),
        ),
    )


def _help_item(ctx: BotContext, bc: BotCommand) -> str:
    """Format one `/help` line, appending a usage example when one exists."""
    line = f"**/{bc.command}** — {bc.description}"
    if bc.command in _HELP_EXAMPLES:
        key = f"help_example_{bc.command}"
        ex = ctx.tr.t(key)
        if ex and ex != key:
            line += f" — _{ex}_"
    return line


async def show_help(message: Message, ctx: BotContext, **_: object) -> None:
    """Render the grouped command reference for `/help`."""
    # Inline-bold headers + single-newline item lines keep the layout compact:
    # `##`/`-` would render as block elements (h2/ul/li) whose Rich Message
    # margins stack into large vertical gaps.
    by_name = {bc.command: bc for bc in ctx.bot_command_list}
    grouped: set[str] = set()
    sections: list[str] = [f"**{ctx.tr.t('help_header')}**"]

    for header_key, names in _HELP_GROUPS:
        items = [_help_item(ctx, by_name[n]) for n in names if n in by_name]
        grouped.update(n for n in names if n in by_name)
        if items:
            sections.append(f"**{ctx.tr.t(header_key)}**\n" + "\n".join(items))

    # Anything left is a user-defined custom command.
    custom = [_help_item(ctx, bc) for bc in ctx.bot_command_list if bc.command not in grouped]
    if custom:
        sections.append(f"**{ctx.tr.t('help_group_custom')}**\n" + "\n".join(custom))

    await send_md(message, "\n\n".join(sections))


def register(dp: Dispatcher) -> None:
    """Register the static-command handlers on ``dp``."""
    dp.message.register(start, CommandStart())
    dp.message.register(new_session, Command("new"))
    dp.message.register(cancel_pending, Command("cancel"))
    dp.message.register(show_context, Command("context"))
    dp.message.register(stop_query, Command("stop"))
    dp.message.register(show_mcp, Command("mcp"))
    dp.message.register(show_info, Command("info"))
    dp.message.register(whoami, Command("whoami"))
    dp.message.register(show_help, Command("help"))
