"""Per-album debounce for Telegram media_group_id arrivals.

A single Telegram album arrives as N separate `photo` / `document` updates
sharing the same `media_group_id`. We hold a short timer per media_group_id,
restarting it on every arrival, so the agent fires once after the album is
fully delivered. Captions can land on any one of the album's items —
collect them here.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram.types import Message

ALBUM_DEBOUNCE_SEC = 1.5


class AlbumDebouncer:
    """Debounce album arrivals so the agent fires once per media group."""

    def __init__(self, glog: logging.Logger, bot_name: str) -> None:
        """Store loggers and init the per-group timer and caption maps."""
        self._glog = glog
        self._bot_name = bot_name
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._captions: dict[str, str] = {}

    async def schedule(
        self,
        message: Message,
        caption: str,
        cl: logging.Logger,
        on_fire: Callable[[Message, str], Awaitable[None]],
    ) -> None:
        """Fire `on_fire` now for a single item, or after a quiet period for an album.

        The `message` passed to `on_fire` is the LAST item observed in the
        album — react_to lands on it, preserving the existing behavior.
        """
        mg = message.media_group_id
        if not mg:
            await on_fire(message, caption)
            return

        if caption:
            self._captions[mg] = caption

        existing = self._timers.get(mg)
        if existing is not None and not existing.done():
            existing.cancel()

        async def _delayed() -> None:
            """Wait out the debounce, then fire with the collected caption."""
            try:
                await asyncio.sleep(ALBUM_DEBOUNCE_SEC)
            except asyncio.CancelledError:
                return
            self._timers.pop(mg, None)
            cap = self._captions.pop(mg, "")
            cl.info("album %s: firing with caption=%r", mg, cap)
            try:
                await on_fire(message, cap)
            except Exception:
                self._glog.exception(
                    "[%s] album fire failed", self._bot_name
                )

        self._timers[mg] = asyncio.create_task(_delayed())
