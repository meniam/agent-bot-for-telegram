"""Logging: global console + per-bot files.

File layout:
  <logs_dir>/<internal_name>/bot.log         — general log for this bot
  <logs_dir>/<internal_name>/<chat_id>.log   — chat events (user/bot/errors)
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import queue
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

from .message_db import SqliteChatLogHandler

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .session_store import Session

GENERAL_FMT = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
CHAT_FMT = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5
DEFAULT_CHAT_LOGGER_CAPACITY = 256

_NOOP = logging.getLogger("chat.noop")
_NOOP.addHandler(logging.NullHandler())
_NOOP.propagate = False


def setup_console() -> None:
    """Global init: log everything to console."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(GENERAL_FMT)
    root.addHandler(console)


class BotLogs:
    """Logs for a single bot: writes to <base_dir>/bot.log + per-chat files.

    Chat-loggers are kept in a bounded LRU; when the cap is exceeded, the
    least-recently-used logger is evicted: its handlers are closed (releasing
    the file descriptor) and the logger is removed from the global registry.
    """

    def __init__(
        self,
        name: str,
        base_dir: Path | None,
        capacity: int = DEFAULT_CHAT_LOGGER_CAPACITY,
        messages_dir: Path | None = None,
    ) -> None:
        """Set up this bot's general log file and the per-chat logger registry.

        ``base_dir`` enables file logging (``bot.log`` + per-chat ``.log``);
        ``messages_dir`` enables the structured SQLite mirror. Either being None
        disables that sink (e.g. in tests).
        """
        self._name = name
        self._base = base_dir
        self._capacity = capacity
        self._messages_dir = messages_dir
        self._session_resolver: Callable[[int], Session | None] | None = None
        self._chat_loggers: OrderedDict[int, logging.Logger] = OrderedDict()
        # Per-chat QueueListener feeding the SQLite handler from a background
        # thread, so message logging never blocks the event loop on sqlite I/O.
        self._listeners: dict[int, logging.handlers.QueueListener] = {}
        # Background threads that drain+close evicted listeners off the event
        # loop; joined at shutdown so no tail is lost.
        self._evict_threads: set[threading.Thread] = set()
        self._general = logging.getLogger(f"bot.{name}")
        self._general.setLevel(logging.INFO)

        if base_dir is not None:
            base_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                base_dir / "bot.log",
                maxBytes=MAX_BYTES,
                backupCount=BACKUPS,
                encoding="utf-8",
            )
            handler.setFormatter(GENERAL_FMT)
            self._general.addHandler(handler)
        # Keep propagate on — the shared console handler will show events from every bot.

    @property
    def general(self) -> logging.Logger:
        """The bot-wide general logger (``bot.<name>``)."""
        return self._general

    def set_session_resolver(
        self, resolver: Callable[[int], Session | None]
    ) -> None:
        """Inject the current-session lookup used to tag SQLite message rows.

        Set after the `SessionStore` exists (it is built later than `BotLogs`).
        Per-chat loggers are created lazily on the first message, i.e. after
        this is set, so existing handlers need no retrofit.
        """
        self._session_resolver = resolver

    def for_chat(self, chat_id: int) -> logging.Logger:
        """Return (creating + caching) the per-chat logger for ``chat_id``.

        Lazily builds a logger writing to ``<chat_id>.log`` plus the optional
        SQLite mirror, records it in the LRU as most-recently-used, and evicts
        the least-recently-used logger when over capacity. Returns a no-op
        logger when file logging is disabled.
        """
        if self._base is None:
            return _NOOP
        existing = self._chat_loggers.get(chat_id)
        if existing is not None:
            self._chat_loggers.move_to_end(chat_id)
            return existing

        log = logging.getLogger(f"bot.{self._name}.chat.{chat_id}")
        log.setLevel(logging.INFO)
        log.propagate = False
        handler = logging.handlers.RotatingFileHandler(
            self._base / f"{chat_id}.log",
            maxBytes=MAX_BYTES,
            backupCount=BACKUPS,
            encoding="utf-8",
        )
        handler.setFormatter(CHAT_FMT)
        log.addHandler(handler)

        # Structured SQLite mirror at <messages_dir>/<chat_id>.db (configurable;
        # defaults to <logs_dir>/messages). Per-chat file, no <bot> segment.
        # None → SQLite logging disabled (e.g. in unit tests).
        if self._messages_dir is not None:
            self._messages_dir.mkdir(parents=True, exist_ok=True)
            resolver = self._session_resolver
            session_of = (
                (lambda cid=chat_id: resolver(cid))
                if resolver is not None
                else None
            )
            # The SQLite write runs in the listener's background thread; the
            # logger only enqueues (non-blocking) via the QueueHandler.
            sqlite_handler = SqliteChatLogHandler(
                self._messages_dir / f"{chat_id}.db", session_of
            )
            log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
            listener = logging.handlers.QueueListener(
                log_queue, sqlite_handler, respect_handler_level=True
            )
            listener.start()
            self._listeners[chat_id] = listener
            log.addHandler(logging.handlers.QueueHandler(log_queue))

        self._chat_loggers[chat_id] = log

        while len(self._chat_loggers) > self._capacity:
            evicted_id, evicted_log = self._chat_loggers.popitem(last=False)
            self._evict(evicted_id, evicted_log)

        return log

    @staticmethod
    def _shutdown_listener(listener: logging.handlers.QueueListener) -> None:
        """Drain the listener queue and close its (SQLite) handlers, losslessly."""
        with contextlib.suppress(Exception):
            listener.stop()  # drains the queue, then joins the worker thread
        for handler in listener.handlers:
            with contextlib.suppress(Exception):
                handler.close()

    def _evict(self, chat_id: int, log: logging.Logger, *, background: bool = True) -> None:
        """Close an evicted chat logger's handlers and drop it from the registry.

        ``listener.stop()`` joins the worker thread, which may be mid-write (up
        to the sqlite busy_timeout). On the LRU hot path (called from the event
        loop via ``for_chat``) that join runs in a daemon thread so it never
        blocks the loop; at shutdown (``close``) it runs inline to guarantee the
        tail is flushed before the process exits.
        """
        listener = self._listeners.pop(chat_id, None)
        if listener is not None:
            if background:
                t = threading.Thread(
                    target=self._shutdown_listener, args=(listener,), daemon=True
                )
                self._evict_threads.add(t)
                t.start()
            else:
                self._shutdown_listener(listener)
        for handler in list(log.handlers):
            with contextlib.suppress(Exception):
                handler.close()
            log.removeHandler(handler)
        logging.Logger.manager.loggerDict.pop(
            f"bot.{self._name}.chat.{chat_id}", None
        )

    def close(self) -> None:
        """Stop every chat's SQLite listener and close its handlers (shutdown)."""
        for chat_id, log in list(self._chat_loggers.items()):
            self._evict(chat_id, log, background=False)
        self._chat_loggers.clear()
        # Join any in-flight background eviction shutdowns so the tail flushes.
        for t in list(self._evict_threads):
            t.join(timeout=5)
        self._evict_threads.clear()
