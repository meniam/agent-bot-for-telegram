"""Shared scheduled-task logic for the `/task` handler and the `task` tool.

Parsing of human input (the `/task add 2m | text` string, or the tool's JSON
args) stays at the call site; this service owns everything that must behave
identically regardless of who calls it: permission rules, prompt scanning,
validation, and the `TaskStore` read-modify-write. Methods raise `TaskError`
subclasses with user-facing messages; callers format them for their channel.
"""

import re
from datetime import datetime

from ..config import BotConfig, is_admin
from ..infra.task_store import TaskStore, new_task_id
from ..infra.task_types import Task, TaskRun, TaskScope, compute_next_run, parse_schedule

# Prompt-injection / dangerous-command patterns, scanned on every LLM task
# prompt at create time. Ported from Hermes' cron scanner (strict set): a
# legitimate reminder prompt has no business carrying these.
_THREAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), label)
    for pat, label in (
        (r"ignore\s+(?:\w+\s+)*(?:previous|all|above|prior)\s+(?:\w+\s+)*instructions", "prompt_injection"),
        (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
        (r"system\s+prompt\s+override", "sys_prompt_override"),
        (r"disregard\s+(?:your|all|any)\s+(?:instructions|rules|guidelines)", "disregard_rules"),
        (r"cat\s+[^\n]*(?:\.env|credentials|\.netrc|\.pgpass)", "read_secrets"),
        (r"authorized_keys", "ssh_backdoor"),
        (r"/etc/sudoers|visudo", "sudoers_mod"),
        (r"rm\s+-rf\s+/", "destructive_root_rm"),
    )
)

# Zero-width / bidi control characters used to smuggle hidden instructions.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")

class TaskError(Exception):
    """Base for task-operation failures; ``str(e)`` is user-facing."""


class TaskPermissionError(TaskError):
    """Caller lacks admin rights for a global or script task."""


class TaskNotFoundError(TaskError):
    """No task with the given id is visible to the caller."""


class TaskValidationError(TaskError):
    """Bad schedule, missing prompt, or a scanned-unsafe prompt."""


def scan_prompt(text: str) -> str | None:
    """Return a reason string if ``text`` looks unsafe, else None."""
    if _INVISIBLE.search(text):
        return "prompt contains invisible/control characters"
    for pattern, label in _THREAT_PATTERNS:
        if pattern.search(text):
            return f"prompt rejected ({label})"
    return None


class TaskService:
    """Permission-checked CRUD over `TaskStore`, shared by handler and tool."""

    def __init__(self, store: TaskStore, cfg: BotConfig) -> None:
        """Bind the service to a `TaskStore` and the owning bot config."""
        self._store = store
        self._cfg = cfg

    def _now(self) -> datetime:
        """Return the current time as a timezone-aware datetime."""
        return datetime.now().astimezone()

    def is_admin(self, chat_id: int) -> bool:
        """Whether ``chat_id`` may manage global tasks and create scripts."""
        return is_admin(self._cfg, chat_id)

    def _visible(self, task: Task, chat_id: int, *, admin: bool) -> bool:
        """Whether ``chat_id`` may see ``task`` (global tasks need admin)."""
        if task.scope == "global":
            return admin
        return task.owner_chat_id == chat_id

    async def create(
        self,
        chat_id: int,
        *,
        schedule_text: str,
        prompt: str = "",
        name: str = "",
        scope: TaskScope = "user",
        script: str | None = None,
        exclusive: bool = False,
    ) -> Task:
        """Create and persist a task. Raises `TaskError` on any rejection."""
        admin = self.is_admin(chat_id)
        kind = "script" if script else "llm"

        if scope == "global" and not admin:
            raise TaskPermissionError("only admins can create global tasks")
        if kind == "script" and not admin:
            raise TaskPermissionError("only admins can create script tasks")

        prompt = prompt.strip()
        if kind == "llm":
            if not prompt:
                raise TaskValidationError("missing prompt")
            reason = scan_prompt(prompt)
            if reason:
                raise TaskValidationError(reason)

        try:
            schedule = parse_schedule(schedule_text.strip(), now=self._now())
        except ValueError as e:
            raise TaskValidationError(str(e)) from e

        task = Task(
            id=new_task_id(),
            owner_chat_id=chat_id,
            scope=scope,
            name=name.strip(),
            kind=kind,  # type: ignore[arg-type]
            schedule=schedule,
            prompt=prompt or None,
            script=script or None,
            exclusive=exclusive or kind == "llm",
            next_run_at=compute_next_run(schedule, now=self._now()),
        )
        return await self._store.add(task)

    async def list(self, chat_id: int) -> list[Task]:
        """Return the caller's tasks, plus global ones when the caller is admin."""
        return await self._store.list_all(
            chat_id, include_global=self.is_admin(chat_id)
        )

    async def get(self, chat_id: int, task_id: str) -> Task:
        """Fetch a visible task or raise `TaskNotFoundError`."""
        admin = self.is_admin(chat_id)
        task = await self._store.get(task_id.strip()) if task_id.strip() else None
        if task is None or not self._visible(task, chat_id, admin=admin):
            raise TaskNotFoundError(task_id or "?")
        return task

    async def last_run(self, chat_id: int, task_id: str) -> TaskRun | None:
        """Return the most recent run record for a visible task, or None if none."""
        task = await self.get(chat_id, task_id)  # permission-checked
        runs = await self._store.list_history(task.id)
        return runs[-1] if runs else None

    async def act(self, chat_id: int, action: str, task_id: str) -> Task:
        """Apply a state action (show/pause/resume/run/rm) to a visible task."""
        if action not in ("show", "pause", "resume", "run", "rm"):
            raise TaskValidationError(f"unknown action: {action}")
        task = await self.get(chat_id, task_id)
        if action == "show":
            return task
        if action == "rm":
            await self._store.remove(task)
            return task
        if action == "pause":
            return await self._store.update(
                task.model_copy(update={"enabled": False, "state": "paused"})
            )
        if action == "resume":
            nxt = compute_next_run(
                task.schedule, last_run=task.last_run_at, now=self._now()
            )
            return await self._store.update(
                task.model_copy(
                    update={"enabled": True, "state": "scheduled", "next_run_at": nxt}
                )
            )
        # action == "run" (guarded above)
        return await self._store.update(
            task.model_copy(
                update={
                    "enabled": True,
                    "state": "scheduled",
                    "next_run_at": self._now(),
                }
            )
        )
