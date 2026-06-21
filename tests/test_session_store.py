import json
from pathlib import Path

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


def test_corrupt_file_degrades_gracefully(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = tmp_path / "sessions" / "5.json"
    path.write_text("{ not json", encoding="utf-8")
    assert store.all_sessions(5) == []
    assert store.current_id(5) is None
    # And creating still works, overwriting the garbage.
    s = store.create(5)
    assert store.current_id(5) == s.id
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["current"] == s.id
