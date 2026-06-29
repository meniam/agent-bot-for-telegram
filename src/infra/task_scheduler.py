"""Background scheduler that fires due tasks.

A single asyncio loop ticks every ``tick_interval`` seconds. Each tick:

1. Loads due tasks (``next_run_at <= now``) from the store.
2. Skips tasks already running (in-flight dedup).
3. Drops tasks whose owner lost access (paused with ``access_revoked``).
4. Advances scheduling state *before* executing (so a slow run never
   double-fires) and persists it.
5. Fires the task as a tracked background coroutine via `TaskRunner`.

There is no backlog: a task carries a single ``next_run_at``. Catch-up / grace
handling on restart lives in `_pick_next_run` (Phase 6).
"""

import asyncio
import contextlib
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from ..config import BotConfig, is_admin
from .task_logging import task_log_context
from .task_runner import TaskRunner
from .task_store import TaskStore
from .task_types import (
    ONESHOT_GRACE_SECONDS,
    Task,
    TaskAuditEvent,
    compute_grace_seconds,
    compute_next_run,
)

# Module logger; the bot wires a file handler here (and on the runner + the
# ephemeral agent) so the full task lifecycle lands in <tasks_dir>/tasks.log.
log = logging.getLogger(__name__)


class TaskScheduler:
    """Background loop that fires due tasks via `TaskRunner`.

    Ticks every ``tick_interval`` seconds: loads due tasks, dedups in-flight
    runs, drops owners that lost access, applies catch-up grace, advances
    scheduling state before running, and fires each as a tracked coroutine.
    """

    def __init__(
        self,
        *,
        store: TaskStore,
        runner: TaskRunner,
        cfg: BotConfig,
        glog: logging.Logger,
        is_allowed: Callable[[int], bool],
        tick_interval: int = 60,
        now_fn: Callable[[], datetime] | None = None,
        heartbeat_path: Path | None = None,
        on_loop_death: Callable[[BaseException], Awaitable[None]] | None = None,
        running_ids: set[str] | None = None,
    ) -> None:
        """Wire the scheduler to its store, runner, config, and access check.

        ``heartbeat_path`` (optional) is rewritten after every loop pass for
        liveness checks. ``on_loop_death`` (optional) is awaited if the loop
        ends unexpectedly (crash or external cancel — not via ``stop()``).
        ``running_ids`` (optional) is a shared set the scheduler keeps in sync
        with the ids of tasks running right now, so the UI can show a live state.
        """
        self._store = store
        self._runner = runner
        self._cfg = cfg
        self._glog = glog
        self._is_allowed = is_allowed
        self._tick = max(1, tick_interval)
        self._now = now_fn or (lambda: datetime.now().astimezone())
        self._heartbeat_path = heartbeat_path
        self._on_loop_death = on_loop_death
        self._running: set[str] = running_ids if running_ids is not None else set()
        self._inflight: set[asyncio.Task[None]] = set()
        self._loop_task: asyncio.Task[None] | None = None
        # Set by stop() so the done-callback can tell an intentional shutdown
        # from an unexpected death.
        self._stopping = False

    def start(self) -> None:
        """Start the background tick loop (idempotent)."""
        if self._loop_task is None:
            self._stopping = False
            self._loop_task = asyncio.create_task(self._loop())
            self._loop_task.add_done_callback(self._on_loop_done)
            self._glog.info("[%s] task scheduler started", self._cfg.name)

    async def stop(self) -> None:
        """Cancel and await the tick loop (idempotent)."""
        if self._loop_task is None:
            return
        self._stopping = True
        self._loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._loop_task
        self._loop_task = None

    def _on_loop_done(self, task: asyncio.Task[None]) -> None:
        """Done-callback: alert if the loop ended without going through ``stop()``.

        The loop is infinite, so any completion that isn't our own cancellation
        is a fault — log ERROR and fire the optional async notifier.
        """
        if self._stopping:
            return
        if task.cancelled():
            exc: BaseException = asyncio.CancelledError("scheduler loop cancelled")
        else:
            caught = task.exception()
            if caught is None:
                return  # infinite loop returned cleanly — shouldn't happen, no-op
            exc = caught
        self._glog.error(
            "[%s] task scheduler loop died: %r", self._cfg.name, exc
        )
        if self._on_loop_death is not None:
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(self._notify_death(exc))

    async def _notify_death(self, exc: BaseException) -> None:
        """Await the death notifier, swallowing its failures."""
        notifier = self._on_loop_death
        if notifier is None:
            return
        try:
            await notifier(exc)
        except Exception:
            self._glog.exception(
                "[%s] scheduler loop-death notify failed", self._cfg.name
            )

    async def _loop(self) -> None:
        """Tick forever, sleeping ``tick_interval`` between passes.

        A failed tick is logged and the loop continues; cancellation propagates.
        The heartbeat is refreshed after every pass (success or caught error) so
        it proves the loop is cycling, not that any one tick succeeded.
        """
        await self._recover_interrupted_running()
        await self._write_heartbeat()  # mark alive before the first tick
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._glog.exception("[%s] task scheduler tick failed", self._cfg.name)
            await self._write_heartbeat()
            await asyncio.sleep(self._tick)

    async def _recover_interrupted_running(self) -> None:
        """Mark persisted running tasks from a previous process as interrupted.

        The in-memory ``_running`` set protects tasks this scheduler instance is
        actually executing. After a process restart that set is empty, so any
        persisted ``running`` state is stale and must become a durable error
        instead of a forever-running phantom.
        """
        now = self._now()
        for task in await self._store.list_running():
            if task.id in self._running:
                continue
            recurring = task.schedule.kind != "once"
            has_future_run = (
                task.next_run_at is not None and task.next_run_at > now
            )
            updates: dict[str, object] = {
                "state": "error",
                "last_status": "error",
                "last_error": "interrupted_by_restart",
            }
            if task.schedule.kind == "once":
                updates["enabled"] = False
                updates["next_run_at"] = None
            elif recurring:
                updates["enabled"] = has_future_run
            recovered = task.model_copy(update=updates)
            with task_log_context(self._cfg.name, task.id):
                log.warning(
                    "[%s] task %s recovered stale running state enabled=%s next_run_at=%s",
                    self._cfg.name,
                    task.id,
                    recovered.enabled,
                    recovered.next_run_at,
                )
            await self._store.update(recovered)

    async def _write_heartbeat(self) -> None:
        """Rewrite the heartbeat file with the current time (best-effort)."""
        if self._heartbeat_path is None:
            return
        stamp = self._now().isoformat()
        try:
            await asyncio.to_thread(self._write_heartbeat_sync, stamp)
        except Exception:
            # A heartbeat write must never take the loop down; staleness will
            # surface the problem via the healthcheck instead.
            self._glog.warning(
                "[%s] heartbeat write failed", self._cfg.name, exc_info=True
            )

    def _write_heartbeat_sync(self, stamp: str) -> None:
        """Atomically replace the heartbeat file (temp + rename, no fsync).

        Durability is irrelevant — only the latest value matters — so fsync is
        skipped, but the atomic rename still prevents readers seeing a partial
        write.
        """
        path = self._heartbeat_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".hb_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(stamp)
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def tick(self) -> None:
        """Run one scheduler pass (also callable directly from tests)."""
        now = self._now()
        for task in await self._store.list_due(now):
            if task.id in self._running:
                continue
            if not self._owner_allowed(task):
                await self._revoke(task)
                continue
            decision = self._grace_decision(task, now)
            if decision == "complete":
                with task_log_context(self._cfg.name, task.id):
                    log.info(
                        "[%s] task %s missed one-shot window — completed without run",
                        self._cfg.name,
                        task.id,
                    )
                await self._record_audit(
                    task,
                    "completed_stale",
                    now,
                    details={"reason": "missed_one_shot_window"},
                )
                await self._store.update(
                    task.model_copy(
                        update={
                            "state": "completed",
                            "enabled": False,
                            "next_run_at": None,
                        }
                    )
                )
                continue
            if decision == "skip":
                nxt = compute_next_run(task.schedule, last_run=now, now=now)
                with task_log_context(self._cfg.name, task.id):
                    log.info(
                        "[%s] task %s missed its window — fast-forwarded to %s",
                        self._cfg.name,
                        task.id,
                        nxt,
                    )
                await self._record_audit(
                    task,
                    "skipped_stale",
                    now,
                    details={"reason": "missed_recurring_window", "next_run_at": str(nxt)},
                )
                await self._store.update(task.model_copy(update={"next_run_at": nxt}))
                continue
            with task_log_context(self._cfg.name, task.id):
                log.info("[%s] task %s due — dispatching", self._cfg.name, task.id)
            self._running.add(task.id)
            job = asyncio.create_task(self._run_tracked(task, now))
            self._inflight.add(job)
            job.add_done_callback(self._inflight.discard)

    def _grace_decision(self, task: Task, now: datetime) -> str:
        """Decide whether a due task should run, be skipped, or be completed.

        Prevents a flood after downtime: a one-shot past its 120s window is
        completed without running; a recurring run later than its grace window
        (period/2, clamped) is fast-forwarded instead of fired. Otherwise the
        single catch-up run proceeds.
        """
        if task.next_run_at is None:
            return "skip"
        overdue = (now - task.next_run_at).total_seconds()
        if task.schedule.kind == "once":
            return "complete" if overdue > ONESHOT_GRACE_SECONDS else "run"
        grace = compute_grace_seconds(task.schedule, now=now)
        return "skip" if overdue > grace else "run"

    def _owner_allowed(self, task: Task) -> bool:
        """Whether the task's owner still has access (admin for global tasks)."""
        if task.scope == "global":
            return is_admin(self._cfg, task.owner_chat_id)
        return self._is_allowed(task.owner_chat_id)

    async def _revoke(self, task: Task) -> None:
        """Pause a task whose owner lost access, recording ``access_revoked``."""
        now = self._now()
        with task_log_context(self._cfg.name, task.id):
            self._glog.warning(
                "[%s] task %s owner %s lost access — pausing",
                self._cfg.name,
                task.id,
                task.owner_chat_id,
            )
        await self._record_audit(
            task,
            "access_revoked",
            now,
            details={"owner_chat_id": str(task.owner_chat_id)},
        )
        revoked = task.model_copy(
            update={"enabled": False, "state": "paused", "last_error": "access_revoked"}
        )
        await self._store.update(revoked)

    async def _record_audit(
        self,
        task: Task,
        event: str,
        occurred_at: datetime,
        *,
        details: dict[str, str] | None = None,
    ) -> None:
        """Persist a non-execution audit event, logging but not crashing on failure."""
        try:
            await self._store.append_audit_event(
                TaskAuditEvent(
                    task_id=task.id,
                    scope=task.scope,
                    kind=task.kind,
                    event=event,  # type: ignore[arg-type]
                    occurred_at=occurred_at,
                    scheduled_for=task.next_run_at,
                    details=details or {},
                )
            )
        except Exception:
            log.exception("[%s] task %s audit event write failed", self._cfg.name, task.id)

    async def _run_tracked(self, task: Task, now: datetime) -> None:
        """Advance scheduling, run the task, persist its final state.

        Always clears the task from the in-flight set; advancing before the run
        prevents a slow run from double-firing.
        """
        with task_log_context(self._cfg.name, task.id):
            await self._run_tracked_in_context(task, now)

    async def _run_tracked_in_context(self, task: Task, now: datetime) -> None:
        """Advance scheduling, run the task, and settle it inside task log context."""
        try:
            # Advance scheduling BEFORE running so a slow run can't double-fire.
            pre = self._advance_before_run(task, now)
            await self._store.update(pre)
            log.info(
                "[%s] task %s fired kind=%s next_run_at->%s",
                self._cfg.name,
                task.id,
                task.kind,
                pre.next_run_at,
            )

            outcome = await self._runner.run(
                pre,
                scheduled_for=task.next_run_at,
                dispatched_at=now,
            )

            final = self._finalize_after_run(pre, outcome.status, outcome.error, now)
            await self._store.update(final)
            log.info(
                "[%s] task %s settled state=%s last_status=%s",
                self._cfg.name,
                task.id,
                final.state,
                final.last_status,
            )
        except Exception:
            log.exception("[%s] task %s crashed", self._cfg.name, task.id)
            self._glog.exception("[%s] task %s crashed", self._cfg.name, task.id)
        finally:
            self._running.discard(task.id)

    def _advance_before_run(self, task: Task, now: datetime) -> Task:
        """Move ``next_run_at`` forward (recurring) or clear it (one-shot)."""
        if task.schedule.kind == "once":
            return task.model_copy(
                update={"state": "running", "next_run_at": None, "last_error": None}
            )
        nxt = compute_next_run(task.schedule, last_run=now, now=now)
        return task.model_copy(
            update={"state": "running", "next_run_at": nxt, "last_error": None}
        )

    def _finalize_after_run(
        self, task: Task, status: str, error: str | None, now: datetime
    ) -> Task:
        """Record run outcome and settle terminal state (completed / repeat)."""
        repeat = task.repeat.model_copy(
            update={"completed": task.repeat.completed + 1}
        )
        updates: dict[str, object] = {
            "last_run_at": now,
            "last_status": status,
            "last_error": error,
            "repeat": repeat,
        }
        done = task.schedule.kind == "once" or (
            repeat.times is not None and repeat.completed >= repeat.times
        )
        if done:
            updates["state"] = "completed"
            updates["enabled"] = False
            updates["next_run_at"] = None
        else:
            updates["state"] = "error" if status == "error" else "scheduled"
        return task.model_copy(update=updates)
