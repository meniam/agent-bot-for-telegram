"""Unit tests for the task scheduler: dispatch, completion, revoke, dedup."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.config import BotConfig
from src.infra.task_scheduler import TaskScheduler
from src.infra.task_store import TaskStore, new_task_id
from src.infra.task_types import Task, TaskRepeat, TaskSchedule

NOW = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)


class _RecordingRunner:
    """Stand-in for TaskRunner that records which tasks it executed."""

    def __init__(self) -> None:
        """Initialize an empty record of executed task ids."""
        self.ran: list[str] = []

    async def run(self, task: Task) -> object:
        """Record the task id and return a stub success outcome."""
        self.ran.append(task.id)

        class _Outcome:
            """Minimal stand-in for a task run outcome."""

            status = "ok"
            error = None

        return _Outcome()


def _cfg(**over: object) -> BotConfig:
    """Build a minimal BotConfig with the given overrides."""
    base: dict[str, object] = {
        "name": "t",
        "telegram_bot_token": "1:abc",
        "allowed_chat_ids": (10,),
        "admin_chat_ids": (99,),
    }
    base.update(over)
    return BotConfig.model_validate(base)


def _sched(
    tmp_path: Path, runner: _RecordingRunner, cfg: BotConfig
) -> tuple[TaskScheduler, TaskStore]:
    """Build a TaskScheduler with a fresh store and a fixed clock."""
    store = TaskStore(tmp_path)
    sched = TaskScheduler(
        store=store,
        runner=runner,  # type: ignore[arg-type]
        cfg=cfg,
        glog=logging.getLogger("test"),
        is_allowed=cfg_is_allowed(cfg),
        tick_interval=60,
        now_fn=lambda: NOW,
    )
    return sched, store


def cfg_is_allowed(cfg: BotConfig) -> Callable[[int], bool]:
    """Build an ACL predicate from the config's allowed chat ids."""
    allowed = set(cfg.allowed_chat_ids)

    def _is_allowed(chat_id: int) -> bool:
        """Return whether the chat id is in the allowed set."""
        return chat_id in allowed

    return _is_allowed


def _interval_task(chat_id: int = 10) -> Task:
    """Build an overdue 30-minute interval task for the given chat."""
    return Task(
        id=new_task_id(),
        owner_chat_id=chat_id,
        kind="llm",
        prompt="x",
        schedule=TaskSchedule(kind="interval", interval_sec=1800),
        next_run_at=NOW - timedelta(seconds=1),
    )


def _once_task(chat_id: int = 10) -> Task:
    """Build an overdue one-shot task for the given chat."""
    return Task(
        id=new_task_id(),
        owner_chat_id=chat_id,
        kind="llm",
        prompt="x",
        schedule=TaskSchedule(kind="once", run_at=NOW - timedelta(seconds=1)),
        next_run_at=NOW - timedelta(seconds=1),
    )


async def _drain(sched: TaskScheduler) -> None:
    """Run one tick and let the fire-and-forget run-tracked tasks finish."""
    await sched.tick()
    # Yield enough for the spawned tasks to complete.
    for _ in range(5):
        await asyncio.sleep(0)


async def test_once_task_completes_and_persists(tmp_path: Path) -> None:
    """A one-shot task runs, completes, and is disabled on disk."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    t = _once_task()
    await store.add(t)

    await _drain(sched)

    assert runner.ran == [t.id]
    stored = store.get(t.id)
    assert stored is not None
    assert stored.state == "completed"
    assert stored.enabled is False
    assert stored.next_run_at is None


async def test_interval_task_reschedules(tmp_path: Path) -> None:
    """An interval task runs once and reschedules its next run."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    t = _interval_task()
    await store.add(t)

    await _drain(sched)

    stored = store.get(t.id)
    assert stored is not None
    assert stored.state == "scheduled"
    assert stored.repeat.completed == 1
    assert stored.next_run_at == NOW + timedelta(minutes=30)


async def test_revoked_owner_pauses_task(tmp_path: Path) -> None:
    """A task whose owner lost access is paused without running."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    t = _interval_task(chat_id=777)  # not in allowed_chat_ids
    await store.add(t)

    await _drain(sched)

    assert runner.ran == []  # never executed
    stored = store.get(t.id)
    assert stored is not None
    assert stored.enabled is False
    assert stored.state == "paused"
    assert stored.last_error == "access_revoked"


async def test_no_double_fire_within_tick(tmp_path: Path) -> None:
    """Back-to-back ticks do not re-fire an already-advanced task."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    t = _interval_task()
    await store.add(t)

    # Two ticks back to back: the second must not re-pick the already-advanced
    # task (its next_run_at moved into the future after the first tick).
    await _drain(sched)
    await _drain(sched)

    assert runner.ran == [t.id]


async def test_repeat_limit_completes(tmp_path: Path) -> None:
    """A task with a repeat limit completes once the limit is reached."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    t = _interval_task()
    t = t.model_copy(update={"repeat": TaskRepeat(times=1)})
    await store.add(t)

    await _drain(sched)

    stored = store.get(t.id)
    assert stored is not None
    assert stored.state == "completed"
    assert stored.enabled is False


async def test_stale_oneshot_completes_without_running(tmp_path: Path) -> None:
    """A one-shot past its grace window completes without running."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    # run_at long past the 120s one-shot grace window.
    t = Task(
        id=new_task_id(),
        owner_chat_id=10,
        kind="llm",
        prompt="x",
        schedule=TaskSchedule(kind="once", run_at=NOW - timedelta(hours=5)),
        next_run_at=NOW - timedelta(hours=5),
    )
    await store.add(t)

    await _drain(sched)

    assert runner.ran == []  # never executed
    stored = store.get(t.id)
    assert stored is not None
    assert stored.state == "completed"


async def test_recent_oneshot_runs(tmp_path: Path) -> None:
    """A one-shot within its grace window still runs."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    t = Task(
        id=new_task_id(),
        owner_chat_id=10,
        kind="llm",
        prompt="x",
        schedule=TaskSchedule(kind="once", run_at=NOW - timedelta(seconds=30)),
        next_run_at=NOW - timedelta(seconds=30),
    )
    await store.add(t)

    await _drain(sched)

    assert runner.ran == [t.id]


async def test_stale_interval_fast_forwards_without_running(tmp_path: Path) -> None:
    """A long-overdue interval fast-forwards without catch-up runs."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    # "every 10m" task overdue by 1h → way past its 5m grace → skip + reschedule.
    t = Task(
        id=new_task_id(),
        owner_chat_id=10,
        kind="llm",
        prompt="x",
        schedule=TaskSchedule(kind="interval", interval_sec=600),
        next_run_at=NOW - timedelta(hours=1),
    )
    await store.add(t)

    await _drain(sched)

    assert runner.ran == []  # NOT 6 catch-up runs, and not even 1
    stored = store.get(t.id)
    assert stored is not None
    assert stored.next_run_at is not None
    assert stored.next_run_at > NOW


async def test_recent_interval_runs_once(tmp_path: Path) -> None:
    """A slightly overdue interval within grace runs once."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    # Overdue by 1 min, well within the 30m-task grace (15m) → one catch-up run.
    t = Task(
        id=new_task_id(),
        owner_chat_id=10,
        kind="llm",
        prompt="x",
        schedule=TaskSchedule(kind="interval", interval_sec=1800),
        next_run_at=NOW - timedelta(minutes=1),
    )
    await store.add(t)

    await _drain(sched)

    assert runner.ran == [t.id]
