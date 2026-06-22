"""Shared backend scaffolding.

Concrete backends (Claude / Codex / PI) keep their own live-connection stores
and SDK wire details, but the per-chat lock map, idle-GC loop, and session
meta-ops delegating to ``SessionStore`` are identical. This base holds them in
one place so a fourth backend inherits instead of copy-pasting, and the three
existing ones cannot drift.

Locking note: ``new_session`` / ``switch_session`` / ``delete_session`` here
delegate teardown to ``reset`` (the Codex/PI strategy: reset interrupts an
active turn, then the store op runs). The Claude backend keeps the live client
teardown and the store mutation under a single per-chat lock, so it overrides
these three methods rather than using this template.
"""

import asyncio
import time

from .session_store import Session, SessionStore


class BaseAgentBackend:
    """Shared per-chat lock map, idle-GC loop, and session meta-ops for backends."""

    _store: SessionStore
    _idle_ttl: int
    _locks: dict[int, asyncio.Lock]
    _gc_task: asyncio.Task[None] | None

    def _init_base(self, session_store: SessionStore, idle_ttl_sec: int) -> None:
        """Initialize shared state. Call from each subclass ``__init__``."""
        self._store = session_store
        self._idle_ttl = idle_ttl_sec
        self._locks = {}
        self._gc_task = None

    def _lock(self, chat_id: int) -> asyncio.Lock:
        """Return the per-chat lock, creating it on first use."""
        return self._locks.setdefault(chat_id, asyncio.Lock())

    def _ensure_gc_running(self) -> None:
        """Start the idle-GC loop if a TTL is set and it is not already running."""
        if self._idle_ttl <= 0:
            return
        if self._gc_task is None or self._gc_task.done():
            self._gc_task = asyncio.create_task(self._gc_loop())

    async def _gc_loop(self) -> None:
        """Sweep idle sessions on a derived interval until cancelled."""
        interval = max(min(self._idle_ttl / 4, 60.0), 5.0)
        try:
            while True:
                await asyncio.sleep(interval)
                await self._gc_idle()
        except asyncio.CancelledError:
            raise

    def _stale_chat_ids(self, last_used: dict[int, float]) -> list[int]:
        """Chat ids idle past the TTL whose lock is free (skip in-flight turns)."""
        now = time.monotonic()
        stale: list[int] = []
        for chat_id, used in last_used.items():
            if now - used <= self._idle_ttl:
                continue
            lock = self._locks.get(chat_id)
            if lock is not None and lock.locked():
                continue
            stale.append(chat_id)
        return stale

    async def _gc_idle(self) -> None:
        """Drop sessions idle past the TTL. Implemented per backend."""
        raise NotImplementedError

    async def reset(self, chat_id: int) -> None:
        """Tear down the live connection for a chat. Implemented per backend."""
        _ = chat_id
        raise NotImplementedError

    async def new_session(self, chat_id: int) -> Session:
        """Reset the live connection and create a fresh current session."""
        await self.reset(chat_id)
        return self._store.create(chat_id)

    async def switch_session(self, chat_id: int, sid: str) -> Session | None:
        """Make session ``sid`` current after a reset; return None if unknown."""
        session = self._store.get_by_id(chat_id, sid)
        if session is None:
            return None
        await self.reset(chat_id)
        self._store.set_current(chat_id, session.id)
        return session

    async def delete_session(self, chat_id: int, sid: str) -> Session | None:
        """Delete session ``sid``, resetting first if it was current. None if unknown."""
        target = self._store.get_by_id(chat_id, sid)
        if target is None:
            return None
        if self._store.current_id(chat_id) == target.id:
            await self.reset(chat_id)
        self._store.delete(chat_id, target.id)
        return target

    def list_sessions(self, chat_id: int) -> list[Session]:
        """Return all stored sessions for the chat."""
        return self._store.all_sessions(chat_id)

    def current_session(self, chat_id: int) -> Session | None:
        """Return the chat's current session, or None if it has none."""
        return self._store.current(chat_id)
