"""Mirror Claude SDK tool lifecycle events into the Telegram chat.

`pre` fires for every tool — we skip the ones whose UX is already provided by
the interaction gate. `post` fires only for the small set of tools whose tail
output is actually useful (Monitor, TaskOutput).
"""

import html as _html
import logging
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage

from ..i18n import Translator
from ..infra.logs import BotLogs
from ..infra.message_db import ROLE_TOOL
from .markdown import TG_LIMIT

log = logging.getLogger(__name__)

# Tools whose UX is already provided by the interaction gate — skip the
# generic pre-tool announcement so the chat does not see two notices.
_GATE_HANDLED_TOOLS = frozenset({
    "AskUserQuestion",
    "ExitPlanMode",
    "PushNotification",
})

# Per-tool preferred input field for the brief status line. Tools not
# listed fall back to the first scalar value in `tool_input`.
_TOOL_PRIMARY_FIELD: dict[str, str] = {
    "Bash": "command",
    "BashOutput": "bash_id",
    "KillShell": "shell_id",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
    "Grep": "pattern",
    "Glob": "pattern",
    "WebFetch": "url",
    "WebSearch": "query",
    "Task": "description",
    "Skill": "skill",
    "Monitor": "description",
    "TaskOutput": "task_id",
    "ToolSearch": "query",
    "PI": "status",
    "Codex": "status",
}

_TOOL_PATH_FIELDS = frozenset({
    "file_path",
    "notebook_path",
})

_STATUS_LINE_MAX = 88

_TOOL_EMOJI: dict[str, str] = {
    "bash": "⌨️",
    "bashoutput": "⌨️",
    "killshell": "⏹",
    "read": "📖",
    "write": "✍️",
    "edit": "✏️",
    "multiedit": "✏️",
    "notebookedit": "📓",
    "grep": "🔎",
    "glob": "🗂",
    "webfetch": "🌐",
    "websearch": "🔎",
    "task": "🧩",
    "skill": "🛠",
    "monitor": "📡",
    "taskoutput": "📋",
    "toolsearch": "🧰",
    "pi": "🧠",
    "codex": "🧠",
}


def _one_line(text: str, limit: int = _STATUS_LINE_MAX) -> str:
    """Collapse whitespace to one line and truncate with an ellipsis."""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(limit - 1, 0)].rstrip() + "…"


def _tool_display(tool_name: str) -> str:
    """Prefix the tool name with its emoji (a wrench for unknown tools)."""
    emoji = _TOOL_EMOJI.get(tool_name.replace("_", "").replace("-", "").lower(), "🔧")
    return f"{emoji} {tool_name}"


def _tool_input_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the payload's `tool_input` dict, or the payload itself."""
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        return tool_input
    return payload


def _format_path_brief(path: str, working_dir: Path | None) -> str:
    """Shorten a path: `@/…` relative to working_dir, else its last parts."""
    raw = Path(path).expanduser()
    if working_dir is not None:
        if raw.is_absolute():
            try:
                rel = raw.resolve(strict=False).relative_to(working_dir)
            except ValueError:
                pass
            else:
                return "@" if str(rel) == "." else f"@/{rel}"
        else:
            return f"@/{raw}"

    parts = [part for part in raw.parts if part != raw.anchor]
    if len(parts) <= 3:
        return str(raw)
    return ".../" + "/".join(parts[-3:])


def _tool_brief(
    tool_name: str,
    tool_input: dict[str, Any],
    working_dir: Path | None = None,
) -> str:
    """Build a short status description from the tool's primary input field."""
    if tool_name == "TodoWrite":
        todos = tool_input.get("todos") or []
        return f"{len(todos)} todo(s)"
    field = _TOOL_PRIMARY_FIELD.get(tool_name)
    if field and field in tool_input:
        value = str(tool_input[field])
        if field in _TOOL_PATH_FIELDS:
            value = _format_path_brief(value, working_dir)
        return value[:300]
    for k, v in tool_input.items():
        if k in {"content", "new_string", "old_string"}:
            continue
        if isinstance(v, (str, int, float, bool)):
            return f"{k}={str(v)[:200]}"
    return ""


