"""Per-chat session metadata store, backed by SQLite.

The Claude Agent SDK already persists conversation history to disk keyed by
``session_id`` (a UUID) and can ``resume`` it. This store is the bot-level
*meta layer* on top of that: it maps a ``chat_id`` to a list of named
sessions plus a ``current`` pointer, so users can keep several conversations
per chat, switch between them, and have them survive a bot restart.

State lives in the **same** per-chat SQLite file as the structured message log
(``<base_dir>/<chat_id>.db``, see ``message_db.py``):

- ``sessions`` — one row per named session (``id``, ``title``, ``auto_titled``,
  timestamps). This store is its authoritative writer.
- ``chat_meta`` — a single row (``id = 0``) holding ``current_session_id``.

Operations open a short-lived connection, commit, and close — the store keeps
no in-memory cache, so it stays correct across the bot's per-chat locks and
alongside the message-log handler's long-lived connection (WAL serializes
writers).
"""

import asyncio
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .message_db import connect

log = logging.getLogger(__name__)

_UPSERT_CURRENT = """
INSERT INTO chat_meta (id, current_session_id) VALUES (0, ?)
ON CONFLICT(id) DO UPDATE SET current_session_id = excluded.current_session_id
"""

_INSERT_SESSION = """
INSERT INTO sessions (id, title, auto_titled, created_at, last_used, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
"""


@dataclass(slots=True)
class Session:
    """One named conversation. ``id`` is the SDK session UUID."""

    id: str
    title: str
    auto_titled: bool
    created_at: float
    last_used: float


