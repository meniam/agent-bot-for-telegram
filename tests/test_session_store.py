import logging
import sqlite3
from pathlib import Path

from src.infra.message_db import SqliteChatLogHandler, query_messages
from src.infra.session_store import SessionStore


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions", default_title="Новая сессия")


def test_create_sets_current_and_default_title(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create(42)
    assert s.title == "Новая сессия"
    assert s.auto_titled is False
    assert store.current_id(42) == s.id
    assert [x.id for x in store.all_sessions(42)] == [s.id]


def test_unknown_chat_is_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.current_id(7) is None
    assert store.all_sessions(7) == []
    assert store.current(7) is None


def test_ordinal_is_creation_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create(1)
    second = store.create(1)
    o1 = store.get_by_ordinal(1, 1)
    o2 = store.get_by_ordinal(1, 2)
    assert o1 is not None and o1.id == first.id
    assert o2 is not None and o2.id == second.id
    assert store.get_by_ordinal(1, 3) is None
    assert store.get_by_ordinal(1, 0) is None


def test_set_current_via_switch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create(1)
    store.create(1)  # second becomes current
    store.set_current(1, first.id)
    assert store.current_id(1) == first.id
    current = store.current(1)
    assert current is not None and current.id == first.id


def test_set_title_marks_auto_titled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create(9)
    store.set_title(9, s.id, "Рефакторинг стриминга")
    reloaded = store.current(9)
    assert reloaded is not None
    assert reloaded.title == "Рефакторинг стриминга"
    assert reloaded.auto_titled is True


def test_delete_non_current_keeps_current(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create(1)
    second = store.create(1)  # current
    new_current = store.delete(1, first.id)
    assert new_current == second.id
    assert [s.id for s in store.all_sessions(1)] == [second.id]


def test_delete_current_repoints_to_latest_remaining(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create(1)
    second = store.create(1)
    third = store.create(1)  # current
    new_current = store.delete(1, third.id)
    # Most recently created of the remaining (second).
    assert new_current == second.id
    assert store.current_id(1) == second.id
    assert {s.id for s in store.all_sessions(1)} == {first.id, second.id}


def test_delete_last_session_clears_current(tmp_path: Path) -> None:
    store = _store(tmp_path)
    only = store.create(1)
    new_current = store.delete(1, only.id)
    assert new_current is None
    assert store.current_id(1) is None
    assert store.all_sessions(1) == []


def test_round_trip_persists_to_disk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create(100)
    # A fresh store over the same dir sees the persisted state.
    reopened = SessionStore(tmp_path / "sessions", default_title="x")
    assert reopened.current_id(100) == s.id
    assert [x.id for x in reopened.all_sessions(100)] == [s.id]


def test_corrupt_db_reads_degrade_gracefully(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = tmp_path / "sessions" / "5.db"
    path.write_bytes(b"not a sqlite database")
    assert store.all_sessions(5) == []
    assert store.current_id(5) is None


# --- meaner edge cases ----------------------------------------------------


def test_create_mints_distinct_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = {store.create(1).id for _ in range(5)}
    assert len(ids) == 5  # uuid4, no collisions


def test_set_current_to_unknown_id_dangles(tmp_path: Path) -> None:
    """Pointer can be set to a non-existent id; current() must not invent a row."""
    store = _store(tmp_path)
    store.create(1)
    store.set_current(1, "ghost-id")
    assert store.current_id(1) == "ghost-id"  # raw pointer persisted
    assert store.current(1) is None  # but it resolves to nothing


def test_set_title_on_non_current_does_not_move_current(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create(1)
    second = store.create(1)  # current
    store.set_title(1, first.id, "Старая беседа")
    assert store.current_id(1) == second.id  # unchanged
    renamed = store.get_by_id(1, first.id)
    assert renamed is not None
    assert renamed.title == "Старая беседа"
    assert renamed.auto_titled is True


def test_touch_reorders_recency_not_creation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create(1)
    second = store.create(1)
    store.touch(1, first.id)  # first now most-recently-used
    assert [s.id for s in store.all_sessions(1)] == [first.id, second.id]  # creation
    assert [s.id for s in store.list_by_recency(1)] == [first.id, second.id]  # recency
    store.touch(1, second.id)
    assert [s.id for s in store.list_by_recency(1)] == [second.id, first.id]


def test_get_by_id_unknown_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(1)
    assert store.get_by_id(1, "nope") is None
    assert store.get_by_id(999, "nope") is None  # unknown chat too


def test_delete_unknown_sid_is_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.create(1)
    b = store.create(1)  # current
    returned = store.delete(1, "not-here")
    assert returned == b.id  # current preserved
    assert {s.id for s in store.all_sessions(1)} == {a.id, b.id}


def test_ordinals_shift_after_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create(1)
    second = store.create(1)
    third = store.create(1)
    store.delete(1, second.id)
    # remaining ordered by creation: first(1), third(2)
    o1 = store.get_by_ordinal(1, 1)
    o2 = store.get_by_ordinal(1, 2)
    assert o1 is not None and o1.id == first.id
    assert o2 is not None and o2.id == third.id
    assert store.get_by_ordinal(1, 3) is None


def test_null_title_row_reads_as_empty_string(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create(1)
    path = tmp_path / "sessions" / "1.db"
    conn = sqlite3.connect(path)
    conn.execute("UPDATE sessions SET title = NULL WHERE id = ?", (s.id,))
    conn.commit()
    conn.close()
    reloaded = store.get_by_id(1, s.id)
    assert reloaded is not None
    assert reloaded.title == ""  # NULL coalesced, no crash


def test_set_title_and_touch_survive_reopen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create(1)
    store.set_title(1, s.id, "Закреплённое")
    store.touch(1, s.id)
    reopened = SessionStore(tmp_path / "sessions", default_title="x")
    again = reopened.get_by_id(1, s.id)
    assert again is not None
    assert again.title == "Закреплённое"
    assert again.auto_titled is True
    assert again.last_used > 0.0


def test_negative_group_chat_id_round_trips(tmp_path: Path) -> None:
    """Telegram group ids are negative; the db filename must handle them safely."""
    store = _store(tmp_path)
    gid = -1001234567890
    s = store.create(gid)
    assert (tmp_path / "sessions" / f"{gid}.db").exists()
    reopened = SessionStore(tmp_path / "sessions", default_title="x")
    assert reopened.current_id(gid) == s.id
    # a positive twin is a separate, isolated chat (no abs() collision)
    assert reopened.all_sessions(-gid) == []


def test_store_and_message_handler_share_one_file(tmp_path: Path) -> None:
    """SessionStore + the log handler write the same per-chat db without clobber."""
    store = _store(tmp_path)
    s = store.create(7)
    db = tmp_path / "sessions" / "7.db"
    h = SqliteChatLogHandler(db, session_of=lambda: store.current(7))
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hi", None, None)
    rec.role = "user"
    h.emit(rec)
    h.close()
    # store still sees its session; the message landed tagged with it
    assert [x.id for x in store.all_sessions(7)] == [s.id]
    msgs = query_messages(db)
    assert len(msgs) == 1
    assert msgs[0]["session_id"] == s.id