class ToolStatusMirror:
    """Render Claude SDK tool lifecycle events as a live chat status line."""

    def __init__(
        self,
        bot: Bot,
        tr: Translator,
        bot_logs: BotLogs,
        glog: logging.Logger,
        bot_name: str,
        working_dir: str | None = None,
    ) -> None:
        """Store collaborators and resolve the optional working directory."""
        self._bot = bot
        self._tr = tr
        self._bot_logs = bot_logs
        self._glog = glog
        self._bot_name = bot_name
        self._last_status_message: dict[int, int] = {}
        self._working_dir = (
            Path(working_dir).expanduser().resolve(strict=False)
            if working_dir
            else None
        )

    def begin_turn(self, chat_id: int) -> None:
        """Start a fresh visible status line for the next agent turn."""
        self._last_status_message.pop(chat_id, None)

    async def _upsert_status(self, chat_id: int, body: str) -> None:
        """Edit the chat's status message in place, or send a new one."""
        body = _one_line(body)
        html_body = f"<code>{_html.escape(body[:TG_LIMIT])}</code>"
        rich = InputRichMessage(html=html_body)
        message_id = self._last_status_message.get(chat_id)
        if message_id is not None:
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    rich_message=rich,
                )
                return
            except TelegramBadRequest as e:
                if "message is not modified" in str(e).lower():
                    return
                log.debug("tool status edit failed; sending new status", exc_info=True)
        try:
            sent = await self._bot.send_rich_message(
                chat_id=chat_id,
                rich_message=rich,
                disable_notification=True,
            )
        except TelegramBadRequest:
            log.exception("tool status send failed")
            return
        self._last_status_message[chat_id] = sent.message_id

    async def handle(
        self,
        chat_id: int,
        phase: str,
        tool_name: str,
        payload: dict[str, Any],
    ) -> None:
        """Mirror one pre/post tool event into the chat, logging on failure."""
        cl = self._bot_logs.for_chat(chat_id)
        try:
            tool_display = _tool_display(tool_name)
            if phase == "pre":
                if tool_name in _GATE_HANDLED_TOOLS:
                    return
                tool_input = _tool_input_from_payload(payload)
                desc = _tool_brief(tool_name, tool_input, self._working_dir)
                if desc:
                    body = self._tr.t(
                        "tool_status_pre", tool=tool_display, desc=desc
                    )
                else:
                    body = self._tr.t(
                        "tool_status_pre_no_desc", tool=tool_display
                    )
                cl.info(
                    "hook %s: %s",
                    phase,
                    body.replace("\n", " ⏎ "),
                    extra={"role": ROLE_TOOL, "tool": tool_name},
                )
                await self._upsert_status(chat_id, body)
            elif phase == "post":
                response = payload.get("tool_response")
                # Tool response shapes vary across tools; best-effort extract.
                preview = ""
                if isinstance(response, dict):
                    preview = str(
                        response.get("output")
                        or response.get("stdout")
                        or response.get("text")
                        or response.get("result")
                        or ""
                    )
                elif isinstance(response, str):
                    preview = response
                preview_lines = preview.strip().splitlines()[:6]
                preview = "\n".join(preview_lines)[:600]
                if preview:
                    body = self._tr.t(
                        "tool_status_post_with_preview",
                        tool=tool_display,
                        preview=preview,
                    )
                    cl.info(
                        "hook %s: %s",
                        phase,
                        body.replace("\n", " ⏎ "),
                        extra={"role": ROLE_TOOL, "tool": tool_name},
                    )
                    await self._upsert_status(chat_id, body)
                else:
                    cl.info(
                        "hook %s: %s",
                        phase,
                        self._tr.t("tool_status_post", tool=tool_display),
                        extra={"role": ROLE_TOOL, "tool": tool_name},
                    )
                    return
        except Exception:
            self._glog.exception(
                "[%s] tool-event delivery failed", self._bot_name
            )
