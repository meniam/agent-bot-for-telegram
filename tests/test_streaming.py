"""DraftStreamer: build_draft_html and stream behaviour."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infra.agent_types import StreamChunk
from src.infra.streaming import DraftStreamer, _build_draft_html


def _make_streamer() -> DraftStreamer:
    bot = MagicMock()
    bot.send_rich_message_draft = AsyncMock(return_value=None)
    return DraftStreamer(bot=bot, interval_sec=0.0)


def test_build_draft_html_text_only() -> None:
    out = _build_draft_html(lambda s: s, "", "hello")
    assert out == "hello"


def test_build_draft_html_thinking_only() -> None:
    out = _build_draft_html(lambda s: s, "thinking", "")
    assert out == "<tg-thinking>thinking</tg-thinking>"


def test_build_draft_html_both() -> None:
    out = _build_draft_html(lambda s: s, "think", "text")
    assert "<tg-thinking>think</tg-thinking>" in out
    assert "text" in out


def test_build_draft_html_truncates_thinking() -> None:
    long_thinking = "x" * 1000
    out = _build_draft_html(lambda s: s, long_thinking, "", t_limit=10)
    assert "<tg-thinking>" in out
    assert len(out) < 50


def test_build_draft_html_empty() -> None:
    assert _build_draft_html(lambda s: s, "", "") == ""


@pytest.mark.asyncio
async def test_stream_returns_text_only() -> None:
    streamer = _make_streamer()

    from collections.abc import AsyncGenerator

    async def _chunks() -> AsyncGenerator[StreamChunk, None]:
        yield StreamChunk(kind="text", text="hello")
        yield StreamChunk(kind="thinking", text="think")
        yield StreamChunk(kind="text", text=" world")

    result = await streamer.stream(42, _chunks())
    assert result == "hello world"


@pytest.mark.asyncio
async def test_stream_calls_send_draft() -> None:
    from collections.abc import AsyncGenerator

    streamer = _make_streamer()

    async def _chunks() -> AsyncGenerator[StreamChunk, None]:
        yield StreamChunk(kind="text", text="hi")

    await streamer.stream(42, _chunks())
    streamer._bot.send_rich_message_draft.assert_called()


@pytest.mark.asyncio
async def test_stream_empty_chunks() -> None:
    from collections.abc import AsyncGenerator

    streamer = _make_streamer()

    async def _chunks() -> AsyncGenerator[StreamChunk, None]:
        return
        yield StreamChunk(kind="text", text="")  # noqa: unreachable

    result = await streamer.stream(42, _chunks())
    assert result == ""
