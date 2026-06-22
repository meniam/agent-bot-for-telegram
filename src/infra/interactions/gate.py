"""`TelegramInteractionGate` — the class Claude SDK plugs into via `can_use_tool`.

The class itself stays thin: it holds shared per-bot state (translator, bot
client, in-flight prompt registries) plus a handful of helpers used across
flows. The four tool flows live in sibling modules and operate on the gate
via free functions.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ...i18n import Translator
from . import ask_user_question as aq
from . import permission_prompt as pp
from . import plan_mode as pm
from . import push_notification as push

log = logging.getLogger(__name__)

DEFAULT_APPROVAL_TIMEOUT_SEC = 300


class _AQSession:
    """Per-question state for an AskUserQuestion turn."""

    __slots__ = (
        "build_kb",
        "chat_id",
        "fut",
        "message_id",
        "multi",
        "options",
        "selected",
    )

    def __init__(
        self,
        fut: asyncio.Future[list[int] | None],
        options: list[dict[str, Any]],
        multi: bool,
        chat_id: int,
        message_id: int,
        build_kb: Callable[[], InlineKeyboardMarkup],
    ) -> None:
        """Capture the future, options, selection state, and keyboard builder."""
        self.fut = fut
        self.options: list[dict[str, Any]] = options
        self.selected: set[int] = set()
        self.multi = multi
        self.chat_id = chat_id
        self.message_id = message_id
        self.build_kb = build_kb


class TelegramInteractionGate:
    """Boundary between the SDK's tool-permission checks and Telegram UX.

    `can_use_tool` is the single entry point the backend calls per tool use; it
    routes the four special tools to their flow modules and everything else to
    the generic Allow/Deny/Always prompt. The gate owns the in-flight prompt
    registries (`_pending`, `_aq`, `_plan_pending`); the flows themselves live
    in sibling modules and mutate that state through free functions.
    """

    def __init__(
        self,
        bot: Bot,
        translator: Translator,
        approval_timeout_sec: int = DEFAULT_APPROVAL_TIMEOUT_SEC,
        send_md_callback: Callable[[int, str], Awaitable[None]] | None = None,
        chat_logger: Callable[[int], logging.Logger] | None = None,
    ) -> None:
        """Initialize shared per-bot state and the in-flight prompt registries.

        ``send_md_callback`` (if given) renders Markdown plan bodies, and
        ``chat_logger`` routes verdicts into per-chat log files; both fall back
        to plain sends / the module logger when omitted.
        """
        self._bot = bot
        self._t = translator
        self._timeout = approval_timeout_sec
        # Markdown sender. `ExitPlanMode` uses it to render the plan body
        # with code-block highlighting. Falls back to plain `send_message`
        # if the caller did not provide one.
        self._send_md = send_md_callback
        # Per-chat logger lookup so verdicts (Allow/Deny/Always, AQ picks,
        # plan approvals) land in the same `<chat_id>.log` file as the
        # other agent-flow events. Falls back to the module logger.
        self._chat_logger = chat_logger
        # request_id -> (Future with decision, tool_name, expected_chat_id,
        # prompt_message_id). prompt_message_id is None until the inline-
        # button message is sent successfully; we use it to delete the
        # prompt on click / timeout so the chat does not pile up with stale
        # permission requests.
        self._pending: dict[
            str, tuple[asyncio.Future[str], str, int, int | None]
        ] = {}
        # AskUserQuestion sessions: request_id -> session state.
        # Keyed by a short token embedded in callback_data (`aq:<rid>:...`).
        self._aq: dict[str, _AQSession] = {}
        # Chats with a pending "abort" signal — the next iteration of the
        # AskUserQuestion loop returns immediately and any remaining
        # questions are recorded as skipped.
        self._aq_aborted: set[int] = set()
        # ExitPlanMode sessions per chat: chat_id -> (future, request_id,
        # prompt message id). Future resolves to ("approve", "") on click,
        # ("reject", feedback or "") on Reject or on freeform text reply.
        self._plan_pending: dict[
            int, tuple[asyncio.Future[tuple[str, str]], str, int]
        ] = {}

    # ----- shared helpers -----

    def _cl(self, chat_id: int) -> logging.Logger:
        """Return the per-chat logger, falling back to the module logger."""
        if self._chat_logger is not None:
            try:
                return self._chat_logger(chat_id)
            except Exception:
                log.exception("chat_logger lookup failed for %s", chat_id)
        return log

    async def _delete_prompt(self, chat_id: int, message_id: int) -> None:
        """Best-effort delete a prompt message, swallowing failures."""
        try:
            await self._bot.delete_message(chat_id, message_id)
        except Exception:
            log.debug("could not delete permission prompt", exc_info=True)

    def _format_request(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        ctx: ToolPermissionContext,
    ) -> str:
        """Build the human-readable prompt body for a generic permission request.

        Combines the context title/description/reason/blocked path with a
        truncated preview of ``tool_input`` into a single block of text.
        """
        t = self._t
        head = ctx.title or t.t("permission_request_default_title", tool=tool_name)
        parts: list[str] = [head]
        if ctx.description and ctx.description != head:
            parts.append(ctx.description)
        if ctx.decision_reason:
            parts.append(t.t("permission_request_reason", reason=ctx.decision_reason))
        if ctx.blocked_path:
            parts.append(t.t("permission_request_path", path=ctx.blocked_path))
        if tool_input:
            try:
                preview = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
            except Exception:
                preview = str(tool_input)
            if len(preview) > 800:
                preview = preview[:800] + "…"
            parts.append(t.t("permission_request_params", params=preview))
        return "\n\n".join(parts)

    # ----- dispatcher -----

    async def can_use_tool(
        self,
        chat_id: int,
        tool_name: str,
        tool_input: dict[str, Any],
        ctx: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Route one tool-permission check to its flow and await the verdict.

        `AskUserQuestion` / `ExitPlanMode` / `PushNotification` get bespoke flows;
        every other tool gets the generic Allow/Deny/Always prompt. Blocks until
        the user responds or the flow times out (timeout resolves as deny).
        """
        if tool_name == "AskUserQuestion":
            return await aq.handle(self, chat_id, tool_input)
        if tool_name == "ExitPlanMode":
            return await pm.handle(self, chat_id, tool_input)
        if tool_name == "PushNotification":
            return await push.handle(self, chat_id, tool_input)
        return await pp.handle(self, chat_id, tool_name, tool_input, ctx)

    # ----- per-flow callback / text intake (thin wrappers) -----

    async def handle_callback(self, callback: CallbackQuery) -> None:
        """Route a generic permission (`perm:`) button tap to its flow."""
        await pp.on_callback(self, callback)

    async def handle_aq_callback(self, callback: CallbackQuery) -> None:
        """Route an AskUserQuestion (`aq:`) button tap to its flow."""
        await aq.on_callback(self, callback)

    async def handle_plan_callback(self, callback: CallbackQuery) -> None:
        """Route an ExitPlanMode (`plan:`) button tap to its flow."""
        await pm.on_callback(self, callback)

    async def cancel_active_aq(self, chat_id: int) -> None:
        """Abort the in-flight AskUserQuestion turn for this chat (if any).

        Resolves the currently-shown question's future as a skip and arms
        the abort flag so the loop returns immediately on the next
        iteration without rendering more questions. Safe to call when no
        AskUserQuestion is running.
        """
        active = [s for s in self._aq.values() if s.chat_id == chat_id]
        if not any(not s.fut.done() for s in active):
            return
        self._aq_aborted.add(chat_id)
        for session in active:
            if not session.fut.done():
                session.fut.set_result(None)

    def consume_plan_text(self, chat_id: int, text: str) -> bool:
        """Feed a freeform text message to a pending ExitPlanMode prompt.

        Returns True if the chat had a pending plan future and we consumed
        the text as rejection-with-feedback. Returns False if the caller
        should treat the text as a normal user message instead.
        """
        return pm.consume_text(self, chat_id, text)


# Re-export the session shape so per-flow modules can import it cleanly.
__all__ = ["TelegramInteractionGate", "_AQSession"]
