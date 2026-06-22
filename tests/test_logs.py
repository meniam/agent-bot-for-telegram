"""BotLogs: per-chat LRU eviction closes file handlers."""

import logging
import logging.handlers
import sqlite3
from pathlib import Path

from src.infra.logs import BotLogs
from src.infra.message_db import SqliteChatLogHandler
from src.infra.session_store import Session


def test_for_chat_creates_logger_with_file_handler(tmp_path: Path) -> None:
    """Verify for_chat creates a logger that writes a per-chat log file."""
    logs = BotLogs(name="bot1", base_dir=tmp_path)
    log = logs.for_chat(42)
    log.info("hello")
    for h in log.handlers:
        h.flush()
    assert (tmp_path / "42.log").exists()


def test_for_chat_returns_same_logger_on_second_call(tmp_path: Path) -> None:
    """Verify for_chat returns the cached logger on a repeat call."""
    logs = BotLogs(name="bot1", base_dir=tmp_path)
    a = logs.for_chat(1)
    b = logs.for_chat(1)
    assert a is b


def test_no_base_dir_returns_noop_logger() -> None:
    """Verify a no-base-dir BotLogs returns a shared no-op logger."""
    logs = BotLogs(name="bot2", base_dir=None)
    log = logs.for_chat(1)
    # Should be silent (NullHandler) and shared across calls.
    assert log is logs.for_chat(2)


def test_lru_evicts_oldest_when_capacity_exceeded(tmp_path: Path) -> None:
    """Verify the LRU evicts the least recently used logger and closes its handlers."""
    logs = BotLogs(name="bot3", base_dir=tmp_path, capacity=2)
    log1 = logs.for_chat(1)
    log2 = logs.for_chat(2)
    # Touching chat 1 moves it to the end of the LRU.
    logs.for_chat(1)
    # Adding chat 3 should evict chat 2 (least recently used).
    logs.for_chat(3)
    # Evicted logger's handlers should be closed.
    assert log2.handlers == []
    # log1 must still have its handler.
    assert log1.handlers


def test_general_logger_writes_bot_log(tmp_path: Path) -> None:
    """Verify the general logger writes to bot.log."""
    logs = BotLogs(name="bot4", base_dir=tmp_path)
    logs.general.info("startup")
    for h in logs.general.handlers:
        h.flush()
    assert (tmp_path / "bot.log").exists()


def _cleanup_loggers(prefix: str) -> None:
    """Remove dynamically-created loggers so tests don't leak across runs."""
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith(prefix):
            logging.Logger.manager.loggerDict.pop(name, None)


def test_messages_dir_creates_db_with_session(tmp_path: Path) -> None:
    """Verify a configured messages_dir creates a db tagged with the resolved session."""
    logs = BotLogs(
        name="mbot",
        base_dir=tmp_path / "bot",
        messages_dir=tmp_path / "messages",
    )
    logs.set_session_resolver(
        lambda cid: Session(f"s{cid}", "t", auto_titled=False, created_at=0.0, last_used=0.0)
    )
    log = logs.for_chat(42)
    log.info("hi")
    db = tmp_path / "messages" / "42.db"
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        msg = conn.execute("SELECT role, session_id FROM messages").fetchone()
        sess = conn.execute("SELECT id FROM sessions").fetchone()
    finally:
        conn.close()
    assert msg == ("system", "s42")
    assert sess == ("s42",)
    _cleanup_loggers("bot.mbot")


def test_no_messages_dir_writes_no_db(tmp_path: Path) -> None:
    """Verify no message db is written when messages_dir is unset."""
    logs = BotLogs(name="nodb", base_dir=tmp_path / "bot")
    logs.for_chat(1).info("hi")
    assert not any(tmp_path.rglob("*.db"))
    _cleanup_loggers("bot.nodb")


def test_lru_eviction_closes_sqlite_connection(tmp_path: Path) -> None:
    """Verify LRU eviction closes the evicted chat's SQLite connection."""
    logs = BotLogs(
        name="evict",
        base_dir=tmp_path / "bot",
        capacity=1,
        messages_dir=tmp_path / "messages",
    )
    log1 = logs.for_chat(1)
    sqlite_handlers = [
        h for h in log1.handlers if isinstance(h, SqliteChatLogHandler)
    ]
    assert sqlite_handlers
    conn = sqlite_handlers[0]._conn
    logs.for_chat(2)  # evicts chat 1
    assert log1.handlers == []
    # connection closed → using it raises
    try:
        conn.execute("SELECT 1")
        raise AssertionError("connection should be closed")
    except sqlite3.ProgrammingError:
        pass
    _cleanup_loggers("bot.evict")


