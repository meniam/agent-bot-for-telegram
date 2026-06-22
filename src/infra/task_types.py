"""Scheduled-task domain models and schedule math.

`Task` is the persisted unit of scheduled work. A task is either one-shot
(`schedule.kind == "once"`) or recurring (`interval` / `cron`), and either an
LLM turn (`kind == "llm"`) or a script run (`kind == "script"`).

The schedule helpers (`parse_schedule`, `compute_next_run`, plus the grace
helpers used on restart) are ports of Hermes' `cron/jobs.py` logic, adapted to
seconds-based intervals and timezone-aware datetimes. They are pure functions so
they can be unit-tested without a store or scheduler.
"""

import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskKind = Literal["llm", "script"]
TaskScope = Literal["user", "global"]
TaskState = Literal["scheduled", "paused", "completed", "error"]
ScheduleKind = Literal["once", "interval", "cron"]
RunStatus = Literal["ok", "error"]

# One-shot tasks may fire this many seconds late and still count as a catch-up
# rather than a stale miss (mirrors Hermes ONESHOT_GRACE_SECONDS).
ONESHOT_GRACE_SECONDS = 120
# Recurring catch-up grace is half the period, clamped to this range.
MIN_GRACE_SECONDS = 120
MAX_GRACE_SECONDS = 7200  # 2 hours


def _now() -> datetime:
    """Timezone-aware 'now' in the system local timezone."""
    return datetime.now().astimezone()


def _ensure_aware(dt: datetime) -> datetime:
    """Interpret naive datetimes as local wall time; pass aware ones through."""
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt


class TaskSchedule(BaseModel):
    """Polymorphic schedule discriminated by ``kind``."""

    model_config = ConfigDict(frozen=True)

    kind: ScheduleKind
    # once: absolute fire time.
    run_at: datetime | None = None
    # interval: spacing between runs, in seconds.
    interval_sec: int | None = None
    # cron: a 5-field cron expression.
    expr: str | None = None
    # Human-readable summary for `/task list`.
    display: str = ""


class TaskRepeat(BaseModel):
    """How many times a recurring task should run; ``times=None`` is forever."""

    times: int | None = None
    completed: int = 0


class Task(BaseModel):
    """One scheduled unit of work, persisted as JSON."""

    model_config = ConfigDict(extra="ignore")

    id: str
    owner_chat_id: int
    scope: TaskScope = "user"
    name: str = ""
    enabled: bool = True
    state: TaskState = "scheduled"
    kind: TaskKind
    schedule: TaskSchedule
    # LLM tasks carry a prompt; script tasks carry a script path.
    prompt: str | None = None
    script: str | None = None
    # True when the task mutates working_dir and must run under the workdir lock.
    # Always effectively true for kind="llm".
    exclusive: bool = False
    repeat: TaskRepeat = Field(default_factory=TaskRepeat)
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: RunStatus | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=_now)

    @property
    def needs_lock(self) -> bool:
        """Whether this task must serialize on the per-bot workdir lock."""
        return self.kind == "llm" or self.exclusive


class TaskRun(BaseModel):
    """One append-only history record for a single execution."""

    task_id: str
    scope: TaskScope
    kind: TaskKind
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    status: RunStatus
    exit_code: int | None = None
    output: str = ""
    error: str | None = None
    delivered_to: list[int] = Field(default_factory=list)


