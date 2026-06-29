"""Bot-scoped logging helpers for scheduled-task lifecycle logs."""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_BOT_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "task_log_bot_name", default=None
)
_TASK_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "task_log_task_id", default=None
)

_SECRET_KEYS = (
    "token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "authorization",
    "cookie",
    "set-cookie",
    "credential",
    "private_key",
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|api[_-]?key|password|secret|authorization|cookie)"
    r"\s*[:=]\s*([^\s,;]+)"
)


class TaskLogFilter(logging.Filter):
    """Allow only log records emitted while the current task context matches a bot."""

    def __init__(self, bot_name: str) -> None:
        """Store the bot name whose task-context records should pass."""
        super().__init__()
        self._bot_name = bot_name

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether ``record`` belongs to this filter's bot task context."""
        bot_name = _BOT_NAME.get()
        if bot_name != self._bot_name:
            return False
        record.bot_name = bot_name
        record.task_id = _TASK_ID.get() or "-"
        return True


class TaskLogHandle:
    """Own file handlers attached to task lifecycle module loggers for one bot."""

    def __init__(
        self,
        handler: logging.Handler,
        logger_names: tuple[str, ...],
    ) -> None:
        """Store the handler and logger names it was attached to."""
        self._handler = handler
        self._logger_names = logger_names

    def close(self) -> None:
        """Detach and close the task lifecycle file handler."""
        for name in self._logger_names:
            logging.getLogger(name).removeHandler(self._handler)
        self._handler.close()


def attach_task_log(
    *,
    bot_name: str,
    tasks_dir: Path,
    logger_names: tuple[str, ...],
) -> TaskLogHandle:
    """Attach one bot-scoped rotating task log handler to lifecycle loggers."""
    handler = RotatingFileHandler(
        tasks_dir / "tasks.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    handler.addFilter(TaskLogFilter(bot_name))
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [bot=%(bot_name)s task=%(task_id)s]: "
            "%(message)s"
        )
    )
    for name in logger_names:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
    return TaskLogHandle(handler, logger_names)


@contextlib.contextmanager
def task_log_context(bot_name: str, task_id: str | None = None) -> Iterator[None]:
    """Mark log records emitted in this block as belonging to one bot/task."""
    bot_token = _BOT_NAME.set(bot_name)
    task_token = _TASK_ID.set(task_id)
    try:
        yield
    finally:
        _TASK_ID.reset(task_token)
        _BOT_NAME.reset(bot_token)


def redact(value: Any, *, depth: int = 5) -> Any:
    """Return ``value`` with common secret-looking keys replaced by a marker."""
    if depth <= 0:
        return "<redacted-depth>"
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower().replace("-", "_")
            if any(secret in key_text for secret in _SECRET_KEYS):
                out[key] = "<redacted>"
            else:
                out[key] = redact(item, depth=depth - 1)
        return out
    if isinstance(value, list):
        return [redact(item, depth=depth - 1) for item in value[:50]]
    if isinstance(value, tuple):
        return tuple(redact(item, depth=depth - 1) for item in value[:50])
    if isinstance(value, str):
        return _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", value)
    return value
