"""Unit tests for the scheduler heartbeat healthcheck."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.infra.healthcheck import check, main

NOW = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)


def test_missing_file(tmp_path: Path) -> None:
    """A nonexistent heartbeat reports missing with no age."""
    status, age = check(tmp_path / "nope", max_age=120.0, now=NOW)
    assert status == "missing"
    assert age is None


def test_unparseable_content_is_missing(tmp_path: Path) -> None:
    """Garbage content is treated as missing, not crashing."""
    hb = tmp_path / "hb"
    hb.write_text("not-a-timestamp")
    status, age = check(hb, max_age=120.0, now=NOW)
    assert status == "missing"
    assert age is None


def test_fresh_is_ok(tmp_path: Path) -> None:
    """A beat within max_age is ok with a small age."""
    hb = tmp_path / "hb"
    hb.write_text((NOW - timedelta(seconds=30)).isoformat())
    status, age = check(hb, max_age=120.0, now=NOW)
    assert status == "ok"
    assert age == 30.0


def test_stale_beyond_max_age(tmp_path: Path) -> None:
    """A beat older than max_age is stale."""
    hb = tmp_path / "hb"
    hb.write_text((NOW - timedelta(seconds=200)).isoformat())
    status, age = check(hb, max_age=120.0, now=NOW)
    assert status == "stale"
    assert age == 200.0


def test_boundary_is_ok(tmp_path: Path) -> None:
    """A beat exactly at max_age still counts as ok (inclusive)."""
    hb = tmp_path / "hb"
    hb.write_text((NOW - timedelta(seconds=120)).isoformat())
    status, _ = check(hb, max_age=120.0, now=NOW)
    assert status == "ok"


def test_naive_timestamp_handled(tmp_path: Path) -> None:
    """A naive ISO timestamp is localized rather than raising."""
    hb = tmp_path / "hb"
    hb.write_text("2026-06-22T09:00:00")  # no tzinfo
    status, age = check(hb, max_age=120.0, now=NOW)
    assert status in {"ok", "stale"}
    assert age is not None


def test_main_exit_codes(tmp_path: Path) -> None:
    """main() returns 0 for a fresh beat and 1 for a missing one."""
    hb = tmp_path / "hb"
    hb.write_text(datetime.now().astimezone().isoformat())
    assert main([str(hb)]) == 0
    assert main([str(tmp_path / "absent")]) == 1
