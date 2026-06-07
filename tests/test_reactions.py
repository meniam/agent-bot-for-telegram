"""ReactionPicker: regex rule matching + Translator-based factory."""

import re

from src.i18n import Translator
from src.ui.reactions import FALLBACK_REACTION, ReactionPicker


def _picker(rules: list[tuple[str, str]], default: str = FALLBACK_REACTION) -> ReactionPicker:
    compiled = [(re.compile(p), e) for p, e in rules]
    return ReactionPicker(compiled, default)


def test_first_matching_rule_wins() -> None:
    p = _picker([("hello", "👋"), ("world", "🌍")])
    assert p.pick("hello world") == "👋"


def test_lowercases_haystack_for_matching() -> None:
    p = _picker([("thanks", "🙏")])
    assert p.pick("THANKS!") == "🙏"


def test_returns_default_when_no_rule_matches() -> None:
    p = _picker([("foo", "1")], default="❓")
    assert p.pick("bar") == "❓"


def test_empty_text_returns_default() -> None:
    p = _picker([("foo", "1")], default="🤷")
    assert p.pick("") == "🤷"


def test_from_translator_uses_lang_rules() -> None:
    tr = Translator("ru")
    p = ReactionPicker.from_translator(tr)
    # Should not raise and should fall back when no rule matches.
    result = p.pick("xyz-no-match-string-12345")
    assert isinstance(result, str)
    assert len(result) >= 1


def test_from_translator_skips_invalid_regex() -> None:
    class _StubTranslator:
        def get(self, key: str, default: object = None) -> object:
            if key == "reactions":
                return [
                    {"pattern": "[invalid(regex", "emoji": "💥"},
                    {"pattern": "hi", "emoji": "👋"},
                ]
            if key == "default_reaction":
                return "🤷"
            return default

    p = ReactionPicker.from_translator(_StubTranslator())  # type: ignore[arg-type]
    assert p.pick("hi there") == "👋"
    assert p.pick("xyz qrs") == "🤷"
