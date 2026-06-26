"""Markdown → Telegram HTML conversion and audio filename helpers."""

from unittest.mock import MagicMock

from src.ui.markdown import (
    audio_filename,
    format_quote,
    to_html,
    to_rich_html,
)

# --- to_rich_html: line breaks for sendRichMessage ---

def test_rich_html_paragraphs_become_br() -> None:
    """Render a blank-line paragraph break as a double ``<br>``."""
    assert to_rich_html("a\n\nb") == "a<br><br>b"


def test_rich_html_softbreak_becomes_br() -> None:
    """Render a single soft line break as one ``<br>``."""
    assert to_rich_html("a\nb") == "a<br>b"


def test_rich_html_keeps_pre_newlines() -> None:
    """Preserve literal newlines inside a code block."""
    out = to_rich_html("```\nfoo\nbar\n```")
    assert "<pre>foo\nbar</pre>" in out


def test_rich_html_strips_table_newlines() -> None:
    """Emit a table without newlines or ``<br>`` separators."""
    out = to_rich_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "\n" not in out
    assert "<br>" not in out


def test_rich_html_gallery_has_no_inner_br() -> None:
    """A gallery's media must not be separated by ``<br>`` (it corrupts it)."""
    raw = (
        "<tg-slideshow>\n"
        '<img src="https://h/a.jpg"/>\n'
        '<img src="https://h/b.jpg"/>\n'
        "<figcaption>Cap</figcaption>\n"
        "</tg-slideshow>"
    )
    out = to_rich_html(raw)
    assert "<br>" not in out
    assert (
        out
        == "<tg-slideshow>"
        '<img src="https://h/a.jpg"/>'
        '<img src="https://h/b.jpg"/>'
        "<figcaption>Cap</figcaption></tg-slideshow>"
    )


def test_rich_html_standalone_images_have_no_br() -> None:
    """Consecutive standalone media blocks render without ``<br>`` padding."""
    out = to_rich_html("![A](https://h/a.jpg)\n\n![B](https://h/b.jpg)")
    assert "<br>" not in out
    assert out == '<img src="https://h/a.jpg"/><img src="https://h/b.jpg"/>'


# --- to_html: inline formatting ---

def test_bold() -> None:
    """Convert ``**text**`` to a ``<b>`` element."""
    assert to_html("**text**") == "<b>text</b>"


def test_italic() -> None:
    """Convert ``_text_`` to an ``<i>`` element."""
    assert to_html("_text_") == "<i>text</i>"


def test_strikethrough() -> None:
    """Convert ``~~text~~`` to an ``<s>`` element."""
    assert to_html("~~text~~") == "<s>text</s>"


def test_inline_code() -> None:
    """Convert backtick spans to a ``<code>`` element."""
    assert to_html("`code`") == "<code>code</code>"


def test_inline_code_escapes_html() -> None:
    """Escape HTML special characters inside inline code."""
    assert to_html("`<b>`") == "<code>&lt;b&gt;</code>"


# --- to_html: code blocks ---

def test_fenced_code_block_with_lang() -> None:
    """Tag a fenced block's language as a ``language-*`` class."""
    out = to_html("```python\nfoo()\n```")
    assert '<pre><code class="language-python">foo()</code></pre>' in out


def test_fenced_code_block_no_lang() -> None:
    """Render a language-less fenced block as a bare ``<pre>``."""
    out = to_html("```\nfoo\n```")
    assert "<pre>foo</pre>" in out


def test_code_block_escapes_html() -> None:
    """Escape HTML special characters inside a fenced block."""
    out = to_html("```\n<b>bold</b>\n```")
    assert "&lt;b&gt;" in out


# --- to_html: lists ---

def test_bullet_list_html() -> None:
    """Render a bullet list as ``<ul>`` with ``<li>`` items."""
    out = to_html("- one\n- two")
    assert out.startswith("<ul>")
    assert "<li>one</li>" in out
    assert "<li>two</li>" in out


def test_ordered_list_html() -> None:
    """Render an ordered list as ``<ol>`` with ``<li>`` items."""
    out = to_html("1. first\n2. second")
    assert "<ol>" in out
    assert "<li>first</li>" in out


def test_ordered_list_custom_start() -> None:
    """Carry a non-1 start value into the ``<ol start>`` attribute."""
    out = to_html("3. third\n4. fourth")
    assert '<ol start="3">' in out
    assert "<li>third</li>" in out


