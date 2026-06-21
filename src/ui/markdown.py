"""Markdown → Telegram HTML conversion, chunked send, audio filename.

Pure helpers — no I/O state, no closures over per-bot config. Reusable
across bots.
"""

import html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

log = logging.getLogger(__name__)

TG_LIMIT = 4000

_md = MarkdownIt()
_md.enable("strikethrough")
_md.enable("table")

_SAFE_TAG_RE = re.compile(
    r"</?(?:mark|sub|sup|details|summary|tg-spoiler)(?:\s[^>]*)?>",
    re.IGNORECASE,
)


def _passthrough_safe_tags(raw: str) -> str:
    """Escape plain text, keep whitelisted HTML tags, strip everything else."""
    parts: list[str] = []
    pos = 0
    for m in re.finditer(r"<[^>]+>", raw):
        parts.append(html.escape(raw[pos : m.start()]))
        tag = m.group(0)
        if _SAFE_TAG_RE.fullmatch(tag):
            parts.append(tag)
        pos = m.end()
    parts.append(html.escape(raw[pos:]))
    return "".join(parts)


def _render(node: SyntaxTreeNode) -> str:
    t = node.type
    ch = node.children or []

    if t == "root":
        out = "".join(_render(c) for c in ch)
        return re.sub(r"\n{3,}", "\n\n", out).strip()

    if t == "inline":
        return "".join(_render(c) for c in ch)

    if t == "text":
        return html.escape(node.content)

    if t in ("softbreak", "hardbreak"):
        return "\n"

    if t == "strong":
        return f'<b>{"".join(_render(c) for c in ch)}</b>'

    if t == "em":
        return f'<i>{"".join(_render(c) for c in ch)}</i>'

    if t == "s":
        return f'<s>{"".join(_render(c) for c in ch)}</s>'

    if t == "code_inline":
        return f"<code>{html.escape(node.content)}</code>"

    if t == "fence":
        lang_raw = (node.info or "").split()[0] if (node.info or "").strip() else ""
        code = html.escape(node.content.rstrip("\n"))
        inner = (
            f'<code class="language-{html.escape(lang_raw)}">{code}</code>'
            if lang_raw
            else code
        )
        return f"<pre>{inner}</pre>\n\n"

    if t == "code_block":
        return f"<pre>{html.escape(node.content.rstrip())}</pre>\n\n"

    if t == "heading":
        level = max(1, min(6, len(node.markup))) if node.markup else 1
        inner = "".join(_render(c) for c in ch)
        return f"<h{level}>{inner}</h{level}>\n\n"

    if t == "paragraph":
        return f'{"".join(_render(c) for c in ch)}\n\n'

    if t == "bullet_list":
        return "".join(_render_list_item(c, prefix="•") for c in ch) + "\n"

    if t == "ordered_list":
        start = int(node.attrGet("start") or 1)
        return (
            "".join(
                _render_list_item(c, prefix=f"{int(c.info) if c.info else start + i}.")
                for i, c in enumerate(ch)
            )
            + "\n"
        )

    if t == "blockquote":
        inner = "".join(_render(c) for c in ch).strip()
        return f"<blockquote>{inner}</blockquote>\n\n"

    if t == "link":
        inner = "".join(_render(c) for c in ch)
        url = html.escape(str(node.attrGet("href") or ""), quote=True)
        return f'<a href="{url}">{inner}</a>'

    if t == "image":
        return html.escape(str(node.attrGet("alt") or ""))

    if t == "hr":
        return "<hr>\n"

    if t == "table":
        return f"<table>{''.join(_render(c) for c in ch)}</table>\n"

    if t in ("thead", "tbody"):
        return "".join(_render(c) for c in ch)

    if t == "tr":
        return f"<tr>{''.join(_render(c) for c in ch)}</tr>\n"

    if t == "th":
        return f"<th>{''.join(_render(c) for c in ch).strip()}</th>"

    if t == "td":
        return f"<td>{''.join(_render(c) for c in ch).strip()}</td>"

    if t in ("html_block", "html_inline"):
        return _passthrough_safe_tags(node.content)

    if ch:
        return "".join(_render(c) for c in ch)
    try:
        return html.escape(node.content)
    except AttributeError:
        return ""


def _render_list_item(node: SyntaxTreeNode, *, prefix: str) -> str:
    content_parts: list[str] = []
    nested: list[str] = []
    for child in node.children or []:
        if child.type in ("bullet_list", "ordered_list"):
            nested.append(_render(child))
        elif child.type == "paragraph":
            content_parts.append("".join(_render(c) for c in child.children or []))
        else:
            content_parts.append(_render(child))
    content = "".join(content_parts).strip()
    return f"{prefix} {content}\n" + ("".join(nested) if nested else "")


def to_html(text: str) -> str:
    tokens = _md.parse(text)
    tree = SyntaxTreeNode(tokens)
    return _render(tree)


async def send_md(message: Message, text: str) -> None:
    """Sends Markdown as Telegram Rich Message HTML in chunks of ≤ TG_LIMIT.

    Falls back to plain text for any chunk that the parser rejects.
    """
    bot = message.bot
    if bot is None:
        return
    await send_md_to_chat(bot, message.chat.id, text)


async def send_md_to_chat(bot: Bot, chat_id: int, text: str) -> None:
    """Chat-id-bound twin of `send_md` — used for bot-initiated messages
    (hooks, notifications) that have no inbound `Message` to reply to.

    Tries sendRichMessage with HTML first; on parse failure falls back to
    plain text so the user never sees raw escape artifacts.
    """
    converted = to_html(text)
    sent_any = False
    for i in range(0, len(converted), TG_LIMIT):
        chunk = converted[i : i + TG_LIMIT]
        try:
            await bot.send_rich_message(
                chat_id=chat_id,
                rich_message=InputRichMessage(html=chunk),
            )
            sent_any = True
        except TelegramBadRequest:
            log.warning(
                "send_md_to_chat: Rich Message rejected for chat_id=%s — "
                "falling back to plain text (sent_any=%s)",
                chat_id,
                sent_any,
            )
            for j in range(0, len(text), TG_LIMIT):
                await bot.send_message(chat_id, text[j : j + TG_LIMIT], parse_mode=None)
            return


_AUDIO_EXT_BY_MIME = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".m4a",
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
}


def audio_filename(message: Message) -> str:
    """Pick a filename with an extension Groq can dispatch by."""
    if message.voice is not None:
        return "voice.ogg"
    audio = message.audio
    if audio is not None:
        if audio.file_name:
            return audio.file_name
        ext = _AUDIO_EXT_BY_MIME.get((audio.mime_type or "").lower(), ".ogg")
        return f"audio{ext}"
    return "audio.ogg"


def format_quote(text: str) -> str:
    """Wrap each line with a Markdown blockquote prefix."""
    lines = text.splitlines() or [""]
    return "\n".join(f"> {line}" if line else ">" for line in lines)
