import html as _html
import itertools
import logging
import time
from collections.abc import AsyncIterator, Callable

from aiogram import Bot
from aiogram.types import InputRichMessage

from .agent_types import StreamChunk

log = logging.getLogger(__name__)

DEFAULT_DRAFT_INTERVAL = 0.2  # min seconds between draft updates
DRAFT_TEXT_LIMIT = 4000  # max draft message length
THINKING_PREVIEW_LIMIT = 500  # max thinking chars shown in draft
TEXT_PREVIEW_LIMIT = DRAFT_TEXT_LIMIT - THINKING_PREVIEW_LIMIT

_draft_seq = itertools.count(1)


def _build_draft_html(
    convert: Callable[[str], str],
    thinking: str,
    text: str,
    t_limit: int = THINKING_PREVIEW_LIMIT,
    x_limit: int = TEXT_PREVIEW_LIMIT,
) -> str:
    """Build the HTML payload for a streaming draft.

    Shows a <tg-thinking> block when extended-thinking tokens are present,
    followed by the accumulated text response.
    """
    parts: list[str] = []
    if thinking:
        parts.append(f"<tg-thinking>{convert(thinking[-t_limit:])}</tg-thinking>")
    if text:
        parts.append(convert(text[-x_limit:]))
    return "\n".join(parts)


class DraftStreamer:
    """Streams accumulated agent text to Telegram via `sendRichMessageDraft`.

    The final message is sent separately as a regular sendRichMessage by the
    caller — drafts are ephemeral and not persisted to chat history.

    Accepts an iterator of StreamChunk objects (kind='text' or 'thinking').
    Accumulates them locally to avoid O(N²) re-sends.
    """

    def __init__(
        self,
        bot: Bot,
        interval_sec: float = DEFAULT_DRAFT_INTERVAL,
        convert: Callable[[str], str] | None = None,
    ) -> None:
        self._bot = bot
        self._interval = interval_sec
        self._convert = convert or _html.escape

    def __repr__(self) -> str:
        return f"DraftStreamer(interval={self._interval})"

    async def stream(
        self, chat_id: int, chunks: AsyncIterator[StreamChunk]
    ) -> str:
        draft_id = next(_draft_seq) % 2_147_483_647
        last_sent = 0.0
        last_preview = ""
        thinking_acc = ""
        text_acc = ""

        async for chunk in chunks:
            if not chunk.text:
                continue
            if chunk.kind == "thinking":
                thinking_acc += chunk.text
            else:
                text_acc += chunk.text

            now = time.monotonic()
            preview = _build_draft_html(self._convert, thinking_acc, text_acc)
            if now - last_sent >= self._interval and preview != last_preview:
                try:
                    await self._bot.send_rich_message_draft(
                        chat_id=chat_id,
                        draft_id=draft_id,
                        rich_message=InputRichMessage(html=preview),
                    )
                    last_sent = now
                    last_preview = preview
                except Exception as e:
                    log.warning("draft send failed chat_id=%s: %s", chat_id, e)

        return text_acc