def test_nested_list() -> None:
    """Nest a sublist inside its parent list item."""
    out = to_html("- a\n  - a1\n- b")
    assert "<li>a<ul><li>a1</li></ul></li>" in out


def test_task_list() -> None:
    """Render task-list items as checkbox inputs with checked state."""
    out = to_html("- [ ] todo\n- [x] done")
    assert '<input type="checkbox"> todo' in out
    assert '<input type="checkbox" checked> done' in out


def test_list_with_bold_item() -> None:
    """Apply inline formatting inside a list item."""
    out = to_html("- **bold** item")
    assert "<b>bold</b>" in out


# --- to_html: headings ---

def test_heading_h1() -> None:
    """Render a level-1 heading as ``<h1>``."""
    out = to_html("# Hello")
    assert "<h1>Hello</h1>" in out


def test_heading_h2() -> None:
    """Render a level-2 heading as ``<h2>``."""
    out = to_html("## Second")
    assert "<h2>Second</h2>" in out


def test_hr() -> None:
    """Render a thematic break as ``<hr>``."""
    assert "<hr>" in to_html("---")


def test_table() -> None:
    """Render a pipe table with header and data cells."""
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = to_html(md)
    assert "<table>" in result
    assert "<th>" in result
    assert "<td>1</td>" in result


def test_safe_html_mark() -> None:
    """Pass an allowlisted ``<mark>`` tag through unchanged."""
    out = to_html("<mark>important</mark>")
    assert "<mark>important</mark>" in out


def test_unsafe_html_stripped() -> None:
    """Strip a disallowed ``<script>`` tag from the output."""
    assert "<script>" not in to_html("<script>alert(1)</script>")


def test_spoiler() -> None:
    """Convert ``||text||`` to a ``<tg-spoiler>`` element."""
    assert to_html("||hidden||") == "<tg-spoiler>hidden</tg-spoiler>"


def test_spoiler_inline_with_text() -> None:
    """Wrap only the spoiler span, leaving surrounding text intact."""
    out = to_html("before ||secret|| after")
    assert out == "before <tg-spoiler>secret</tg-spoiler> after"


def test_spoiler_nested_bold() -> None:
    """Apply inline formatting inside a spoiler span."""
    assert to_html("||**x**||") == "<tg-spoiler><b>x</b></tg-spoiler>"


def test_single_pipe_literal() -> None:
    """Leave a lone pipe as a literal character."""
    assert to_html("a | b") == "a | b"


def test_unclosed_spoiler_literal() -> None:
    """Leave an unclosed spoiler marker as literal text."""
    assert to_html("|| open") == "|| open"


