"""Markdown → Telegram HTML conversion and audio filename helpers."""

from unittest.mock import MagicMock

from src.ui.markdown import audio_filename, format_quote, to_html

# --- to_html: inline formatting ---

def test_bold() -> None:
    assert to_html("**text**") == "<b>text</b>"


def test_italic() -> None:
    assert to_html("_text_") == "<i>text</i>"


def test_strikethrough() -> None:
    assert to_html("~~text~~") == "<s>text</s>"


def test_inline_code() -> None:
    assert to_html("`code`") == "<code>code</code>"


def test_inline_code_escapes_html() -> None:
    assert to_html("`<b>`") == "<code>&lt;b&gt;</code>"


# --- to_html: code blocks ---

def test_fenced_code_block_with_lang() -> None:
    out = to_html("```python\nfoo()\n```")
    assert '<pre><code class="language-python">foo()</code></pre>' in out


def test_fenced_code_block_no_lang() -> None:
    out = to_html("```\nfoo\n```")
    assert "<pre>foo</pre>" in out


def test_code_block_escapes_html() -> None:
    out = to_html("```\n<b>bold</b>\n```")
    assert "&lt;b&gt;" in out


# --- to_html: lists ---

def test_bullet_list_blank_line_after() -> None:
    out = to_html("- one\n- two\n\nParagraph")
    assert "• two\n\nParagraph" in out


def test_ordered_list_blank_line_after() -> None:
    out = to_html("1. first\n2. second\n\nParagraph")
    assert "2. second\n\nParagraph" in out


def test_ordered_list_custom_start() -> None:
    out = to_html("3. third\n4. fourth")
    assert "3." in out
    assert "4." in out


def test_list_with_bold_item() -> None:
    out = to_html("- **bold** item")
    assert "<b>bold</b>" in out


# --- to_html: headings ---

def test_heading_becomes_bold() -> None:
    out = to_html("# Hello")
    assert "<b>Hello</b>" in out


# --- to_html: blockquote ---

def test_blockquote() -> None:
    out = to_html("> quote text")
    assert "<blockquote>quote text</blockquote>" in out


# --- to_html: links ---

def test_link() -> None:
    out = to_html("[click](https://example.com)")
    assert '<a href="https://example.com">click</a>' in out


def test_link_escapes_special_chars_in_text() -> None:
    out = to_html("[a & b](https://x.com)")
    assert "a &amp; b" in out


# --- to_html: plain text escaping ---

def test_html_special_chars_escaped() -> None:
    out = to_html("a & b < c > d")
    assert "&amp;" in out
    assert "&lt;" in out
    assert "&gt;" in out


# --- format_quote ---

def test_format_quote_prefixes_each_line() -> None:
    out = format_quote("first\nsecond")
    assert out == "> first\n> second"


def test_format_quote_handles_empty_lines() -> None:
    out = format_quote("a\n\nb")
    assert out == "> a\n>\n> b"


def test_format_quote_empty_string() -> None:
    assert format_quote("") == ">"


# --- audio_filename ---

def test_audio_filename_voice() -> None:
    msg = MagicMock()
    msg.voice = object()
    assert audio_filename(msg) == "voice.ogg"


def test_audio_filename_audio_with_name() -> None:
    msg = MagicMock()
    msg.voice = None
    msg.audio.file_name = "song.mp3"
    assert audio_filename(msg) == "song.mp3"


def test_audio_filename_audio_by_mime() -> None:
    msg = MagicMock()
    msg.voice = None
    msg.audio.file_name = None
    msg.audio.mime_type = "audio/mpeg"
    assert audio_filename(msg) == "audio.mp3"


def test_audio_filename_unknown_mime_defaults_to_ogg() -> None:
    msg = MagicMock()
    msg.voice = None
    msg.audio.file_name = None
    msg.audio.mime_type = "audio/totally-made-up"
    assert audio_filename(msg) == "audio.ogg"


def test_audio_filename_no_audio_no_voice() -> None:
    msg = MagicMock()
    msg.voice = None
    msg.audio = None
    assert audio_filename(msg) == "audio.ogg"
