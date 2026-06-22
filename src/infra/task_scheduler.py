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
from collections.abc import Callable
from datetime import datetime

from ..config import BotConfig, is_admin
from .task_runner import TaskRunner
from .task_store import TaskStore
from .task_types import (
    ONESHOT_GRACE_SECONDS,
    Task,
    compute_grace_seconds,
    compute_next_run,
)


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
    ) -> None:
        """Wire the scheduler to its store, runner, config, and access check."""
        self._store = store
        self._runner = runner
        self._cfg = cfg
        self._glog = glog
        self._is_allowed = is_allowed
        self._tick = max(1, tick_interval)
        self._now = now_fn or (lambda: datetime.now().astimezone())
        self._running: set[str] = set()
        self._inflight: set[asyncio.Task[None]] = set()
        self._loop_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background tick loop (idempotent)."""
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop())
            self._glog.info("[%s] task scheduler started", self._cfg.name)

    async def stop(self) -> None:
        """Cancel and await the tick loop (idempotent)."""
        if self._loop_task is None:
            return
        self._loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._loop_task
        self._loop_task = None

    async def _loop(self) -> None:
        """Tick forever, sleeping ``tick_interval`` between passes.

        A failed tick is logged and the loop continues; cancellation propagates.
        """
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._glog.exception("[%s] task scheduler tick failed", self._cfg.name)
            await asyncio.sleep(self._tick)

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
                await self._store.update(
                    task.model_copy(
                        update={
                            "state": "completed",
                            "enabled": False,
                            "next_run_at": None,
                        }
                    )
                )
                self._glog.info(
                    "[%s] task %s missed one-shot window — completed without run",
                    self._cfg.name,
                    task.id,
                )
                continue
            if decision == "skip":
                nxt = compute_next_run(task.schedule, last_run=now, now=now)
                await self._store.update(task.model_copy(update={"next_run_at": nxt}))
                self._glog.info(
                    "[%s] task %s missed its window — fast-forwarded to %s",
                    self._cfg.name,
                    task.id,
                    nxt,
                )
                continue
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
        self._glog.warning(
            "[%s] task %s owner %s lost access — pausing",
            self._cfg.name,
            task.id,
            task.owner_chat_id,
        )
        revoked = task.model_copy(
            update={"enabled": False, "state": "paused", "last_error": "access_revoked"}
        )
        await self._store.update(revoked)

    async def _run_tracked(self, task: Task, now: datetime) -> None:
        """Advance scheduling, run the task, persist its final state.

        Always clears the task from the in-flight set; advancing before the run
        prevents a slow run from double-firing.
        """
        try:
            # Advance scheduling BEFORE running so a slow run can't double-fire.
            pre = self._advance_before_run(task, now)
            await self._store.update(pre)

            outcome = await self._runner.run(pre)

            final = self._finalize_after_run(pre, outcome.status, outcome.error, now)
            await self._store.update(final)
        except Exception:
            self._glog.exception("[%s] task %s crashed", self._cfg.name, task.id)
        finally:
            self._running.discard(task.id)

    def _advance_before_run(self, task: Task, now: datetime) -> Task:
        """Move ``next_run_at`` forward (recurring) or clear it (one-shot)."""
        if task.schedule.kind == "once":
            return task.model_copy(update={"next_run_at": None})
        nxt = compute_next_run(task.schedule, last_run=now, now=now)
        return task.model_copy(update={"next_run_at": nxt})

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