def test_table_not_broken_by_spoiler_rule() -> None:
    """Keep table rendering intact despite the spoiler rule."""
    result = to_html("| A | B |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in result
    assert "<td>1</td>" in result


def test_table_alignment() -> None:
    """Carry column alignment into ``align`` attributes."""
    out = to_html("| A | B |\n|:--|--:|\n| 1 | 2 |")
    assert '<th align="left">A</th>' in out
    assert '<th align="right">B</th>' in out
    assert '<td align="right">2</td>' in out


# --- to_html: mark / math / footnotes / media ---

def test_mark_syntax() -> None:
    """Convert ``==text==`` to a ``<mark>`` element."""
    assert to_html("==important==") == "<mark>important</mark>"


def test_mark_inline_with_text() -> None:
    """Wrap only the highlight span, leaving surrounding text intact."""
    assert to_html("a ==b== c") == "a <mark>b</mark> c"


def test_single_equals_literal() -> None:
    """Leave a lone equals sign as a literal character."""
    assert to_html("x = y") == "x = y"


def test_math_inline() -> None:
    """Convert ``$...$`` to an inline ``<tg-math>`` element."""
    assert to_html("e $E=mc^2$ x") == "e <tg-math>E=mc^2</tg-math> x"


def test_math_block() -> None:
    """Convert ``$$...$$`` to a ``<tg-math-block>`` element."""
    out = to_html("before\n\n$$E=mc^2$$\n\nafter")
    assert "<tg-math-block>E=mc^2</tg-math-block>" in out


def test_footnote() -> None:
    """Render a footnote reference and its definition."""
    out = to_html("text[^1] more\n\n[^1]: the definition")
    assert '<sup><a href="#fn-1">1</a></sup>' in out
    assert '<footer><a name="fn-1"></a>1. the definition</footer>' in out


def test_image_media_block() -> None:
    """Render a remote image with its title as a figure caption."""
    out = to_html('![cap](https://host/p.jpg "Caption")')
    assert '<img src="https://host/p.jpg"/>' in out
    assert "<figcaption>Caption</figcaption>" in out


def test_video_media_block() -> None:
    """Render a remote ``.mp4`` source as a ``<video>`` element."""
    assert '<video src="https://host/v.mp4"></video>' in to_html(
        "![](https://host/v.mp4)"
    )


def test_audio_media_block() -> None:
    """Render a remote ``.mp3`` source as an ``<audio>`` element."""
    assert '<audio src="https://host/a.mp3"></audio>' in to_html(
        "![](https://host/a.mp3)"
    )


def test_local_image_falls_back_to_alt() -> None:
    """Replace a non-remote image with its alt text."""
    assert to_html("![alt text](photo.jpg)") == "alt text"


def test_underline_passthrough() -> None:
    """Pass an allowlisted ``<u>`` tag through unchanged."""
    assert to_html("<u>x</u>") == "<u>x</u>"


def test_aside_passthrough() -> None:
    """Pass allowlisted ``<aside>`` and ``<cite>`` tags through unchanged."""
    out = to_html("<aside>quote<cite>me</cite></aside>")
    assert out == "<aside>quote<cite>me</cite></aside>"


# --- to_html: blockquote ---

def test_blockquote() -> None:
    """Render a quoted line as a ``<blockquote>`` element."""
    out = to_html("> quote text")
    assert "<blockquote>quote text</blockquote>" in out


# --- to_html: links ---

def test_link() -> None:
    """Render a Markdown link as an ``<a href>`` element."""
    out = to_html("[click](https://example.com)")
    assert '<a href="https://example.com">click</a>' in out


def test_link_escapes_special_chars_in_text() -> None:
    """Escape HTML special characters within link text."""
    out = to_html("[a & b](https://x.com)")
    assert "a &amp; b" in out


# --- to_html: plain text escaping ---

def test_html_special_chars_escaped() -> None:
    """Escape ``&``, ``<`` and ``>`` in plain text."""
    out = to_html("a & b < c > d")
    assert "&amp;" in out
    assert "&lt;" in out
    assert "&gt;" in out


# --- format_quote ---

def test_format_quote_prefixes_each_line() -> None:
    """Prefix every line of the input with ``> ``."""
    out = format_quote("first\nsecond")
    assert out == "> first\n> second"


def test_format_quote_handles_empty_lines() -> None:
    """Prefix a blank line with a bare ``>`` marker."""
    out = format_quote("a\n\nb")
    assert out == "> a\n>\n> b"


def test_format_quote_empty_string() -> None:
    """Quote an empty string as a single ``>`` marker."""
    assert format_quote("") == ">"


# --- audio_filename ---

def test_audio_filename_voice() -> None:
    """Name a voice message ``voice.ogg``."""
    msg = MagicMock()
    msg.voice = object()
    assert audio_filename(msg) == "voice.ogg"


def test_audio_filename_audio_with_name() -> None:
    """Use the audio attachment's own file name when present."""
    msg = MagicMock()
    msg.voice = None
    msg.audio.file_name = "song.mp3"
    assert audio_filename(msg) == "song.mp3"


def test_audio_filename_audio_by_mime() -> None:
    """Derive the extension from the MIME type when no name is set."""
    msg = MagicMock()
    msg.voice = None
    msg.audio.file_name = None
    msg.audio.mime_type = "audio/mpeg"
    assert audio_filename(msg) == "audio.mp3"


def test_audio_filename_unknown_mime_defaults_to_ogg() -> None:
    """Default to ``audio.ogg`` for an unrecognised MIME type."""
    msg = MagicMock()
    msg.voice = None
    msg.audio.file_name = None
    msg.audio.mime_type = "audio/totally-made-up"
    assert audio_filename(msg) == "audio.ogg"


def test_audio_filename_no_audio_no_voice() -> None:
    """Default to ``audio.ogg`` when neither voice nor audio is set."""
    msg = MagicMock()
    msg.voice = None
    msg.audio = None
    assert audio_filename(msg) == "audio.ogg"
