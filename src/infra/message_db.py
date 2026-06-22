"""Per-chat SQLite message log: a structured mirror of the chat `.log`.

A `logging.Handler` (`SqliteChatLogHandler`) writes every per-chat event into
a SQLite file `<messages_dir>/<chat_id>.db` (defaults to `<logs_dir>/messages`).
Each row carries the author **role** (`user`/`bot`/`tool`/`system`) and the
**session** the event happened in. Roles come from `extra={"role": ...}` on
key call-sites; everything else defaults to `system`. The session is resolved
at emit time via an injected callable (see `BotLogs.set_session_resolver`).

Schema (one file per chat):
  sessions(id, title, auto_titled, created_at, last_used, updated_at)
  messages(id, ts, created_at, session_id, role, tool, level, message)
  chat_meta(id, current_session_id)

`SessionStore` (`session_store.py`) owns the `sessions` + `chat_meta` tables in
the *same* file: it is the authoritative source for the named-session list and
the chat's current pointer. The handler's per-emit session upsert is a
best-effort denormalized touch sourced from the same store, so the two never
diverge. The shared schema + connection helper live here (`SCHEMA`, `connect`).

`query_messages` is a read-only reader over the same file (no agent / Telegram
coupling) — a provider-agnostic building block for a future consumer.
"""

import datetime as _dt
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from .session_store import Session

ROLE_USER = "user"
ROLE_BOT = "bot"
ROLE_TOOL = "tool"
ROLE_SYSTEM = "system"