def test_messages_dir_without_resolver_tags_null_session(tmp_path: Path) -> None:
    """Verify messages are tagged with a null session when no resolver is set."""
    logs = BotLogs(
        name="nores",
        base_dir=tmp_path / "bot",
        messages_dir=tmp_path / "messages",
    )
    logs.for_chat(5).info("orphan")  # no resolver injected
    db = tmp_path / "messages" / "5.db"
    conn = sqlite3.connect(db)
    try:
        msg = conn.execute("SELECT session_id FROM messages").fetchone()
        sessions = conn.execute("SELECT count(*) FROM sessions").fetchone()
    finally:
        conn.close()
    assert msg == (None,)
    assert sessions == (0,)
    _cleanup_loggers("bot.nores")


def test_resolver_returning_none_writes_null_session(tmp_path: Path) -> None:
    """Verify a resolver returning None writes a null session id."""
    logs = BotLogs(
        name="nullres",
        base_dir=tmp_path / "bot",
        messages_dir=tmp_path / "messages",
    )
    logs.set_session_resolver(lambda _cid: None)  # resolver present but yields nothing
    logs.for_chat(9).info("hi")
    conn = sqlite3.connect(tmp_path / "messages" / "9.db")
    try:
        assert conn.execute("SELECT session_id FROM messages").fetchone() == (None,)
        assert conn.execute("SELECT count(*) FROM sessions").fetchone() == (0,)
    finally:
        conn.close()
    _cleanup_loggers("bot.nullres")


def test_resolver_receives_per_chat_id(tmp_path: Path) -> None:
    """Verify each chat's handler resolves its own id, not a shared captured one."""
    logs = BotLogs(
        name="percid",
        base_dir=tmp_path / "bot",
        messages_dir=tmp_path / "messages",
    )
    logs.set_session_resolver(
        lambda cid: Session(f"s{cid}", "t", auto_titled=False, created_at=0.0, last_used=0.0)
    )
    logs.for_chat(11).info("a")
    logs.for_chat(22).info("b")
    for chat, want in ((11, "s11"), (22, "s22")):
        conn = sqlite3.connect(tmp_path / "messages" / f"{chat}.db")
        try:
            assert conn.execute("SELECT session_id FROM messages").fetchone() == (want,)
        finally:
            conn.close()
    _cleanup_loggers("bot.percid")


def test_no_base_dir_skips_db_even_with_messages_dir(tmp_path: Path) -> None:
    """Verify base_dir=None short-circuits so messages_dir alone writes nothing.

    base_dir=None forces NOOP mode regardless of messages_dir.
    """
    logs = BotLogs(name="nobase", base_dir=None, messages_dir=tmp_path / "messages")
    logs.for_chat(1).info("hi")
    assert not (tmp_path / "messages").exists()
    assert not any(tmp_path.rglob("*.db"))


def test_eviction_closes_file_handler_too(tmp_path: Path) -> None:
    """Verify eviction also closes the evicted chat's rotating file handler."""
    logs = BotLogs(name="evictfile", base_dir=tmp_path / "bot", capacity=1)
    log1 = logs.for_chat(1)
    file_handlers = [
        h for h in log1.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert file_handlers
    fh = file_handlers[0]
    logs.for_chat(2)  # evicts chat 1
    assert log1.handlers == []
    assert fh.stream is None  # closed file handler releases its descriptor
    _cleanup_loggers("bot.evictfile")


def test_evicted_chat_reopens_as_fresh_logger(tmp_path: Path) -> None:
    """Verify an evicted chat reopens as a brand-new, freshly wired logger."""
    logs = BotLogs(
        name="reopen",
        base_dir=tmp_path / "bot",
        capacity=1,
        messages_dir=tmp_path / "messages",
    )
    first = logs.for_chat(1)
    logs.for_chat(2)  # evicts chat 1
    reborn = logs.for_chat(1)  # re-create after eviction
    assert reborn is not first  # brand-new logger object
    assert reborn.handlers  # freshly wired, not the stripped corpse
    assert any(isinstance(h, SqliteChatLogHandler) for h in reborn.handlers)
    _cleanup_loggers("bot.reopen")


def test_general_logger_no_base_dir_has_no_file_handler() -> None:
    """Verify the general logger has no file handler without a base dir."""
    logs = BotLogs(name="gennobase", base_dir=None)
    assert not any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        for h in logs.general.handlers
    )
    _cleanup_loggers("bot.gennobase")


def test_module_cleanup_after_run() -> None:
    """Remove all loggers created by this module after the run."""
    _cleanup_loggers("bot.")