class SessionStore:
    """Per-chat named-session store backed by the chat's SQLite file.

    Authoritative writer of the ``sessions`` table and the ``current`` pointer
    in ``chat_meta``. Each operation opens a short-lived connection and keeps no
    in-memory cache.
    """

    def __init__(
        self,
        base_dir: Path,
        default_title: str,
    ) -> None:
        """Store sessions under ``base_dir`` (created if needed), with ``default_title`` for new sessions."""
        self._base_dir = base_dir
        self._default_title = default_title
        base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: int) -> Path:
        """Return the per-chat SQLite file path for ``chat_id``."""
        return self._base_dir / f"{chat_id}.db"

    @staticmethod
    def _row_to_session(row: Any) -> Session:
        """Build a `Session` from a ``sessions`` table row."""
        return Session(
            id=str(row["id"]),
            title=str(row["title"] or ""),
            auto_titled=bool(row["auto_titled"]),
            created_at=float(row["created_at"] or 0.0),
            last_used=float(row["last_used"] or 0.0),
        )

    # Public methods are async: the actual sqlite work runs in a worker thread
    # (via ``asyncio.to_thread``) so a short blocking read/write never stalls the
    # event loop. The ``_*_sync`` helpers hold the real I/O and may be composed
    # directly (one thread hop per public call).

    def _all_sessions_sync(self, chat_id: int) -> list[Session]:
        path = self._path(chat_id)
        if not path.exists():
            return []
        try:
            conn = connect(path)
        except Exception:
            log.exception("session store: failed to open %s", path)
            return []
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, auto_titled, created_at, last_used "
                "FROM sessions ORDER BY created_at"
            ).fetchall()
        except Exception:
            log.exception("session store: failed to read %s", path)
            return []
        finally:
            conn.close()
        return [self._row_to_session(r) for r in rows]

    async def all_sessions(self, chat_id: int) -> list[Session]:
        """Sessions ordered by ``created_at`` — index+1 is the `/sess <n>` ordinal."""
        return await asyncio.to_thread(self._all_sessions_sync, chat_id)

    def _current_id_sync(self, chat_id: int) -> str | None:
        path = self._path(chat_id)
        if not path.exists():
            return None
        try:
            conn = connect(path)
        except Exception:
            log.exception("session store: failed to open %s", path)
            return None
        try:
            row = conn.execute(
                "SELECT current_session_id FROM chat_meta WHERE id = 0"
            ).fetchone()
        except Exception:
            log.exception("session store: failed to read current of %s", path)
            return None
        finally:
            conn.close()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    async def current_id(self, chat_id: int) -> str | None:
        """Return the chat's current session id, or None if unset/unreadable."""
        return await asyncio.to_thread(self._current_id_sync, chat_id)

    def _get_by_id_sync(self, chat_id: int, sid: str) -> Session | None:
        path = self._path(chat_id)
        if not path.exists():
            return None
        try:
            conn = connect(path)
        except Exception:
            log.exception("session store: failed to open %s", path)
            return None
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, title, auto_titled, created_at, last_used "
                "FROM sessions WHERE id = ?",
                (sid,),
            ).fetchone()
        except Exception:
            log.exception("session store: failed to read %s", path)
            return None
        finally:
            conn.close()
        return self._row_to_session(row) if row is not None else None

    def _current_sync(self, chat_id: int) -> Session | None:
        sid = self._current_id_sync(chat_id)
        if sid is None:
            return None
        return self._get_by_id_sync(chat_id, sid)

    async def current(self, chat_id: int) -> Session | None:
        """Return the chat's current `Session`, or None if there is none."""
        return await asyncio.to_thread(self._current_sync, chat_id)

    def current_sync(self, chat_id: int) -> Session | None:
        """Look up the current session synchronously, for non-async callers.

        Used by the SQLite log handler's session resolver, which runs in the
        QueueListener's background thread (no event loop to await on). Event-loop
        callers must use the async `current` instead.
        """
        return self._current_sync(chat_id)

    def _create_sync(self, chat_id: int) -> Session:
        now = time.time()
        session = Session(
            id=str(uuid.uuid4()),
            title=self._default_title,
            auto_titled=False,
            created_at=now,
            last_used=now,
        )
        conn = connect(self._path(chat_id))
        try:
            conn.execute(
                _INSERT_SESSION,
                (
                    session.id,
                    session.title,
                    int(session.auto_titled),
                    session.created_at,
                    session.last_used,
                    now,
                ),
            )
            conn.execute(_UPSERT_CURRENT, (session.id,))
            conn.commit()
        finally:
            conn.close()
        return session

    async def create(self, chat_id: int) -> Session:
        """Mint a new session, persist it, and make it current."""
        return await asyncio.to_thread(self._create_sync, chat_id)

    def _set_current_sync(self, chat_id: int, sid: str) -> None:
        conn = connect(self._path(chat_id))
        try:
            conn.execute(_UPSERT_CURRENT, (sid,))
            conn.commit()
        finally:
            conn.close()

    async def set_current(self, chat_id: int, sid: str) -> None:
        """Point the chat's current pointer at session ``sid``."""
        await asyncio.to_thread(self._set_current_sync, chat_id, sid)

    async def get_by_ordinal(self, chat_id: int, ordinal: int) -> Session | None:
        """Return the 1-based ``ordinal`` session (creation order), or None."""
        sessions = await self.all_sessions(chat_id)
        if 1 <= ordinal <= len(sessions):
            return sessions[ordinal - 1]
        return None

    async def get_by_id(self, chat_id: int, sid: str) -> Session | None:
        """Return the session with id ``sid`` (indexed lookup), or None."""
        return await asyncio.to_thread(self._get_by_id_sync, chat_id, sid)

    async def list_by_recency(self, chat_id: int) -> list[Session]:
        """Sessions newest-interaction-first (by ``last_used``) — for display."""
        sessions = await self.all_sessions(chat_id)
        return sorted(sessions, key=lambda s: s.last_used, reverse=True)

    def _set_title_sync(self, chat_id: int, sid: str, title: str) -> None:
        conn = connect(self._path(chat_id))
        try:
            conn.execute(
                "UPDATE sessions SET title = ?, auto_titled = 1 WHERE id = ?",
                (title, sid),
            )
            conn.commit()
        finally:
            conn.close()

    async def set_title(self, chat_id: int, sid: str, title: str) -> None:
        """Set a session's title and mark it as auto-titled."""
        await asyncio.to_thread(self._set_title_sync, chat_id, sid, title)

    def _delete_sync(self, chat_id: int, sid: str) -> str | None:
        conn = connect(self._path(chat_id))
        try:
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            row = conn.execute(
                "SELECT current_session_id FROM chat_meta WHERE id = 0"
            ).fetchone()
            current = row[0] if row is not None else None
            if current == sid:
                latest = conn.execute(
                    "SELECT id FROM sessions ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                current = latest[0] if latest is not None else None
                conn.execute(_UPSERT_CURRENT, (current,))
            conn.commit()
        finally:
            conn.close()
        return str(current) if current else None

    async def delete(self, chat_id: int, sid: str) -> str | None:
        """Remove a session's meta row and return the resulting current id.

        If the deleted session was current, repoint current to the most
        recently created remaining session (or None if none are left). The
        SDK's on-disk JSONL is left untouched.
        """
        return await asyncio.to_thread(self._delete_sync, chat_id, sid)

    def _touch_sync(self, chat_id: int, sid: str) -> None:
        conn = connect(self._path(chat_id))
        try:
            conn.execute(
                "UPDATE sessions SET last_used = ? WHERE id = ?",
                (time.time(), sid),
            )
            conn.commit()
        finally:
            conn.close()

    async def touch(self, chat_id: int, sid: str) -> None:
        """Update a session's ``last_used`` timestamp to now."""
        await asyncio.to_thread(self._touch_sync, chat_id, sid)

