"""Unit tests for the scheduled-task store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.infra.task_store import TaskStore, new_task_id
from src.infra.task_types import Task, TaskRun, TaskSchedule

NOW = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)


def _task(
    *,
    chat_id: int,
    scope: str = "user",
    kind: str = "llm",
    next_run_at: datetime | None = None,
    enabled: bool = True,
) -> Task:
    """Build a task with the given owner, scope, and schedule."""
    return Task(
        id=new_task_id(),
        owner_chat_id=chat_id,
        scope=scope,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        prompt="x",
        enabled=enabled,
        schedule=TaskSchedule(kind="interval", interval_sec=1800),
        next_run_at=next_run_at,
    )


async def test_add_and_reload(tmp_path: Path) -> None:
    """An added task is readable from a fresh store reading the same dir."""
    store = TaskStore(tmp_path)
    t = await store.add(_task(chat_id=10, next_run_at=NOW))
    # Fresh store reads from disk, no in-memory cache.
    fresh = TaskStore(tmp_path)
    assert await fresh.get(t.id) is not None
    assert (await fresh.get(t.id)).owner_chat_id == 10  # type: ignore[union-attr]


async def test_list_due_spans_user_and_global(tmp_path: Path) -> None:
    """Listing due tasks spans users and global, excluding future and paused."""
    store = TaskStore(tmp_path)
    past = NOW - timedelta(minutes=1)
    future = NOW + timedelta(hours=1)
    await store.add(_task(chat_id=10, next_run_at=past))
    await store.add(_task(chat_id=20, next_run_at=past))
    await store.add(_task(chat_id=99, scope="global", next_run_at=past))
    await store.add(_task(chat_id=10, next_run_at=future))  # not yet due
    await store.add(_task(chat_id=10, next_run_at=past, enabled=False))  # paused

    due = await store.list_due(NOW)
    assert len(due) == 3  # two users + one global, future & paused excluded


async def test_list_all_isolates_users(tmp_path: Path) -> None:
    """Listing a user's tasks isolates them but can include global tasks."""
    store = TaskStore(tmp_path)
    await store.add(_task(chat_id=10, next_run_at=NOW))
    await store.add(_task(chat_id=20, next_run_at=NOW))
    await store.add(_task(chat_id=99, scope="global", next_run_at=NOW))

    own = await store.list_all(10)
    assert len(own) == 1 and own[0].owner_chat_id == 10

    with_global = await store.list_all(10, include_global=True)
    assert {t.scope for t in with_global} == {"user", "global"}
    assert len(with_global) == 2


async def test_remove(tmp_path: Path) -> None:
    """Removing a task succeeds once and then reports not found."""
    store = TaskStore(tmp_path)
    t = await store.add(_task(chat_id=10, next_run_at=NOW))
    assert await store.remove(t) is True
    assert await store.get(t.id) is None
    assert await store.remove(t) is False


async def test_corrupt_file_is_quarantined(tmp_path: Path) -> None:
    """A corrupt task file is quarantined and excluded from listings."""
    store = TaskStore(tmp_path)
    bad = tmp_path / "10.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    assert await store.list_all(10) == []
    assert not bad.exists()
    quarantined = list((tmp_path / "_corrupt").glob("10.*.json"))
    assert len(quarantined) == 1


async def test_history_append_and_prune(tmp_path: Path) -> None:
    """Appending history beyond the limit prunes the oldest runs."""
    store = TaskStore(tmp_path, history_limit=2)
    tid = new_task_id()
    for i in range(3):
        run = TaskRun(
            task_id=tid,
            scope="user",
            kind="llm",
            started_at=NOW + timedelta(seconds=i),
            finished_at=NOW + timedelta(seconds=i + 1),
            duration_ms=1000,
            status="ok",
            output=f"run {i}",
        )
        await store.append_history(run)
    runs = await store.list_history(tid)
    assert len(runs) == 2  # oldest pruned beyond limit
    assert runs[-1].output == "run 2"


async def test_unsafe_task_id_rejected(tmp_path: Path) -> None:
    """An unsafe task id is rejected on history lookup and on add."""
    store = TaskStore(tmp_path)
    with pytest.raises(ValueError):
        await store.list_history("../escape")
    with pytest.raises(ValueError):
        await store.add(_bad_id_task())


def _bad_id_task() -> Task:
    """Build a task with a path-traversal id for rejection tests."""
    return Task(
        id="../escape",
        owner_chat_id=1,
        kind="llm",
        prompt="x",
        schedule=TaskSchedule(kind="interval", interval_sec=60),
    )
