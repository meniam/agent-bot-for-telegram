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
    assert to_rich_html("a\n\nb") == "a<br><br>b"


def test_rich_html_softbreak_becomes_br() -> None:
    assert to_rich_html("a\nb") == "a<br>b"


def test_rich_html_keeps_pre_newlines() -> None:
    out = to_rich_html("```\nfoo\nbar\n```")
    assert "<pre>foo\nbar</pre>" in out


def test_rich_html_strips_table_newlines() -> None:
    out = to_rich_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "\n" not in out
    assert "<br>" not in out


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

def test_bullet_list_html() -> None:
    out = to_html("- one\n- two")
    assert out.startswith("<ul>")
    assert "<li>one</li>" in out
    assert "<li>two</li>" in out


def test_ordered_list_html() -> None:
    out = to_html("1. first\n2. second")
    assert "<ol>" in out
    assert "<li>first</li>" in out


def test_ordered_list_custom_start() -> None:
    out = to_html("3. third\n4. fourth")
    assert '<ol start="3">' in out
    assert "<li>third</li>" in out


def test_nested_list() -> None:
    out = to_html("- a\n  - a1\n- b")
    assert "<li>a<ul><li>a1</li></ul></li>" in out


def test_task_list() -> None:
    out = to_html("- [ ] todo\n- [x] done")
    assert '<input type="checkbox"> todo' in out
    assert '<input type="checkbox" checked> done' in out


def test_list_with_bold_item() -> None:
    out = to_html("- **bold** item")
    assert "<b>bold</b>" in out


# --- to_html: headings ---

def test_heading_h1() -> None:
    out = to_html("# Hello")
    assert "<h1>Hello</h1>" in out


def test_heading_h2() -> None:
    out = to_html("## Second")
    assert "<h2>Second</h2>" in out


def test_hr() -> None:
    assert "<hr>" in to_html("---")


def test_table() -> None:
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = to_html(md)
    assert "<table>" in result
    assert "<th>" in result
    assert "<td>1</td>" in result


def test_safe_html_mark() -> None:
    out = to_html("<mark>important</mark>")
    assert "<mark>important</mark>" in out


def test_unsafe_html_stripped() -> None:
    assert "<script>" not in to_html("<script>alert(1)</script>")


def test_spoiler() -> None:
    assert to_html("||hidden||") == "<tg-spoiler>hidden</tg-spoiler>"


def test_spoiler_inline_with_text() -> None:
    out = to_html("before ||secret|| after")
    assert out == "before <tg-spoiler>secret</tg-spoiler> after"


def test_spoiler_nested_bold() -> None:
    assert to_html("||**x**||") == "<tg-spoiler><b>x</b></tg-spoiler>"


def test_single_pipe_literal() -> None:
    assert to_html("a | b") == "a | b"


def test_unclosed_spoiler_literal() -> None:
    assert to_html("|| open") == "|| open"


def test_table_not_broken_by_spoiler_rule() -> None:
    result = to_html("| A | B |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in result
    assert "<td>1</td>" in result


def test_table_alignment() -> None:
    out = to_html("| A | B |\n|:--|--:|\n| 1 | 2 |")
    assert '<th align="left">A</th>' in out
    assert '<th align="right">B</th>' in out
    assert '<td align="right">2</td>' in out


# --- to_html: mark / math / footnotes / media ---

def test_mark_syntax() -> None:
    assert to_html("==important==") == "<mark>important</mark>"


def test_mark_inline_with_text() -> None:
    assert to_html("a ==b== c") == "a <mark>b</mark> c"


def test_single_equals_literal() -> None:
    assert to_html("x = y") == "x = y"


def test_math_inline() -> None:
    assert to_html("e $E=mc^2$ x") == "e <tg-math>E=mc^2</tg-math> x"


def test_math_block() -> None:
    out = to_html("before\n\n$$E=mc^2$$\n\nafter")
    assert "<tg-math-block>E=mc^2</tg-math-block>" in out


def test_footnote() -> None:
    out = to_html("text[^1] more\n\n[^1]: the definition")
    assert '<sup><a href="#fn-1">1</a></sup>' in out
    assert '<footer><a name="fn-1"></a>1. the definition</footer>' in out


def test_image_media_block() -> None:
    out = to_html('![cap](https://host/p.jpg "Caption")')
    assert '<img src="https://host/p.jpg"/>' in out
    assert "<figcaption>Caption</figcaption>" in out


def test_video_media_block() -> None:
    assert '<video src="https://host/v.mp4"></video>' in to_html(
        "![](https://host/v.mp4)"
    )


def test_audio_media_block() -> None:
    assert '<audio src="https://host/a.mp3"></audio>' in to_html(
        "![](https://host/a.mp3)"
    )


def test_local_image_falls_back_to_alt() -> None:
    assert to_html("![alt text](photo.jpg)") == "alt text"


def test_underline_passthrough() -> None:
    assert to_html("<u>x</u>") == "<u>x</u>"


def test_aside_passthrough() -> None:
    out = to_html("<aside>quote<cite>me</cite></aside>")
    assert out == "<aside>quote<cite>me</cite></aside>"


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
