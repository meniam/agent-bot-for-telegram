"""ReactionPicker: regex rule matching + Translator-based factory."""

import re

from src.i18n import Translator
from src.ui.reactions import FALLBACK_REACTION, ReactionPicker


def _picker(rules: list[tuple[str, str]], default: str = FALLBACK_REACTION) -> ReactionPicker:
    """Build a ReactionPicker from raw pattern/emoji rules."""
    compiled = [(re.compile(p), e) for p, e in rules]
    return ReactionPicker(compiled, default)


def test_first_matching_rule_wins() -> None:
    """The first matching rule determines the reaction."""
    p = _picker([("hello", "👋"), ("world", "🌍")])
    assert p.pick("hello world") == "👋"


def test_lowercases_haystack_for_matching() -> None:
    """Matching is case-insensitive on the input text."""
    p = _picker([("thanks", "🙏")])
    assert p.pick("THANKS!") == "🙏"


def test_returns_default_when_no_rule_matches() -> None:
    """The default reaction is returned when no rule matches."""
    p = _picker([("foo", "1")], default="❓")
    assert p.pick("bar") == "❓"


def test_empty_text_returns_default() -> None:
    """Empty text returns the default reaction."""
    p = _picker([("foo", "1")], default="🤷")
    assert p.pick("") == "🤷"


def test_from_translator_uses_lang_rules() -> None:
    """A picker built from a translator falls back when nothing matches."""
    tr = Translator("ru")
    p = ReactionPicker.from_translator(tr)
    # Should not raise and should fall back when no rule matches.
    result = p.pick("xyz-no-match-string-12345")
    assert isinstance(result, str)
    assert len(result) >= 1


def test_from_translator_skips_invalid_regex() -> None:
    """Invalid regex rules are skipped when building from a translator."""

    class _StubTranslator:
        """Translator stub returning fixed reaction rules."""

        def get(self, key: str, default: object = None) -> object:
            """Return canned reaction config for known keys."""
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
