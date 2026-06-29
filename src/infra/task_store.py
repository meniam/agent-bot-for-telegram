"""Per-chat scheduled-task store.

Mirrors the shape of `SessionStore` but for `Task` records. Layout under the
per-bot ``tasks_dir`` (already bot-specific from config, so no ``<bot_name>``
is appended):

    <tasks_dir>/<chat_id>.json            user-scoped task definitions
    <tasks_dir>/global.json               global (admin) task definitions
    <tasks_dir>/history/<task_id>/<ts>.json   append-only run history
    <tasks_dir>/_corrupt/<chat_id>.<ts>.json  quarantined unparsable files

Each definition file is ``{"updated_at": <iso>, "tasks": [ ... ]}``.

Writes are atomic *and* durable: temp file -> flush -> ``os.fsync`` ->
``os.replace`` -> ``chmod 0600`` (stricter than `SessionStore` because losing a
scheduled task means a promised run silently never happens). Read-modify-write
cycles are serialized by an in-process ``asyncio.Lock``; plain reads parse fresh
and rely on the atomic replace to never observe a half-written file.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .task_types import Task, TaskRun

log = logging.getLogger(__name__)

_TASK_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_GLOBAL_STEM = "global"


def _now_iso() -> str:
    """Return the current local time as an ISO-8601 string."""
    return datetime.now().astimezone().isoformat()


def new_task_id() -> str:
    """Mint a fresh 12-hex task id (also a filesystem path component)."""
    return secrets.token_hex(6)


class TaskStore:
    """JSON-per-chat persistence for scheduled tasks and their run history."""

    def __init__(self, base_dir: Path, *, history_limit: int = 100) -> None:
        """Store tasks under ``base_dir``, keeping at most ``history_limit`` runs per task."""
        self._base = base_dir
        self._history_limit = history_limit
        self._lock = asyncio.Lock()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._secure_dir(base_dir)

    # ----- paths -----------------------------------------------------------

    def _scope_path(self, task: Task) -> Path:
        """Return the definition file a task belongs in (global vs. per-owner)."""
        if task.scope == "global":
            return self._base / f"{_GLOBAL_STEM}.json"
        return self._base / f"{task.owner_chat_id}.json"

    def _user_path(self, chat_id: int) -> Path:
        """Return a chat's user-task definition file path."""
        return self._base / f"{chat_id}.json"

    def _global_path(self) -> Path:
        """Return the global-task definition file path."""
        return self._base / f"{_GLOBAL_STEM}.json"

    def _history_dir(self, task_id: str) -> Path:
        """Return a task's history directory; reject ids unsafe as path parts."""
        if not _TASK_ID_RE.match(task_id):
            raise ValueError(f"unsafe task id: {task_id!r}")
        return self._base / "history" / task_id

    # ----- low-level io ----------------------------------------------------

    @staticmethod
    def _secure_dir(path: Path) -> None:
        """Best-effort restrict a directory to owner-only (0700)."""
        with contextlib.suppress(OSError, NotImplementedError):
            path.chmod(0o700)

    @staticmethod
    def _secure_file(path: Path) -> None:
        """Best-effort restrict a file to owner-only (0600)."""
        with contextlib.suppress(OSError, NotImplementedError):
            path.chmod(0o600)

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        """Write ``data`` as JSON atomically and durably (temp, fsync, replace)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".tasks_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            Path(tmp).replace(path)
            self._secure_file(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _quarantine(self, path: Path) -> None:
        """Move an unparsable file aside so its data is not lost or re-read."""
        corrupt_dir = self._base / "_corrupt"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        stamp = _now_iso().replace(":", "-")
        dest = corrupt_dir / f"{path.stem}.{stamp}.json"
        try:
            path.replace(dest)
            log.error("task store: quarantined corrupt file %s -> %s", path, dest)
        except OSError:
            log.exception("task store: failed to quarantine %s", path)

    def _read_file(self, path: Path) -> list[Task]:
        """Parse a definition file into tasks; quarantine it if unreadable.

        A missing file yields ``[]``. Unparsable or malformed files are moved
        aside; individual invalid task entries are logged and skipped.
        """
        if not path.exists():
            return []
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            self._quarantine(path)
            return []
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            self._quarantine(path)
            return []
        tasks: list[Task] = []
        for raw in data["tasks"]:
            try:
                tasks.append(Task.model_validate(raw))
            except ValueError:
                log.warning("task store: skipping invalid task entry in %s", path)
        return tasks

    def _write_file(self, path: Path, tasks: list[Task]) -> None:
        """Atomically write a definition file holding ``tasks``."""
        payload = {
            "updated_at": _now_iso(),
            "tasks": [t.model_dump(mode="json") for t in tasks],
        }
        self._atomic_write(path, payload)

    def _all_files(self) -> list[Path]:
        """Return all top-level definition files (sorted; excludes subdirs)."""
        return sorted(
            p
            for p in self._base.glob("*.json")
            if p.parent == self._base
        )

    # ----- reads (no lock; atomic replace guarantees whole files) ----------
    #
    # Public reads are async: the file glob + JSON parse runs in a worker thread
    # (via ``asyncio.to_thread``) so it never blocks the event loop. The
    # ``_*_sync`` helpers hold the actual disk I/O.

    def _list_all_sync(self, chat_id: int, include_global: bool) -> list[Task]:
        tasks = self._read_file(self._user_path(chat_id))
        if include_global:
            tasks += self._read_file(self._global_path())
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    async def list_all(
        self, chat_id: int, *, include_global: bool = False
    ) -> list[Task]:
        """List a chat's own user tasks, newest first (by ``created_at``).

        When ``include_global`` is set, global tasks are folded in as well (for
        admin callers).
        """
        return await asyncio.to_thread(self._list_all_sync, chat_id, include_global)

    def _list_global_sync(self) -> list[Task]:
        tasks = self._read_file(self._global_path())
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    async def list_global(self) -> list[Task]:
        """List all global tasks, newest first (by ``created_at``)."""
        return await asyncio.to_thread(self._list_global_sync)

    def _list_due_sync(self, now: datetime) -> list[Task]:
        due: list[Task] = []
        for path in self._all_files():
            for task in self._read_file(path):
                if not task.enabled or task.next_run_at is None:
                    continue
                if task.next_run_at <= now:
                    due.append(task)
        return due

    async def list_due(self, now: datetime) -> list[Task]:
        """Every enabled task whose next_run_at is at or before ``now``."""
        return await asyncio.to_thread(self._list_due_sync, now)

    def _get_sync(self, task_id: str) -> Task | None:
        for path in self._all_files():
            for task in self._read_file(path):
                if task.id == task_id:
                    return task
        return None

    async def get(self, task_id: str) -> Task | None:
        """Find a task by id across all scope files, or None if absent."""
        return await asyncio.to_thread(self._get_sync, task_id)

    # ----- mutations (serialized) ------------------------------------------

    async def add(self, task: Task) -> Task:
        """Persist a new task (assigning an id if needed) into its scope file.

        Validates any caller-supplied id and inserts the task newest-first under
        the store lock. Returns the stored task.
        """
        if not task.id:
            task = task.model_copy(update={"id": new_task_id()})
        elif not _TASK_ID_RE.match(task.id):
            raise ValueError(f"unsafe task id: {task.id!r}")
        async with self._lock:
            path = self._scope_path(task)
            tasks = await asyncio.to_thread(self._read_file, path)
            tasks.insert(0, task)  # newest first in the stored file
            await asyncio.to_thread(self._write_file, path, tasks)
        return task

    async def update(self, task: Task) -> Task:
        """Replace the stored task with the same id (re-reads under the lock)."""
        async with self._lock:
            path = self._scope_path(task)
            tasks = await asyncio.to_thread(self._read_file, path)
            replaced = False
            for i, existing in enumerate(tasks):
                if existing.id == task.id:
                    tasks[i] = task
                    replaced = True
                    break
            if not replaced:
                tasks.append(task)
            await asyncio.to_thread(self._write_file, path, tasks)
        return task

    async def remove(self, task: Task) -> bool:
        """Delete a task and its history; return False if it was not present."""
        async with self._lock:
            path = self._scope_path(task)
            tasks = await asyncio.to_thread(self._read_file, path)
            kept = [t for t in tasks if t.id != task.id]
            if len(kept) == len(tasks):
                return False
            await asyncio.to_thread(self._write_file, path, kept)
        # History is small and bounded; drop it with the task.
        await asyncio.to_thread(self._remove_history, task.id)
        return True

    # ----- history ---------------------------------------------------------

    def _append_history_sync(self, run: TaskRun) -> None:
        hdir = self._history_dir(run.task_id)
        hdir.mkdir(parents=True, exist_ok=True)
        self._secure_dir(hdir)
        stamp = run.started_at.astimezone().isoformat().replace(":", "-")
        self._atomic_write(hdir / f"{stamp}.json", run.model_dump(mode="json"))
        self._prune_history(hdir)

    async def append_history(self, run: TaskRun) -> None:
        """Append one run record to a task's history and prune to the limit."""
        async with self._lock:
            await asyncio.to_thread(self._append_history_sync, run)

    def _prune_history(self, hdir: Path) -> None:
        """Delete the oldest history records (and their transcripts) beyond the limit."""
        records = sorted(hdir.glob("*.json"))
        excess = len(records) - self._history_limit
        for path in records[:excess]:
            path.unlink(missing_ok=True)
            # Drop the paired jsonl transcript (<stamp>.jsonl) if one was copied.
            path.with_name(f"{path.stem}.jsonl").unlink(missing_ok=True)

    def _copy_transcript_sync(
        self, task_id: str, started_at: datetime, src: Path
    ) -> None:
        if not src.is_file():
            log.warning("task store: transcript not found, skipping copy: %s", src)
            return
        hdir = self._history_dir(task_id)
        hdir.mkdir(parents=True, exist_ok=True)
        self._secure_dir(hdir)
        stamp = started_at.astimezone().isoformat().replace(":", "-")
        dst = hdir / f"{stamp}.jsonl"
        fd, tmp = tempfile.mkstemp(dir=str(dst.parent), suffix=".tmp", prefix=".tasks_")
        os.close(fd)
        try:
            shutil.copyfile(src, tmp)
            Path(tmp).replace(dst)
            self._secure_file(dst)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def copy_transcript(
        self, task_id: str, started_at: datetime, src: Path
    ) -> None:
        """Copy the run's SDK jsonl transcript next to its history record."""
        async with self._lock:
            await asyncio.to_thread(self._copy_transcript_sync, task_id, started_at, src)

    def _list_history_sync(self, task_id: str) -> list[TaskRun]:
        hdir = self._history_dir(task_id)
        if not hdir.exists():
            return []
        runs: list[TaskRun] = []
        for path in sorted(hdir.glob("*.json")):
            try:
                with path.open(encoding="utf-8") as f:
                    runs.append(TaskRun.model_validate(json.load(f)))
            except (OSError, ValueError):
                log.warning("task store: skipping invalid history record %s", path)
        return runs

    async def list_history(self, task_id: str) -> list[TaskRun]:
        """Return a task's run records oldest-first; invalid records are skipped."""
        return await asyncio.to_thread(self._list_history_sync, task_id)

    def _remove_history(self, task_id: str) -> None:
        """Delete a task's entire history directory (no-op if absent/unsafe)."""
        try:
            hdir = self._history_dir(task_id)
        except ValueError:
            return
        if not hdir.exists():
            return
        for path in hdir.glob("*.json"):
            path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            hdir.rmdir()
