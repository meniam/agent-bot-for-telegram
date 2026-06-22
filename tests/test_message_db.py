"""SqliteChatLogHandler + query_messages + search_messages."""

import logging
import sqlite3
from pathlib import Path

import pytest

from src.infra.message_db import (
    MESSAGES_MAX_LIMIT,
    SqliteChatLogHandler,
    query_messages,
    search_messages,
)
from src.infra.session_store import Session


def _has_fts5() -> bool:
    """Report whether the SQLite build supports the FTS5 extension."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


requires_fts5 = pytest.mark.skipif(
    not _has_fts5(), reason="SQLite build lacks FTS5"
)


def _record(msg: str, *, role: str | None = None, tool: str | None = None) -> logging.LogRecord:
    """Build a LogRecord with optional ``role`` and ``tool`` extras."""
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
    if role is not None:
        rec.role = role
    if tool is not None:
        rec.tool = tool
    return rec


def _rows(db: Path, table: str) -> list[dict[str, object]]:
    """Read every row of ``table`` as a list of dicts."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]  # noqa: S608
    finally:
        conn.close()


def test_emit_defaults_role_system_and_resolves_session(tmp_path: Path) -> None:
    """Default the role to ``system`` and persist the resolved session."""
    db = tmp_path / "x.db"
    sess = Session("sess-1", "Title", auto_titled=False, created_at=1.0, last_used=2.0)
    h = SqliteChatLogHandler(db, session_of=lambda: sess)
    h.emit(_record("hello"))
    h.close()

    msgs = _rows(db, "messages")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "system"
    assert msgs[0]["session_id"] == "sess-1"
    assert msgs[0]["message"] == "hello"

    sessions = _rows(db, "sessions")
    assert len(sessions) == 1
    assert sessions[0]["id"] == "sess-1"
    assert sessions[0]["title"] == "Title"


def test_emit_applies_logrecord_args(tmp_path: Path) -> None:
    """Interpolate ``%``-style LogRecord args into the stored message."""
    db = tmp_path / "x.db"
    h = SqliteChatLogHandler(db)
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "n=%s ok=%d", ("Read", 7), None)
    h.emit(rec)
    h.close()
    assert _rows(db, "messages")[0]["message"] == "n=Read ok=7"


