"""Inline `||spoiler||` rule for markdown-it-py.

Telegram renders spoilers via the `<tg-spoiler>` tag (or `||...||` in
MarkdownV2). markdown-it-py has no spoiler rule, so we add one modelled on the
built-in strikethrough (`~~`) delimiter logic, swapping the marker to `|` and
the output tag to `tg-spoiler`.

Register with::

    md.inline.ruler.before("strikethrough", "spoiler", spoiler_tokenize)
    md.inline.ruler2.after("strikethrough", "spoiler", spoiler_postprocess)
"""

from __future__ import annotations

from markdown_it.rules_inline.state_inline import Delimiter, StateInline

_MARKER = "|"
_MARKER_CODE = 0x7C  # ord("|")


def spoiler_tokenize(state: StateInline, silent: bool) -> bool:
    """Insert each `||` marker as a text token and register a delimiter."""
    if silent:
        return False

    start = state.pos
    if state.src[start] != _MARKER:
        return False

    scanned = state.scanDelims(state.pos, True)
    length = scanned.length

    # Spoilers require a doubled marker; a lone `|` is literal text (and keeps
    # GFM table pipes intact).
    if length < 2:
        return False

    if length % 2:
        token = state.push("text", "", 0)
        token.content = _MARKER
        length -= 1

    i = 0
    while i < length:
        token = state.push("text", "", 0)
        token.content = _MARKER + _MARKER
        state.delimiters.append(
            Delimiter(
                marker=_MARKER_CODE,
                length=0,  # disable "rule of 3" length checks
                token=len(state.tokens) - 1,
                end=-1,
                open=scanned.can_open,
                close=scanned.can_close,
            )
        )
        i += 2

    state.pos += scanned.length
    return True


def _post_process(state: StateInline, delimiters: list[Delimiter]) -> None:
    lone_markers: list[int] = []
    maximum = len(delimiters)

    i = 0
    while i < maximum:
        start_delim = delimiters[i]
        if start_delim.marker != _MARKER_CODE:
            i += 1
            continue
        if start_delim.end == -1:
            i += 1
            continue

        end_delim = delimiters[start_delim.end]
        markup = state.tokens[start_delim.token].content

        token = state.tokens[start_delim.token]
        token.type = "spoiler_open"
        token.tag = "tg-spoiler"
        token.nesting = 1
        token.markup = markup
        token.content = ""

        token = state.tokens[end_delim.token]
        token.type = "spoiler_close"
        token.tag = "tg-spoiler"
        token.nesting = -1
        token.markup = markup
        token.content = ""

        if (
            state.tokens[end_delim.token - 1].type == "text"
            and state.tokens[end_delim.token - 1].content == _MARKER
        ):
            lone_markers.append(end_delim.token - 1)

        i += 1

    while lone_markers:
        i = lone_markers.pop()
        j = i + 1
        while (j < len(state.tokens)) and (
            state.tokens[j].type == "spoiler_close"
        ):
            j += 1
        j -= 1
        if i != j:
            state.tokens[i], state.tokens[j] = state.tokens[j], state.tokens[i]


def spoiler_postprocess(state: StateInline) -> None:
    """Replace matched delimiter text tokens with spoiler open/close tags."""
    tokens_meta = state.tokens_meta
    maximum = len(state.tokens_meta)
    _post_process(state, state.delimiters)

    curr = 0
    while curr < maximum:
        try:
            curr_meta = tokens_meta[curr]
        except IndexError:
            pass
        else:
            if curr_meta and "delimiters" in curr_meta:
                _post_process(state, curr_meta["delimiters"])
        curr += 1
