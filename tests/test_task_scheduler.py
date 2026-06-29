"""Unit tests for the task scheduler: dispatch, completion, revoke, dedup."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import BotConfig
from src.infra.healthcheck import check
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
    tmp_path: Path,
    runner: _RecordingRunner,
    cfg: BotConfig,
    **extra: Any,
) -> tuple[TaskScheduler, TaskStore]:
    """Build a TaskScheduler with a fresh store and a fixed clock.

    Extra keyword args (e.g. ``heartbeat_path``, ``on_loop_death``) pass through
    to the scheduler constructor.
    """
    store = TaskStore(tmp_path)
    sched = TaskScheduler(
        store=store,
        runner=runner,  # type: ignore[arg-type]
        cfg=cfg,
        glog=logging.getLogger("test"),
        is_allowed=cfg_is_allowed(cfg),
        tick_interval=60,
        now_fn=lambda: NOW,
        **extra,
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
    # Await the spawned run-tracked tasks directly: they now hop through
    # asyncio.to_thread for store I/O, so a fixed number of sleep(0) yields is
    # no longer enough to guarantee completion.
    while sched._inflight:
        await asyncio.gather(*list(sched._inflight), return_exceptions=True)
        await asyncio.sleep(0)


async def test_once_task_completes_and_persists(tmp_path: Path) -> None:
    """A one-shot task runs, completes, and is disabled on disk."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    t = _once_task()
    await store.add(t)

    await _drain(sched)

    assert runner.ran == [t.id]
    stored = await store.get(t.id)
    assert stored is not None
    assert stored.state == "completed"
    assert stored.enabled is False
    assert stored.next_run_at is None


async def test_running_ids_tracked_during_run(tmp_path: Path) -> None:
    """The shared running_ids set holds a task's id mid-run, then clears it."""
    shared: set[str] = set()
    seen_mid: list[bool] = []

    class _ObservingRunner:
        """Runner that records whether its task was marked running mid-run."""

        async def run(self, task: Task) -> object:
            """Observe the shared set during the run and return success."""
            seen_mid.append(task.id in shared)

            class _Outcome:
                status = "ok"
                error = None

            return _Outcome()

    sched, store = _sched(
        tmp_path, _ObservingRunner(), _cfg(), running_ids=shared  # type: ignore[arg-type]
    )
    t = _once_task()
    await store.add(t)

    await _drain(sched)

    assert seen_mid == [True]  # id present in the shared set while running
    assert shared == set()  # discarded once the run finished


async def test_interval_task_reschedules(tmp_path: Path) -> None:
    """An interval task runs once and reschedules its next run."""
    runner = _RecordingRunner()
    sched, store = _sched(tmp_path, runner, _cfg())
    t = _interval_task()
    await store.add(t)

    await _drain(sched)

    stored = await store.get(t.id)
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
    stored = await store.get(t.id)
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

    stored = await store.get(t.id)
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
    stored = await store.get(t.id)
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
    stored = await store.get(t.id)
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


# --- heartbeat + loop-death watchdog -----------------------------------------


async def test_heartbeat_written_and_read_as_fresh(tmp_path: Path) -> None:
    """The scheduler writes an ISO timestamp the healthcheck reads as ``ok``."""
    hb = tmp_path / "scheduler_heartbeat"
    sched, _ = _sched(tmp_path, _RecordingRunner(), _cfg(), heartbeat_path=hb)

    await sched._write_heartbeat()

    assert hb.read_text() == NOW.isoformat()
    status, age = check(hb, max_age=120.0, now=NOW)
    assert status == "ok"
    assert age == 0.0


async def test_heartbeat_noop_without_path(tmp_path: Path) -> None:
    """With no heartbeat path the write is a silent no-op (no file)."""
    sched, _ = _sched(tmp_path, _RecordingRunner(), _cfg())
    await sched._write_heartbeat()
    assert not (tmp_path / "scheduler_heartbeat").exists()


async def test_loop_death_fires_alert(tmp_path: Path) -> None:
    """An unexpected loop exit awaits the death notifier with the cause."""
    deaths: list[BaseException] = []

    async def on_death(exc: BaseException) -> None:
        deaths.append(exc)

    sched, _ = _sched(tmp_path, _RecordingRunner(), _cfg(), on_loop_death=on_death)

    class _Boom(BaseException):
        """Not an ``Exception``, so the loop's ``except Exception`` can't eat it."""

    async def _boom() -> None:
        raise _Boom("dead")

    sched.tick = _boom  # type: ignore[method-assign]
    sched.start()
    for _ in range(20):
        if deaths:
            break
        await asyncio.sleep(0)

    assert sched._loop_task is not None and sched._loop_task.done()
    assert len(deaths) == 1
    assert isinstance(deaths[0], _Boom)


async def test_stop_does_not_alert(tmp_path: Path) -> None:
    """Stopping the scheduler is not mistaken for a loop death."""
    deaths: list[BaseException] = []

    async def on_death(exc: BaseException) -> None:
        deaths.append(exc)

    sched, _ = _sched(tmp_path, _RecordingRunner(), _cfg(), on_loop_death=on_death)
    sched.start()
    await asyncio.sleep(0)
    await sched.stop()
    for _ in range(5):
        await asyncio.sleep(0)

    assert deaths == []
