"""Liveness check for the task scheduler heartbeat.

Reads the file `TaskScheduler` rewrites each tick and reports whether the last
beat is recent. Designed as a container ``HEALTHCHECK`` (and runnable by hand):

    python -m src.infra.healthcheck [PATH] [--max-age SECONDS]

Exit code is 0 when the beat is fresh (``ok``) and 1 otherwise (``stale`` or
``missing``), so Docker flips the container to *unhealthy* on a stalled or dead
scheduler loop.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = Path("/app/var/scheduler_heartbeat")
# Two tick intervals (default tick = 60s) — one missed beat is tolerated.
DEFAULT_MAX_AGE = 120.0


def check(
    path: Path, max_age: float, now: datetime | None = None
) -> tuple[str, float | None]:
    """Classify the heartbeat as ``ok`` / ``stale`` / ``missing``.

    Returns the status and the beat's age in seconds (``None`` when missing or
    unreadable). A future-dated beat (clock skew) reports a negative age but is
    still ``ok``.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "missing", None
    try:
        beat = datetime.fromisoformat(raw)
    except ValueError:
        return "missing", None
    now = now or datetime.now().astimezone()
    if beat.tzinfo is None:
        beat = beat.astimezone()
    age = (now - beat).total_seconds()
    return ("ok" if age <= max_age else "stale"), age


def main(argv: list[str] | None = None) -> int:
    """CLI entry: print the status and return 0 (ok) or 1 (stale/missing)."""
    parser = argparse.ArgumentParser(description="Task scheduler heartbeat check.")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE)
    args = parser.parse_args(argv)

    status, age = check(args.path, args.max_age)
    print(status if age is None else f"{status} (age={age:.0f}s)")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