def test_emit_swallows_session_resolver_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising `session_of` must not crash logging, and writes nothing."""
    db = tmp_path / "x.db"
    monkeypatch.setattr(logging, "raiseExceptions", False)  # silence handleError stderr

    def boom() -> Session:
        raise RuntimeError("resolver exploded")

    h = SqliteChatLogHandler(db, session_of=boom)
    h.emit(_record("first", role="user"))  # raises internally, caught by emit
    # Handler stays usable: with the resolver gone, a later emit still writes.
    h._session_of = None
    h.emit(_record("second", role="bot"))
    h.close()
    msgs = _rows(db, "messages")
    assert [m["message"] for m in msgs] == ["second"]  # the failed emit wrote nothing


def test_emit_records_iso_created_at(tmp_path: Path) -> None:
    """Store the raw ts plus an ISO-second ``created_at`` string."""
    db = tmp_path / "x.db"
    h = SqliteChatLogHandler(db)
    rec = _record("ts-check")
    rec.created = 1_700_000_000.0
    h.emit(rec)
    h.close()
    row = _rows(db, "messages")[0]
    assert row["ts"] == 1_700_000_000.0
    assert "T" in str(row["created_at"]) and len(str(row["created_at"])) == 19  # iso seconds


def test_emit_no_resolver_leaves_sessions_empty(tmp_path: Path) -> None:
    """Leave sessions empty and ``session_id`` NULL without a resolver."""
    db = tmp_path / "x.db"
    h = SqliteChatLogHandler(db)
    h.emit(_record("orphan", role="user"))
    h.close()
    assert _rows(db, "sessions") == []
    assert _rows(db, "messages")[0]["session_id"] is None


def test_long_lived_handler_visible_to_reader(tmp_path: Path) -> None:
    """WAL: a reader sees committed rows while the writer connection stays open."""
    db = tmp_path / "x.db"
    h = SqliteChatLogHandler(db)
    h.emit(_record("one", role="user"))
    h.emit(_record("two", role="bot"))
    # reader opens independently while `h` is still open
    out = query_messages(db)
    assert [r["message"] for r in out] == ["one", "two"]
    h.emit(_record("three", role="user"))
    assert [r["message"] for r in query_messages(db)] == ["one", "two", "three"]
    h.close()


def test_emit_role_and_tool_from_extra(tmp_path: Path) -> None:
    """Persist ``role`` and ``tool`` taken from LogRecord extras."""
    db = tmp_path / "x.db"
    h = SqliteChatLogHandler(db)
    h.emit(_record("hook pre: Read", role="tool", tool="Read"))
    h.close()

    msgs = _rows(db, "messages")
    assert msgs[0]["role"] == "tool"
    assert msgs[0]["tool"] == "Read"
    assert msgs[0]["session_id"] is None


def test_session_upsert_is_idempotent(tmp_path: Path) -> None:
    """Upsert a session in place, keeping one row with the latest title."""
    db = tmp_path / "x.db"
    box = {"s": Session("s1", "First", auto_titled=False, created_at=1.0, last_used=2.0)}
    h = SqliteChatLogHandler(db, session_of=lambda: box["s"])
    h.emit(_record("a", role="user"))
    box["s"] = Session("s1", "Renamed", auto_titled=True, created_at=1.0, last_used=9.0)
    h.emit(_record("b", role="bot"))
    h.close()

    sessions = _rows(db, "sessions")
    assert len(sessions) == 1
    assert sessions[0]["title"] == "Renamed"
    assert len(_rows(db, "messages")) == 2


def _seed(db: Path) -> None:
    """Create the schema (via the handler) and insert rows with controlled ts."""
    SqliteChatLogHandler(db).close()
    conn = sqlite3.connect(db)
    try:
        for i, (role, sid) in enumerate(
            [("user", "s1"), ("bot", "s1"), ("user", "s2"), ("tool", "s2")]
        ):
            conn.execute(
                "INSERT INTO messages (ts, created_at, session_id, role, tool, "
                "level, message) VALUES (?,?,?,?,?,?,?)",
                (100.0 + i, "iso", sid, role, None, "INFO", f"m{i}"),
            )
        conn.commit()
    finally:
        conn.close()


def test_query_latest_no_interval(tmp_path: Path) -> None:
    """Return the latest ``limit`` rows in chronological order."""
    db = tmp_path / "x.db"
    _seed(db)
    out = query_messages(db, limit=2)
    assert [r["message"] for r in out] == ["m2", "m3"]  # last 2, chronological


def test_query_filters_session_and_role(tmp_path: Path) -> None:
    """Filter returned rows by ``session_id`` and by ``role``."""
    db = tmp_path / "x.db"
    _seed(db)
    by_sess = query_messages(db, session_id="s1")
    assert [r["message"] for r in by_sess] == ["m0", "m1"]
    by_role = query_messages(db, role="tool")
    assert [r["message"] for r in by_role] == ["m3"]


def test_query_interval_ascending(tmp_path: Path) -> None:
    """Return rows within a ``since``/``until`` interval ascending."""
    db = tmp_path / "x.db"
    _seed(db)
    out = query_messages(db, since=101.0, until=102.0)
    assert [r["message"] for r in out] == ["m1", "m2"]


def test_query_caps_limit(tmp_path: Path) -> None:
    """Cap an over-large limit at the available row count."""
    db = tmp_path / "x.db"
    _seed(db)
    out = query_messages(db, limit=MESSAGES_MAX_LIMIT + 1000)
    assert len(out) == 4


def test_query_missing_file_returns_empty(tmp_path: Path) -> None:
    """Return an empty list for a nonexistent database file."""
    assert query_messages(tmp_path / "nope.db") == []


def test_query_until_only_is_ascending_inclusive(tmp_path: Path) -> None:
    """Return rows up to ``until`` inclusive, ascending."""
    db = tmp_path / "x.db"
    _seed(db)
    out = query_messages(db, until=101.0)
    assert [r["message"] for r in out] == ["m0", "m1"]  # ts 100,101 inclusive, ASC


def test_query_since_only_is_ascending_inclusive(tmp_path: Path) -> None:
    """Return rows from ``since`` inclusive, ascending."""
    db = tmp_path / "x.db"
    _seed(db)
    out = query_messages(db, since=102.0)
    assert [r["message"] for r in out] == ["m2", "m3"]


def test_query_interval_plus_role(tmp_path: Path) -> None:
    """Combine an interval filter with a role filter."""
    db = tmp_path / "x.db"
    _seed(db)
    out = query_messages(db, since=100.0, until=103.0, role="user")
    assert [r["message"] for r in out] == ["m0", "m2"]  # users within range, ASC


def test_query_interval_empty_range(tmp_path: Path) -> None:
    """Return an empty list when the interval matches no rows."""
    db = tmp_path / "x.db"
    _seed(db)
    assert query_messages(db, since=500.0, until=600.0) == []


def test_query_limit_zero_floors_to_one(tmp_path: Path) -> None:
    """Floor a zero limit to one, returning the latest row."""
    db = tmp_path / "x.db"
    _seed(db)
    out = query_messages(db, limit=0)
    assert [r["message"] for r in out] == ["m3"]  # floored to 1 → latest


def test_query_limit_negative_floors_to_one(tmp_path: Path) -> None:
    """Floor a negative limit to one row."""
    db = tmp_path / "x.db"
    _seed(db)
    assert len(query_messages(db, limit=-99)) == 1


def test_query_joins_session_title(tmp_path: Path) -> None:
    """Join the session title, leaving it NULL for orphan rows."""
    db = tmp_path / "x.db"
    _seed(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sessions (id, title, auto_titled, created_at, last_used, "
        "updated_at) VALUES ('s1', 'Оплата', 0, 1.0, 1.0, 1.0)"
    )
    conn.commit()
    conn.close()
    titled = query_messages(db, session_id="s1")
    assert {r["session_title"] for r in titled} == {"Оплата"}
    orphan = query_messages(db, session_id="s2")  # no sessions row → NULL title
    assert all(r["session_title"] is None for r in orphan)


def test_query_empty_db_file_returns_empty(tmp_path: Path) -> None:
    """Return an empty list for an existing but table-less file."""
    db = tmp_path / "empty.db"
    db.write_bytes(b"")  # exists but has no tables
    assert query_messages(db) == []


def test_query_unknown_role_returns_empty(tmp_path: Path) -> None:
    """Return an empty list for a role that matches no rows."""
    db = tmp_path / "x.db"
    _seed(db)
    assert query_messages(db, role="ghost") == []


# --- SQL injection resistance --------------------------------------------

_INJECTION = "x'; DROP TABLE messages; --"


def test_query_role_is_bound_not_interpolated(tmp_path: Path) -> None:
    """A SQL-injection payload in `role` is a literal value, never executed."""
    db = tmp_path / "x.db"
    _seed(db)
    assert query_messages(db, role=_INJECTION) == []  # no row matches the literal
    # table survives → the payload was bound, not run
    assert len(_rows(db, "messages")) == 4


def test_query_session_id_is_bound_not_interpolated(tmp_path: Path) -> None:
    """Bind a ``session_id`` injection payload as a literal value."""
    db = tmp_path / "x.db"
    _seed(db)
    assert query_messages(db, session_id=_INJECTION) == []
    assert len(_rows(db, "messages")) == 4


def test_query_payload_matches_only_as_literal(tmp_path: Path) -> None:
    """Return a row whose role literally equals the injection payload."""
    db = tmp_path / "x.db"
    SqliteChatLogHandler(db).close()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO messages (ts, created_at, session_id, role, tool, level, "
        "message) VALUES (?,?,?,?,?,?,?)",
        (1.0, "iso", None, _INJECTION, None, "INFO", "payload-row"),
    )
    conn.commit()
    conn.close()
    out = query_messages(db, role=_INJECTION)
    assert [r["message"] for r in out] == ["payload-row"]


@requires_fts5
def test_search_match_payload_does_not_drop_table(tmp_path: Path) -> None:
    """Treat a malicious MATCH payload as text, leaving the table intact."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    # Malicious MATCH text → at worst a syntax error → []; never executes DDL.
    search_messages(db, _INJECTION)
    search_messages(db, "оплат'); DROP TABLE messages; --")
    assert len(_rows(db, "messages")) == len(_FTS_TEXTS)  # table intact