MESSAGES_DEFAULT_LIMIT = 50
MESSAGES_MAX_LIMIT = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    auto_titled INTEGER,
    created_at  REAL,
    last_used   REAL,
    updated_at  REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    created_at TEXT    NOT NULL,
    session_id TEXT,
    role       TEXT    NOT NULL,
    tool       TEXT,
    level      TEXT    NOT NULL,
    message    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_meta (
    id                 INTEGER PRIMARY KEY CHECK (id = 0),
    current_session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_ts         ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_role_ts    ON messages(role, ts);
CREATE INDEX IF NOT EXISTS idx_messages_session_ts ON messages(session_id, ts);
"""

# External-content FTS5 mirror of `messages.message`, kept in sync by triggers.
# `trigram` tokenizer → substring search (≥3 chars), case-insensitive, forgiving
# of Russian morphology. Created separately from `SCHEMA` because it is applied
# fail-safe (SQLite builds without FTS5 must not break log writes); see
# `_ensure_fts`.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message,
    content='messages',
    content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, message) VALUES (new.id, new.message);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, message)
    VALUES('delete', old.id, old.message);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, message)
    VALUES('delete', old.id, old.message);
    INSERT INTO messages_fts(rowid, message) VALUES (new.id, new.message);
END;
"""


def _ensure_fts(conn: sqlite3.Connection) -> None:
    """Create the FTS5 mirror + sync triggers; backfill pre-existing rows once.

    Fail-safe: a SQLite build without FTS5 raises `OperationalError`, which is
    swallowed so log writes keep working. The one-time backfill (`rebuild`)
    populates the index for databases created before FTS existed; on an empty
    or already-populated index it is a no-op.
    """
    try:
        # `count(*)` on an external-content FTS5 table reflects the content
        # table, not the index — so detect a first-time build by whether the
        # table existed before this call. Rebuild only then (and only if there
        # are rows to index); afterwards the triggers keep it in sync.
        existed = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()
        conn.executescript(FTS_SCHEMA)
        if not existed and conn.execute("SELECT 1 FROM messages LIMIT 1").fetchone():
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # FTS5 unavailable in this SQLite build — search degrades, writes don't.


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a per-chat SQLite file with WAL + the shared schema applied.

    Used by both `SqliteChatLogHandler` (long-lived) and `SessionStore`
    (short-lived per op). `CREATE TABLE IF NOT EXISTS` makes concurrent
    initialization from either side safe.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    _ensure_fts(conn)
    return conn

_UPSERT_SESSION = """
INSERT INTO sessions (id, title, auto_titled, created_at, last_used, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    title=excluded.title,
    auto_titled=excluded.auto_titled,
    last_used=excluded.last_used,
    updated_at=excluded.updated_at
"""

_INSERT_MESSAGE = """
INSERT INTO messages (ts, created_at, session_id, role, tool, level, message)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


class SqliteChatLogHandler(logging.Handler):
    """Writes per-chat log records into a SQLite file (one file per chat).

    Role is read from ``record.role`` (default ``system``); the tool name from
    ``record.tool``. The current session is resolved lazily via ``session_of``
    and denormalized into the ``sessions`` table on every write.
    """

    def __init__(
        self,
        db_path: Path,
        session_of: "Callable[[], Session | None] | None" = None,
    ) -> None:
        """Open ``db_path`` and resolve the current session via ``session_of``."""
        super().__init__()
        self._session_of = session_of
        self._conn = connect(db_path)

    def emit(self, record: logging.LogRecord) -> None:
        """Write one log record as a ``messages`` row, upserting its session.

        Reads ``role``/``tool`` off the record, resolves and denormalizes the
        current session, and commits. Errors route through ``handleError`` so a
        write failure never propagates into the logging call site.
        """
        try:
            role = getattr(record, "role", None) or ROLE_SYSTEM
            tool = getattr(record, "tool", None)
            session_id: str | None = None
            if self._session_of is not None:
                session = self._session_of()
                if session is not None:
                    session_id = session.id
                    self._conn.execute(
                        _UPSERT_SESSION,
                        (
                            session.id,
                            session.title,
                            int(session.auto_titled),
                            session.created_at,
                            session.last_used,
                            record.created,
                        ),
                    )
            created_at = _dt.datetime.fromtimestamp(record.created).isoformat(
                timespec="seconds"
            )
            self._conn.execute(
                _INSERT_MESSAGE,
                (
                    record.created,
                    created_at,
                    session_id,
                    role,
                    tool,
                    record.levelname,
                    record.getMessage(),
                ),
            )
            self._conn.commit()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """Close the SQLite connection, then the base handler."""
        try:
            self._conn.close()
        finally:
            super().close()


def _filters(
    session_id: str | None,
    role: str | None,
    since: float | None,
    until: float | None,
) -> tuple[str, list[Any]]:
    """Build the shared ``messages`` WHERE clause + bound params.

    Used by both ``query_messages`` and ``search_messages``. Predicates are
    fixed column comparisons; every user value is a bound parameter — no
    injection vector. The clause has no leading ``WHERE``/``AND`` so callers can
    splice it into either context.
    """
    where: list[str] = []
    params: list[Any] = []
    if session_id is not None:
        where.append("m.session_id = ?")
        params.append(session_id)
    if role is not None:
        where.append("m.role = ?")
        params.append(role)
    if since is not None:
        where.append("m.ts >= ?")
        params.append(since)
    if until is not None:
        where.append("m.ts <= ?")
        params.append(until)
    return " AND ".join(where), params


def query_messages(
    db_path: Path,
    *,
    session_id: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = MESSAGES_DEFAULT_LIMIT,
    role: str | None = None,
) -> list[dict[str, Any]]:
    """Read messages from a chat's ``messages.db`` (read-only).

    ``since``/``until`` are epoch seconds (compared to ``messages.ts``). With no
    interval, returns the latest ``limit`` messages in chronological order; with
    an interval, returns matching messages ascending. A missing file or table
    yields ``[]``. ``limit`` is capped at ``MESSAGES_MAX_LIMIT``.
    """
    if not db_path.exists():
        return []
    limit = max(1, min(limit, MESSAGES_MAX_LIMIT))

    where, params = _filters(session_id, role, since, until)
    clause = f"WHERE {where}" if where else ""

    # No interval → latest N (DESC), then reverse to chronological. With an
    # interval → ascending within the range.
    interval = since is not None or until is not None
    order = "ASC" if interval else "DESC"
    base_sql = (
        "SELECT m.ts, m.created_at, m.session_id, m.role, m.tool, m.level, "
        "m.message, s.title AS session_title "
        "FROM messages m LEFT JOIN sessions s ON s.id = m.session_id "
    )
    # `clause` is built from fixed column predicates and `order` is a literal;
    # all user-supplied values are bound parameters — no injection vector.
    sql = f"{base_sql} {clause} ORDER BY m.ts {order} LIMIT ?"
    params.append(limit)

    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []  # table not created yet
    finally:
        conn.close()

    result = [dict(r) for r in rows]
    if not interval:
        result.reverse()
    return result


def search_messages(
    db_path: Path,
    query: str,
    *,
    session_id: str | None = None,
    role: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = MESSAGES_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Full-text search over a chat's messages via the FTS5 ``trigram`` index.

    ``query`` is an FTS5 MATCH expression (a bare substring works with the
    trigram tokenizer). The same ``session_id``/``role``/``since``/``until``
    filters as ``query_messages`` narrow the result; rows come back ordered by
    relevance (``bm25``) with an extra ``snippet`` key. ``limit`` is capped at
    ``MESSAGES_MAX_LIMIT``.

    Opens the database read-write via ``connect`` so the FTS table is created
    and back-filled on demand (self-heal for databases predating the index).
    A missing file, missing FTS5 support, or malformed MATCH yields ``[]``.
    """
    if not db_path.exists() or not query.strip():
        return []
    limit = max(1, min(limit, MESSAGES_MAX_LIMIT))

    where, params = _filters(session_id, role, since, until)
    clause = f"AND {where}" if where else ""
    # `clause` is fixed column predicates; all user values are bound params.
    sql = (
        "SELECT m.ts, m.created_at, m.session_id, m.role, m.tool, m.level, "  # noqa: S608  # nosec B608
        "m.message, s.title AS session_title, "
        "snippet(messages_fts, 0, '[', ']', '…', 12) AS snippet "
        "FROM messages_fts f "
        "JOIN messages m ON m.id = f.rowid "
        "LEFT JOIN sessions s ON s.id = m.session_id "
        f"WHERE messages_fts MATCH ? {clause} "
        "ORDER BY bm25(messages_fts) LIMIT ?"
    )
    bound: list[Any] = [query, *params, limit]

    try:
        conn = connect(db_path)
    except sqlite3.OperationalError:
        return []
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, bound).fetchall()
        except sqlite3.OperationalError:
            return []  # FTS5 unavailable or malformed MATCH expression.
    finally:
        conn.close()
    return [dict(r) for r in rows]
