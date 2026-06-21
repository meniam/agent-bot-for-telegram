"""Markdown → Telegram HTML conversion, chunked send, audio filename.

Pure helpers — no I/O state, no closures over per-bot config. Reusable
across bots.
"""

import html
import logging
import re
from urllib.parse import urlsplit

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, Message
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from ._inline_marks import (
    mark_postprocess,
    mark_tokenize,
    spoiler_postprocess,
    spoiler_tokenize,
)

log = logging.getLogger(__name__)

TG_LIMIT = 4000

_md = MarkdownIt()
_md.enable("strikethrough")
_md.enable("table")
_md.use(footnote_plugin)
_md.use(dollarmath_plugin)
_md.use(tasklists_plugin)
_md.inline.ruler.before("strikethrough", "spoiler", spoiler_tokenize)
_md.inline.ruler.before("strikethrough", "mark", mark_tokenize)
_md.inline.ruler2.after("strikethrough", "spoiler", spoiler_postprocess)
_md.inline.ruler2.after("strikethrough", "mark", mark_postprocess)
# `|` and `=` are not default inline terminators, so the text rule would
# otherwise swallow `||`/`==` before the spoiler/mark rules see them.
_md.inline.add_terminator_char("|")
_md.inline.add_terminator_char("=")

# Media kinds keyed by file extension (Telegram media blocks accept HTTP/HTTPS
# URLs only; type is inferred from the extension).
_IMG_EXT = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_VIDEO_EXT = frozenset({".mp4", ".mov", ".m4v", ".gif"})
_AUDIO_EXT = frozenset({".mp3", ".ogg", ".oga", ".m4a", ".wav", ".opus", ".flac"})

# Full set of HTML tag names Telegram renders in Rich Messages. Raw HTML the
# agent writes for features without a Markdown syntax (underline, pull quotes,
# maps, custom emoji, anchors, …) is passed through; everything else is escaped.
_SAFE_TAGS = frozenset({
    "a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "mark", "sub", "sup", "tg-spoiler",
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr",
    "ul", "ol", "li", "input",
    "blockquote", "aside", "cite", "footer",
    "table", "caption", "tr", "th", "td",
    "details", "summary",
    "figure", "figcaption", "img", "video", "audio",
    "tg-emoji", "tg-time", "tg-math", "tg-math-block",
    "tg-map", "tg-collage", "tg-slideshow", "tg-reference",
})

_TAG_NAME_RE = re.compile(r"</?\s*([a-zA-Z][a-zA-Z0-9-]*)")


def _passthrough_safe_tags(raw: str) -> str:
    """Escape plain text, keep whitelisted HTML tags, strip everything else."""
    parts: list[str] = []
    pos = 0
    for m in re.finditer(r"<[^>]+>", raw):
        parts.append(html.escape(raw[pos : m.start()]))
        tag = m.group(0)
        name_m = _TAG_NAME_RE.match(tag)
        if name_m and name_m.group(1).lower() in _SAFE_TAGS:
            parts.append(tag)
        pos = m.end()
    parts.append(html.escape(raw[pos:]))
    return "".join(parts)


def _media_block(node: SyntaxTreeNode) -> str | None:
    """Render an image node as a Telegram media block, or None if it is not a
    standalone HTTP(S) media URL we can map to img/video/audio."""
    src = str(node.attrGet("src") or "")
    scheme = urlsplit(src).scheme.lower()
    if scheme not in ("http", "https"):
        return None
    path = urlsplit(src).path.lower()
    ext = path[path.rfind(".") :] if "." in path else ""
    if ext in _IMG_EXT:
        media = f'<img src="{html.escape(src, quote=True)}"/>'
    elif ext in _VIDEO_EXT:
        media = f'<video src="{html.escape(src, quote=True)}"></video>'
    elif ext in _AUDIO_EXT:
        media = f'<audio src="{html.escape(src, quote=True)}"></audio>'
    else:
        return None
    caption = str(node.attrGet("title") or "").strip()
    if caption:
        return (
            f"<figure>{media}"
            f"<figcaption>{html.escape(caption)}</figcaption></figure>"
        )
    return media