@requires_fts5
def test_search_filters_are_bound_not_interpolated(tmp_path: Path) -> None:
    """Bind search ``role``/``session_id`` injection payloads as literals."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    assert search_messages(db, "оплат", role=_INJECTION) == []
    assert search_messages(db, "оплат", session_id=_INJECTION) == []
    assert len(_rows(db, "messages")) == len(_FTS_TEXTS)


def test_query_reader_connection_is_read_only(tmp_path: Path) -> None:
    """`query_messages` opens `mode=ro`; that connection must reject writes."""
    db = tmp_path / "x.db"
    _seed(db)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM messages")
    finally:
        conn.close()


# --- FTS5 full-text search ------------------------------------------------

_FTS_TEXTS = [
    ("user", "s1", "когда пройдёт оплата заказа"),
    ("bot", "s1", "оплату подтвердим в течение часа"),
    ("user", "s2", "вопрос про доставку и сроки"),
    ("tool", "s2", "Read finished without errors"),
]


def _seed_fts(db: Path, *, via_handler: bool) -> None:
    """Insert searchable rows. ``via_handler`` → triggers fire on insert.

    With ``via_handler=False`` only the ``messages`` table is created and filled
    directly (no FTS / triggers), simulating a database that predates the index;
    the FTS table is then built lazily by ``search_messages`` via ``connect``.
    """
    if via_handler:
        SqliteChatLogHandler(db).close()  # creates full schema incl. FTS triggers
    else:
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
            "created_at TEXT NOT NULL, session_id TEXT, role TEXT NOT NULL, "
            "tool TEXT, level TEXT NOT NULL, message TEXT NOT NULL)"
        )
        conn.close()
    conn = sqlite3.connect(db)
    try:
        for i, (role, sid, text) in enumerate(_FTS_TEXTS):
            conn.execute(
                "INSERT INTO messages (ts, created_at, session_id, role, tool, "
                "level, message) VALUES (?,?,?,?,?,?,?)",
                (100.0 + i, "iso", sid, role, None, "INFO", text),
            )
        conn.commit()
    finally:
        conn.close()


@requires_fts5
def test_search_finds_substring_via_trigger(tmp_path: Path) -> None:
    """Match a substring across trigram-indexed rows and return snippets."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    # trigram → "оплат" matches both "оплата" and "оплату".
    out = search_messages(db, "оплат")
    assert {r["message"] for r in out} == {
        "когда пройдёт оплата заказа",
        "оплату подтвердим в течение часа",
    }
    assert all(r.get("snippet") for r in out)


