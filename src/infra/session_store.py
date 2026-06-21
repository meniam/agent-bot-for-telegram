"""Per-chat session metadata store.

The Claude Agent SDK already persists conversation history to disk keyed by
``session_id`` (a UUID) and can ``resume`` it. This store is the bot-level
*meta layer* on top of that: it maps a ``chat_id`` to a list of named
sessions plus a ``current`` pointer, so users can keep several conversations
per chat, switch between them, and have them survive a bot restart.

One JSON file per chat at ``<base_dir>/<chat_id>.json``:

    {
      "current": "<uuid>",
      "sessions": [
        {"id": "<uuid>", "title": "...", "auto_titled": false,
         "created_at": 0.0, "last_used": 0.0}
      ]
    }

Writes are atomic (temp file + ``os.replace``). All reads parse the file
fresh — the store keeps no in-memory cache, so it stays correct across the
bot's per-chat locks without extra coordination.
"""

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Session:
    """One named conversation. ``id`` is the SDK session UUID."""

    id: str
    title: str
    auto_titled: bool
    created_at: float
    last_used: float


class SessionStore:
    def __init__(self, base_dir: Path, default_title: str) -> None:
        self._base_dir = base_dir
        self._default_title = default_title
        base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: int) -> Path:
        return self._base_dir / f"{chat_id}.json"

    def _read(self, chat_id: int) -> dict[str, Any]:
        path = self._path(chat_id)
        if not path.exists():
            return {"current": None, "sessions": []}
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            log.exception("session store: failed to read %s", path)
            return {"current": None, "sessions": []}
        if not isinstance(data, dict):
            return {"current": None, "sessions": []}
        data.setdefault("current", None)
        sessions = data.get("sessions")
        if not isinstance(sessions, list):
            data["sessions"] = []
        return data

    def _write(self, chat_id: int, data: dict[str, Any]) -> None:
        path = self._path(chat_id)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(path)
        except OSError:
            log.exception("session store: failed to write %s", path)
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _to_sessions(data: dict[str, Any]) -> list[Session]:
        out: list[Session] = []
        for raw in data.get("sessions", []):
            if not isinstance(raw, dict) or "id" not in raw:
                continue
            out.append(
                Session(
                    id=str(raw["id"]),
                    title=str(raw.get("title", "")),
                    auto_titled=bool(raw.get("auto_titled", False)),
                    created_at=float(raw.get("created_at", 0.0)),
                    last_used=float(raw.get("last_used", 0.0)),
                )
            )
        out.sort(key=lambda s: s.created_at)
        return out

    def all_sessions(self, chat_id: int) -> list[Session]:
        """Sessions ordered by ``created_at`` — index+1 is the `/sess <n>` ordinal."""
        return self._to_sessions(self._read(chat_id))

    def current_id(self, chat_id: int) -> str | None:
        data = self._read(chat_id)
        current = data.get("current")
        return str(current) if current else None

    def current(self, chat_id: int) -> Session | None:
        sid = self.current_id(chat_id)
        if sid is None:
            return None
        for s in self.all_sessions(chat_id):
            if s.id == sid:
                return s
        return None

    def create(self, chat_id: int) -> Session:
        """Mint a new session, append it, and make it current."""
        now = time.time()
        session = Session(
            id=str(uuid.uuid4()),
            title=self._default_title,
            auto_titled=False,
            created_at=now,
            last_used=now,
        )
        data = self._read(chat_id)
        data["sessions"].append(asdict(session))
        data["current"] = session.id
        self._write(chat_id, data)
        return session

    def set_current(self, chat_id: int, sid: str) -> None:
        data = self._read(chat_id)
        data["current"] = sid
        self._write(chat_id, data)

    def get_by_ordinal(self, chat_id: int, ordinal: int) -> Session | None:
        sessions = self.all_sessions(chat_id)
        if 1 <= ordinal <= len(sessions):
            return sessions[ordinal - 1]
        return None

    def get_by_id(self, chat_id: int, sid: str) -> Session | None:
        for s in self.all_sessions(chat_id):
            if s.id == sid:
                return s
        return None

    def list_by_recency(self, chat_id: int) -> list[Session]:
        """Sessions newest-interaction-first (by ``last_used``) — for display."""
        return sorted(self.all_sessions(chat_id), key=lambda s: s.last_used, reverse=True)

    def set_title(self, chat_id: int, sid: str, title: str) -> None:
        data = self._read(chat_id)
        for raw in data["sessions"]:
            if isinstance(raw, dict) and str(raw.get("id")) == sid:
                raw["title"] = title
                raw["auto_titled"] = True
                self._write(chat_id, data)
                return

    def delete(self, chat_id: int, sid: str) -> str | None:
        """Remove a session's meta entry. If it was current, repoint current to
        the most recently created remaining session (or None if none left).
        Returns the new current id. The SDK's on-disk JSONL is left untouched.
        """
        data = self._read(chat_id)
        data["sessions"] = [
            raw
            for raw in data["sessions"]
            if not (isinstance(raw, dict) and str(raw.get("id")) == sid)
        ]
        if data.get("current") == sid:
            remaining = self._to_sessions(data)
            data["current"] = remaining[-1].id if remaining else None
        self._write(chat_id, data)
        current = data.get("current")
        return str(current) if current else None

    def touch(self, chat_id: int, sid: str) -> None:
        data = self._read(chat_id)
        changed = False
        for raw in data["sessions"]:
            if isinstance(raw, dict) and str(raw.get("id")) == sid:
                raw["last_used"] = time.time()
                changed = True
                break
        if changed:
            self._write(chat_id, data)
