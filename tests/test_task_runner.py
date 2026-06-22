"""Unit tests for the task runner: script execution, containment, delivery."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from src.config import BotConfig
from src.infra.task_runner import TaskRunner, broadcast_targets
from src.infra.task_store import TaskStore, new_task_id
from src.infra.task_types import Task, TaskSchedule


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_rich_message(self, *, chat_id: int, rich_message: object) -> None:
        # send_md_to_chat converts then calls this; record the chat.
        self.sent.append((chat_id, type(rich_message).__name__))


def _cfg(tmp_path: Path, **over: object) -> BotConfig:
    base = {
        "name": "t",
        "telegram_bot_token": "1:abc",
        "tasks_enabled": True,
        "tasks_dir": str(tmp_path / "tasks"),
        "tasks_scripts_dir": str(tmp_path / "scripts"),
        "tasks_max_output_chars": 1000,
        "tasks_script_timeout_sec": 10,
        "allowed_chat_ids": (10, 20),
    }
    base.update(over)
    return BotConfig.model_validate(base)


def _runner(tmp_path: Path, bot: _FakeBot, cfg: BotConfig) -> TaskRunner:
    store = TaskStore(tmp_path / "tasks")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    return TaskRunner(
        bot=bot,  # type: ignore[arg-type]
        cfg=cfg,
        store=store,
        agent=object(),  # type: ignore[arg-type]
        log_for_chat=lambda _cid: logging.getLogger("test"),
        workdir_lock=asyncio.Lock(),
    )


def _script_task(name: str, chat_id: int = 10, scope: str = "user") -> Task:
    return Task(
        id=new_task_id(),
        owner_chat_id=chat_id,
        scope=scope,  # type: ignore[arg-type]
        kind="script",
        script=name,
        schedule=TaskSchedule(kind="once", run_at=datetime(2026, 1, 1, tzinfo=UTC)),
    )


def test_broadcast_targets_excludes_blacklist(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, allowed_chat_ids=(10, 20, 30), blacklist_chat_ids=(20,))
    assert broadcast_targets(cfg) == [10, 30]


async def test_run_script_captures_stdout(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    bot = _FakeBot()
    runner = _runner(tmp_path, bot, cfg)
    (tmp_path / "scripts" / "hi.py").write_text("print('hello world')", encoding="utf-8")

    outcome = await runner.run(_script_task("hi.py", chat_id=10))
    assert outcome.status == "ok"
    assert "hello world" in outcome.output
    assert outcome.delivered_to == [10]
    assert [chat for chat, _ in bot.sent] == [10]


async def test_run_script_nonzero_exit_is_error(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    bot = _FakeBot()
    runner = _runner(tmp_path, bot, cfg)
    (tmp_path / "scripts" / "boom.py").write_text(
        "import sys; sys.exit(3)", encoding="utf-8"
    )
    outcome = await runner.run(_script_task("boom.py"))
    assert outcome.status == "error"
    assert outcome.exit_code == 3
    assert bot.sent == []  # nothing delivered on failure


async def test_script_path_traversal_rejected(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    bot = _FakeBot()
    runner = _runner(tmp_path, bot, cfg)
    outcome = await runner.run(_script_task("../../etc/passwd"))
    assert outcome.status == "error"
    assert outcome.error is not None


async def test_global_script_broadcasts(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, allowed_chat_ids=(10, 20))
    bot = _FakeBot()
    runner = _runner(tmp_path, bot, cfg)
    (tmp_path / "scripts" / "g.py").write_text("print('hi')", encoding="utf-8")
    outcome = await runner.run(_script_task("g.py", chat_id=99, scope="global"))
    assert sorted(outcome.delivered_to) == [10, 20]


async def test_history_written(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    bot = _FakeBot()
    runner = _runner(tmp_path, bot, cfg)
    (tmp_path / "scripts" / "h.py").write_text("print('x')", encoding="utf-8")
    task = _script_task("h.py")
    await runner.run(task)
    store = TaskStore(tmp_path / "tasks")
    runs = store.list_history(task.id)
    assert len(runs) == 1
    assert runs[0].status == "ok"