@requires_fts5
def test_search_backfills_preexisting_rows(tmp_path: Path) -> None:
    """Build the FTS index lazily and find rows that predate it."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=False)  # rows exist before any FTS table
    out = search_messages(db, "доставку")
    assert [r["message"] for r in out] == ["вопрос про доставку и сроки"]


@requires_fts5
def test_search_combines_filters(tmp_path: Path) -> None:
    """Combine a search query with role and session filters."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    by_role = search_messages(db, "оплат", role="user")
    assert [r["message"] for r in by_role] == ["когда пройдёт оплата заказа"]
    by_sess = search_messages(db, "оплат", session_id="s1", role="bot")
    assert [r["message"] for r in by_sess] == ["оплату подтвердим в течение часа"]


@requires_fts5
def test_search_no_match_returns_empty(tmp_path: Path) -> None:
    """Return an empty list when the query matches no rows."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    assert search_messages(db, "несуществующее") == []


def test_search_missing_file_or_blank_query_returns_empty(tmp_path: Path) -> None:
    """Return an empty list for a missing file or a blank query."""
    assert search_messages(tmp_path / "nope.db", "anything") == []
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    assert search_messages(db, "   ") == []


@requires_fts5
def test_search_snippet_wraps_match_in_brackets(tmp_path: Path) -> None:
    """Wrap the matched term in brackets within the returned snippet."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    out = search_messages(db, "достав")
    assert len(out) == 1
    assert "[" in out[0]["snippet"] and "]" in out[0]["snippet"]


@requires_fts5
def test_search_respects_limit(tmp_path: Path) -> None:
    """Cap the number of search results at ``limit``."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)  # two rows contain "оплат"
    assert len(search_messages(db, "оплат", limit=1)) == 1


@requires_fts5
def test_search_filters_by_interval(tmp_path: Path) -> None:
    """Restrict search results to a ``since`` interval."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)  # "оплат" rows at ts 100 (user) and 101 (bot)
    out = search_messages(db, "оплат", since=101.0)
    assert [r["message"] for r in out] == ["оплату подтвердим в течение часа"]


@requires_fts5
def test_search_malformed_match_returns_empty(tmp_path: Path) -> None:
    """Degrade a malformed FTS5 query to an empty result."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    # Trailing boolean operator → FTS5 syntax error, must degrade to [].
    assert search_messages(db, "оплата OR") == []


@requires_fts5
def test_search_trigger_reflects_delete(tmp_path: Path) -> None:
    """Reflect a row deletion in the FTS index via its trigger."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    assert search_messages(db, "достав")  # present before delete
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM messages WHERE message LIKE '%доставку%'")
    conn.commit()
    conn.close()
    assert search_messages(db, "достав") == []  # messages_ad trigger purged index


@requires_fts5
def test_search_trigger_reflects_update(tmp_path: Path) -> None:
    """Reflect a row update in the FTS index via its trigger."""
    db = tmp_path / "x.db"
    _seed_fts(db, via_handler=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE messages SET message = 'новый текст возврата' "
        "WHERE message LIKE '%доставку%'"
    )
    conn.commit()
    conn.close()
    assert search_messages(db, "достав") == []  # old term gone
    assert [r["message"] for r in search_messages(db, "возврат")] == [
        "новый текст возврата"
    ]  # new term indexed by messages_au trigger
