"""Unit tests for scheduled-task models and schedule math."""

from datetime import UTC, datetime, timedelta

import pytest

from src.infra.task_types import (
    Task,
    TaskRepeat,
    TaskSchedule,
    compute_grace_seconds,
    compute_next_run,
    parse_duration_sec,
    parse_schedule,
)

FIXED_NOW = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)


def test_parse_duration_units() -> None:
    """Duration strings parse to seconds; junk raises ValueError."""
    assert parse_duration_sec("30m") == 1800
    assert parse_duration_sec("2h") == 7200
    assert parse_duration_sec("1d") == 86400
    with pytest.raises(ValueError):
        parse_duration_sec("nonsense")


def test_parse_schedule_once_duration() -> None:
    """A duration string yields a one-shot schedule offset from now."""
    sch = parse_schedule("2h", now=FIXED_NOW)
    assert sch.kind == "once"
    assert sch.run_at == FIXED_NOW + timedelta(hours=2)


def test_parse_schedule_once_timestamp() -> None:
    """An ISO timestamp yields a one-shot schedule at that instant."""
    sch = parse_schedule("2026-07-01T14:00:00+00:00", now=FIXED_NOW)
    assert sch.kind == "once"
    assert sch.run_at == datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


def test_parse_schedule_interval() -> None:
    """An `every <duration>` string yields an interval schedule."""
    sch = parse_schedule("every 30m", now=FIXED_NOW)
    assert sch.kind == "interval"
    assert sch.interval_sec == 1800
    assert sch.display == "every 30m"


def test_parse_schedule_cron() -> None:
    """A cron expression yields a cron schedule preserving the expr."""
    sch = parse_schedule("0 9 * * *", now=FIXED_NOW)
    assert sch.kind == "cron"
    assert sch.expr == "0 9 * * *"


def test_parse_schedule_invalid() -> None:
    """An unrecognized schedule string raises ValueError."""
    with pytest.raises(ValueError):
        parse_schedule("not a schedule", now=FIXED_NOW)


def test_compute_next_run_once() -> None:
    """A one-shot run computes once, then yields None after it ran."""
    sch = parse_schedule("1h", now=FIXED_NOW)
    assert compute_next_run(sch, now=FIXED_NOW) == FIXED_NOW + timedelta(hours=1)
    # Already ran → no further runs.
    assert compute_next_run(sch, last_run=FIXED_NOW, now=FIXED_NOW) is None


def test_compute_next_run_interval_rolls_from_last_run() -> None:
    """An interval run rolls forward from last_run, or from now if absent."""
    sch = TaskSchedule(kind="interval", interval_sec=1800)
    last = FIXED_NOW
    assert compute_next_run(sch, last_run=last, now=FIXED_NOW + timedelta(hours=5)) == (
        last + timedelta(minutes=30)
    )
    # No last_run → from now.
    assert compute_next_run(sch, now=FIXED_NOW) == FIXED_NOW + timedelta(minutes=30)


def test_compute_next_run_cron() -> None:
    """A cron run computes the next future occurrence."""
    sch = parse_schedule("0 9 * * *", now=FIXED_NOW)
    nxt = compute_next_run(sch, now=FIXED_NOW)
    assert nxt is not None
    assert nxt > FIXED_NOW
    assert nxt.hour == 9


def test_compute_grace_clamps() -> None:
    """Grace seconds clamp between the floor and ceiling around half the period."""
    # Frequent interval clamps to the floor.
    assert compute_grace_seconds(TaskSchedule(kind="interval", interval_sec=60)) == 120
    # Half-period within range.
    assert compute_grace_seconds(TaskSchedule(kind="interval", interval_sec=3600)) == 1800
    # Huge period clamps to the ceiling.
    assert compute_grace_seconds(TaskSchedule(kind="interval", interval_sec=86400)) == 7200


def test_task_round_trip() -> None:
    """A Task survives a JSON dump/validate round trip; llm always locks."""
    task = Task(
        id="abc123def456",
        owner_chat_id=42,
        kind="llm",
        prompt="summarize my notes",
        schedule=parse_schedule("every 1d", now=FIXED_NOW),
        repeat=TaskRepeat(times=None),
    )
    dumped = task.model_dump(mode="json")
    restored = Task.model_validate(dumped)
    assert restored == task
    assert restored.needs_lock is True  # llm always locks


def test_script_task_not_exclusive_by_default() -> None:
    """A script task does not require the workdir lock by default."""
    task = Task(
        id="0123456789ab",
        owner_chat_id=1,
        kind="script",
        script="backup.sh",
        schedule=parse_schedule("every 1h", now=FIXED_NOW),
    )
    assert task.needs_lock is False
