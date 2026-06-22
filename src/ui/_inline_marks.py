"""Paired inline-marker rules for markdown-it-py.

markdown-it-py ships no rule for Telegram's `||spoiler||` or `==marked==`
syntaxes, so we add them, modelled on the built-in strikethrough (`~~`)
delimiter logic — swapping the marker character and the output node type.

Each rule registers an open/close token pair whose `type` becomes the
`SyntaxTreeNode.type` the renderer dispatches on (e.g. `spoiler`, `mark`).

Register with::

    md.inline.ruler.before("strikethrough", "spoiler", spoiler_tokenize)
    md.inline.ruler2.after("strikethrough", "spoiler", spoiler_postprocess)
    md.inline.add_terminator_char("|")

The terminator char is required: `|` and `=` are not default inline
terminators, so the `text` rule would otherwise swallow the marker run before
our rule sees it.
"""

from __future__ import annotations

from collections.abc import Callable

from markdown_it.rules_inline.state_inline import Delimiter, StateInline

_RuleFn = Callable[[StateInline, bool], bool]
_PostFn = Callable[[StateInline], None]


def _make_rule(marker: str, node_type: str, tag: str) -> tuple[_RuleFn, _PostFn]:
    """Build a (tokenize, postprocess) rule pair for a doubled ``marker`` syntax.

    Models the built-in strikethrough delimiter logic, emitting
    ``{node_type}_open``/``_close`` tokens with HTML ``tag`` for each matched
    ``marker``-doubled span.
    """
    marker_code = ord(marker)

    def tokenize(state: StateInline, silent: bool) -> bool:
        """Scan a doubled-marker run and push text tokens plus delimiters."""
        if silent:
            return False

        start = state.pos
        if state.src[start] != marker:
            return False

        scanned = state.scanDelims(state.pos, True)
        length = scanned.length

        # The marker must be doubled; a lone marker is literal text (keeps GFM
        # table pipes and `=` signs intact).
        if length < 2:
            return False

        if length % 2:
            token = state.push("text", "", 0)
            token.content = marker
            length -= 1

        i = 0
        while i < length:
            token = state.push("text", "", 0)
            token.content = marker + marker
            state.delimiters.append(
                Delimiter(
                    marker=marker_code,
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

    def _post(state: StateInline, delimiters: list[Delimiter]) -> None:
        """Turn matched delimiter pairs into open/close tokens; fix lone markers."""
        lone_markers: list[int] = []
        maximum = len(delimiters)

        i = 0
        while i < maximum:
            start_delim = delimiters[i]
            if start_delim.marker != marker_code:
                i += 1
                continue
            if start_delim.end == -1:
                i += 1
                continue

            end_delim = delimiters[start_delim.end]
            markup = state.tokens[start_delim.token].content

            token = state.tokens[start_delim.token]
            token.type = f"{node_type}_open"
            token.tag = tag
            token.nesting = 1
            token.markup = markup
            token.content = ""

            token = state.tokens[end_delim.token]
            token.type = f"{node_type}_close"
            token.tag = tag
            token.nesting = -1
            token.markup = markup
            token.content = ""

            if (
                state.tokens[end_delim.token - 1].type == "text"
                and state.tokens[end_delim.token - 1].content == marker
            ):
                lone_markers.append(end_delim.token - 1)

            i += 1

        close_type = f"{node_type}_close"
        while lone_markers:
            i = lone_markers.pop()
            j = i + 1
            while (j < len(state.tokens)) and (
                state.tokens[j].type == close_type
            ):
                j += 1
            j -= 1
            if i != j:
                state.tokens[i], state.tokens[j] = (
                    state.tokens[j],
                    state.tokens[i],
                )

    def postprocess(state: StateInline) -> None:
        """Run ``_post`` over the top-level and per-token delimiter lists."""
        tokens_meta = state.tokens_meta
        maximum = len(state.tokens_meta)
        _post(state, state.delimiters)

        curr = 0
        while curr < maximum:
            try:
                curr_meta = tokens_meta[curr]
            except IndexError:
                pass
            else:
                if curr_meta and "delimiters" in curr_meta:
                    _post(state, curr_meta["delimiters"])
            curr += 1

    return tokenize, postprocess


spoiler_tokenize, spoiler_postprocess = _make_rule("|", "spoiler", "tg-spoiler")
mark_tokenize, mark_postprocess = _make_rule("=", "mark", "mark")
