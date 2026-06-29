"""Agent-facing `task` tool: lets the LLM schedule and manage reminders/tasks.

Mirrors Hermes' single compressed `cronjob` tool — one action-dispatched tool
instead of one per verb, to keep the schema small. The tool is built *per chat*:
`build_task_server(chat_id, service)` closes over the owner's ``chat_id`` so the
agent never sees or supplies it, and every operation runs through the same
`TaskService` the `/task` command uses (same permission and scan rules).

The result is an in-process SDK MCP server; the agent calls the tool as
``mcp__tasks__task``.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from ..services.task_service import TaskError, TaskPermissionError, TaskService
from .task_types import Task

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

_TASK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "list", "show", "pause", "resume", "run", "rm"],
            "description": "What to do. 'create' needs schedule (+prompt). "
            "list/show/pause/resume/run/rm act on existing tasks.",
        },
        "schedule": {
            "type": "string",
            "description": "REQUIRED for create. A duration ('2m', '30m', '2h', "
            "'1d'), an interval ('every 30m', 'every 1d'), a 5-field cron "
            "('0 9 * * *'), or an ISO timestamp ('2026-06-23T09:00'). "
            "Durations and timestamps are one-shot; 'every ...' and cron recur.",
        },
        "prompt": {
            "type": "string",
            "description": "REQUIRED for create (LLM tasks). The instruction run "
            "at fire time; its output is delivered back to this chat. Write it "
            "self-contained, e.g. 'Remind the user to take a break.'",
        },
        "name": {
            "type": "string",
            "description": "Optional short human-friendly label.",
        },
        "task_id": {
            "type": "string",
            "description": "REQUIRED for show/pause/resume/run/rm: the task id.",
        },
    },
    "required": ["action"],
}


def _fmt_task(task: Task) -> dict[str, Any]:
    """Render a task as the compact JSON-safe dict returned to the agent."""
    return {
        "id": task.id,
        "name": task.name or None,
        "kind": task.kind,
        "scope": task.scope,
        "schedule": task.schedule.display,
        "state": task.state,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
    }


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a success ``payload`` as an MCP text-content tool result."""
    text = json.dumps({"success": True, **payload}, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _err(message: str) -> dict[str, Any]:
    """Wrap an error ``message`` as an MCP error tool result."""
    text = json.dumps({"success": False, "error": message}, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def make_task_handler(
    chat_id: int, service: TaskService
) -> "Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]":
    """Build the bare async tool handler bound to ``chat_id`` (unit-testable)."""

    async def handle(args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one `task` tool call to the service and shape the result."""
        action = str(args.get("action", "")).strip().lower()
        try:
            if action == "create":
                schedule = str(args.get("schedule") or "").strip()
                if not schedule:
                    return _err("schedule is required for create")
                created = await service.create(
                    chat_id,
                    schedule_text=schedule,
                    prompt=str(args.get("prompt") or ""),
                    name=str(args.get("name") or ""),
                )
                log.info("agent created task %s for chat %s", created.id, chat_id)
                return _ok(
                    {"task": _fmt_task(created), "message": f"Task {created.id} scheduled."}
                )
            if action == "list":
                tasks = await service.list(chat_id)
                return _ok({"tasks": [_fmt_task(t) for t in tasks]})
            if action == "show":
                task_id = str(args.get("task_id") or "").strip()
                if not task_id:
                    return _err("task_id is required for show")
                task = await service.act(chat_id, "show", task_id)
                last = await service.last_run(chat_id, task_id)
                payload: dict[str, Any] = {
                    "task": _fmt_task(task),
                    "message": f"Task {task.id}: show ok.",
                }
                if last is not None:
                    payload["last_run"] = {
                        "status": last.status,
                        "finished_at": last.finished_at.isoformat(),
                        "session_id": last.session_id,
                        "log_path": last.log_path,
                    }
                return _ok(payload)
            if action in ("pause", "resume", "run", "rm"):
                task_id = str(args.get("task_id") or "").strip()
                if not task_id:
                    return _err(f"task_id is required for {action}")
                task = await service.act(chat_id, action, task_id)
                log.info("agent %s task %s for chat %s", action, task.id, chat_id)
                return _ok({"task": _fmt_task(task), "message": f"Task {task.id}: {action} ok."})
            return _err(f"unknown action: {action!r}")
        except TaskPermissionError as e:
            return _err(f"permission denied: {e}")
        except TaskError as e:
            return _err(str(e))
        except Exception as e:  # never surface a raw traceback to the model
            log.exception("task tool failed (chat %s, action %s)", chat_id, action)
            return _err(f"internal error: {type(e).__name__}: {e}")

    return handle


def build_task_server(chat_id: int, service: TaskService) -> McpSdkServerConfig:
    """Build an in-process MCP server exposing the `task` tool for ``chat_id``."""
    task_tool = tool(
        "task",
        "Schedule and manage the user's reminders / scheduled tasks.",
        _TASK_INPUT_SCHEMA,
    )(make_task_handler(chat_id, service))
    return create_sdk_mcp_server(name="tasks", version="1.0.0", tools=[task_tool])


# The agent invokes the tool under this MCP-qualified name.
TASK_TOOL_NAME = "mcp__tasks__task"