_DURATION_RE = re.compile(r"^(\d+)\s*(m|min|mins|minute|minutes|h|hour|hours|d|day|days)$")
_CRON_FIELD_RE = re.compile(r"^[\d*\-,/]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def parse_duration_sec(text: str) -> int:
    """Parse '30m' / '2h' / '1d' into seconds. Raises ValueError otherwise."""
    m = _DURATION_RE.match(text.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid duration: '{text}'. Use a format like '30m', '2h', or '1d'."
        )
    value = int(m.group(1))
    unit = m.group(2)[0]  # m, h, or d
    multipliers = {"m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


def parse_schedule(text: str, *, now: datetime | None = None) -> TaskSchedule:
    """Parse a human schedule string into a `TaskSchedule`.

    Accepted forms:
      - "30m" / "2h" / "1d"          → one-shot, now + duration
      - "2026-02-03T14:00[:00]"      → one-shot at an ISO timestamp
      - "every 30m" / "every 1d"     → recurring interval
      - "0 9 * * *"                  → cron (requires croniter)
    """
    now = now or _now()
    raw = text.strip()
    if not raw:
        raise ValueError("Empty schedule.")
    lowered = raw.lower()

    if lowered.startswith("every "):
        interval_sec = parse_duration_sec(raw[6:].strip())
        return TaskSchedule(
            kind="interval",
            interval_sec=interval_sec,
            display=f"every {interval_sec // 60}m"
            if interval_sec % 60 == 0
            else f"every {interval_sec}s",
        )

    parts = raw.split()
    if len(parts) >= 5 and all(_CRON_FIELD_RE.match(p) for p in parts[:5]):
        expr = " ".join(parts[:5])
        _validate_cron(expr)
        return TaskSchedule(kind="cron", expr=expr, display=expr)

    if "T" in raw or _DATE_RE.match(raw):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"Invalid timestamp '{raw}': {e}") from e
        dt = _ensure_aware(dt)
        return TaskSchedule(
            kind="once",
            run_at=dt,
            display=f"once at {dt.strftime('%Y-%m-%d %H:%M')}",
        )

    try:
        interval_sec = parse_duration_sec(raw)
    except ValueError:
        raise ValueError(
            f"Invalid schedule '{raw}'. Use a duration ('30m', '2h', '1d'), "
            "an interval ('every 30m'), a cron expr ('0 9 * * *'), or an ISO "
            "timestamp ('2026-02-03T14:00')."
        ) from None
    run_at = now + timedelta(seconds=interval_sec)
    return TaskSchedule(kind="once", run_at=run_at, display=f"once in {raw}")


def _validate_cron(expr: str) -> None:
    try:
        from croniter import croniter
    except ImportError as e:  # pragma: no cover - croniter is a hard dependency
        raise ValueError(
            "Cron schedules require the 'croniter' package."
        ) from e
    if not croniter.is_valid(expr):
        raise ValueError(f"Invalid cron expression: '{expr}'.")


def compute_next_run(
    schedule: TaskSchedule,
    *,
    last_run: datetime | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """Next fire time for a schedule, or None if there are no more runs.

    For recurring schedules the base is ``last_run`` when available (so a
    restart anchors to the real last execution), else ``now``. A one-shot that
    already ran (``last_run`` set) returns None.
    """
    now = now or _now()

    if schedule.kind == "once":
        if last_run is not None:
            return None
        return _ensure_aware(schedule.run_at) if schedule.run_at else None

    if schedule.kind == "interval":
        if not schedule.interval_sec:
            return None
        base = _ensure_aware(last_run) if last_run else now
        return base + timedelta(seconds=schedule.interval_sec)

    if schedule.kind == "cron":
        from croniter import croniter

        base = _ensure_aware(last_run) if last_run else now
        nxt: datetime = croniter(schedule.expr, base).get_next(datetime)
        return nxt

    return None


def compute_grace_seconds(schedule: TaskSchedule, *, now: datetime | None = None) -> int:
    """How late a recurring run may be and still catch up (else fast-forward).

    Half the schedule period, clamped to [MIN_GRACE, MAX_GRACE].
    """
    if schedule.kind == "interval" and schedule.interval_sec:
        return max(MIN_GRACE_SECONDS, min(schedule.interval_sec // 2, MAX_GRACE_SECONDS))

    if schedule.kind == "cron" and schedule.expr:
        try:
            from croniter import croniter

            base = now or _now()
            it = croniter(schedule.expr, base)
            first = it.get_next(datetime)
            second = it.get_next(datetime)
            period = int((second - first).total_seconds())
            return max(MIN_GRACE_SECONDS, min(period // 2, MAX_GRACE_SECONDS))
        except Exception:  # fall back to the floor on any cron error
            return MIN_GRACE_SECONDS

    return MIN_GRACE_SECONDS