def _cell_align(node: SyntaxTreeNode) -> str:
    """Map a GFM table cell's `style="text-align:..."` to an `align` attribute."""
    style = str(node.attrGet("style") or "")
    m = re.search(r"text-align:\s*(left|center|right)", style)
    return f' align="{m.group(1)}"' if m else ""


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

    if t == "spoiler":
        return f'<tg-spoiler>{"".join(_render(c) for c in ch)}</tg-spoiler>'

    if t == "mark":
        return f'<mark>{"".join(_render(c) for c in ch)}</mark>'

    if t == "math_inline":
        return f"<tg-math>{html.escape(node.content)}</tg-math>"

    if t == "math_block":
        return f"<tg-math-block>{html.escape(node.content.strip())}</tg-math-block>\n\n"

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
        return f"<ul>{''.join(_render_list_item(c) for c in ch)}</ul>\n\n"

    if t == "ordered_list":
        start = int(node.attrGet("start") or 1)
        attr = f' start="{start}"' if start != 1 else ""
        return f"<ol{attr}>{''.join(_render_list_item(c) for c in ch)}</ol>\n\n"

    if t == "blockquote":
        inner = "".join(_render(c) for c in ch).strip()
        return f"<blockquote>{inner}</blockquote>\n\n"

    if t == "link":
        inner = "".join(_render(c) for c in ch)
        url = html.escape(str(node.attrGet("href") or ""), quote=True)
        return f'<a href="{url}">{inner}</a>'

    if t == "image":
        block = _media_block(node)
        if block is not None:
            return block
        # Non-HTTP(S) or unknown media type: fall back to the alt text
        # (carried as the image node's children or `content`).
        alt = "".join(_render(c) for c in ch) if ch else html.escape(node.content)
        return alt

    if t == "hr":
        return "<hr>\n"

    if t == "table":
        return f"<table>{''.join(_render(c) for c in ch)}</table>\n"

    if t in ("thead", "tbody"):
        return "".join(_render(c) for c in ch)

    if t == "tr":
        return f"<tr>{''.join(_render(c) for c in ch)}</tr>\n"

    if t == "th":
        inner = "".join(_render(c) for c in ch).strip()
        return f"<th{_cell_align(node)}>{inner}</th>"

    if t == "td":
        inner = "".join(_render(c) for c in ch).strip()
        return f"<td{_cell_align(node)}>{inner}</td>"

    if t == "math_block":  # handled above; guard for tree variants
        return f"<tg-math-block>{html.escape(node.content.strip())}</tg-math-block>\n\n"

    if t == "footnote_ref":
        label = html.escape(str((node.meta or {}).get("label", "")))
        return f'<sup><a href="#fn-{label}">{label}</a></sup>'

    if t == "footnote_block":
        return _render_footnotes(ch)

    if t == "footnote_anchor":
        return ""  # back-reference marker — not shown

    if t in ("html_block", "html_inline"):
        raw = node.content
        if _TASK_CHECKBOX_RE.search(raw):
            checked = "checked" in raw.lower()
            return (
                '<input type="checkbox" checked>'
                if checked
                else '<input type="checkbox">'
            )
        return _passthrough_safe_tags(raw)

    if ch:
        return "".join(_render(c) for c in ch)
    try:
        return html.escape(node.content)
    except AttributeError:
        return ""


_TASK_CHECKBOX_RE = re.compile(r"task-list-item-checkbox", re.IGNORECASE)


def _render_list_item(node: SyntaxTreeNode) -> str:
    """Render a `<li>`, converting GFM task-list checkboxes to clean inputs."""
    parts: list[str] = []
    for child in node.children or []:
        if child.type in ("bullet_list", "ordered_list"):
            parts.append(_render(child))
        elif child.type == "paragraph":
            parts.append("".join(_render(c) for c in child.children or []))
        else:
            parts.append(_render(child))
    content = "".join(parts).strip()
    return f"<li>{content}</li>"


def _render_footnotes(footnotes: list[SyntaxTreeNode]) -> str:
    """Render the footnote definitions block as an anchored `<footer>`."""
    lines: list[str] = []
    for fn in footnotes:
        if fn.type != "footnote":
            continue
        label = html.escape(str((fn.meta or {}).get("label", "")))
        inner = "".join(
            "".join(_render(c) for c in child.children or [])
            if child.type == "paragraph"
            else _render(child)
            for child in fn.children or []
        ).strip()
        lines.append(f'<a name="fn-{label}"></a>{label}. {inner}')
    if not lines:
        return ""
    return "<footer>" + "<br>".join(lines) + "</footer>\n\n"


def to_html(text: str) -> str:
    tokens = _md.parse(text)
    tree = SyntaxTreeNode(tokens)
    return _render(tree)


# Rich Messages render real HTML, so literal newlines collapse to whitespace.
# `<pre>` keeps newlines significant; `<table>` rows/cells own their layout.
_PRE_OR_TABLE_RE = re.compile(r"<pre>.*?</pre>|<table>.*?</table>", re.DOTALL)


def to_rich_html(text: str) -> str:
    """`to_html` adapted for sendRichMessage: line breaks become `<br>`.

    Unlike classic `parse_mode=HTML` (where `\\n` is a newline), Rich Message
    HTML is rendered as a document — bare newlines turn into spaces. Convert
    them to `<br>`, except inside `<pre>` (newlines are meaningful) and
    `<table>` (drop the structural newlines between rows/cells).
    """
    converted = to_html(text)
    out: list[str] = []
    pos = 0
    for m in _PRE_OR_TABLE_RE.finditer(converted):
        out.append(converted[pos : m.start()].replace("\n", "<br>"))
        seg = m.group(0)
        out.append(seg.replace("\n", "") if seg.startswith("<table>") else seg)
        pos = m.end()
    out.append(converted[pos:].replace("\n", "<br>"))
    return "".join(out)


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
    converted = to_rich_html(text)
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
