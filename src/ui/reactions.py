"""Pick an emoji reaction for an incoming user message.

Rules and the default emoji come from the per-language i18n file
(`src/i18n/<lang>.json` keys: `reactions`, `default_reaction`).
This keeps keyword patterns and emoji choices language-specific, so an
English-language bot can react to "thanks".

Telegram restricts bots to a fixed set of free-emoji reactions; pick from
that whitelist when adding new entries.
"""

import logging
import re
from collections.abc import Iterable

from ..i18n import Translator

log = logging.getLogger(__name__)

FALLBACK_REACTION = "👀"


class ReactionPicker:
    """Compiles regex rules once; picks the first matching emoji per call."""

    def __init__(
        self,
        rules: Iterable[tuple[re.Pattern[str], str]],
        default: str = FALLBACK_REACTION,
    ) -> None:
        """Store the ordered (pattern, emoji) rules and the default emoji."""
        self._rules = list(rules)
        self._default = default

    def pick(self, text: str) -> str:
        """Return the first rule's emoji matching `text`, else the default."""
        if not text:
            return self._default
        haystack = text.lower()
        for pattern, emoji in self._rules:
            if pattern.search(haystack):
                return emoji
        return self._default

    @classmethod
    def from_translator(cls, translator: Translator) -> "ReactionPicker":
        """Build a picker from the translator's `reactions` rules, skipping invalid ones."""
        raw_rules = translator.get("reactions", []) or []
        compiled: list[tuple[re.Pattern[str], str]] = []
        for entry in raw_rules:
            try:
                pattern = re.compile(entry["pattern"])
                emoji = entry["emoji"]
            except (KeyError, TypeError, re.error) as e:
                log.warning(
                    "skipping invalid reaction rule %r: %s", entry, e
                )
                continue
            compiled.append((pattern, emoji))
        default = translator.get("default_reaction", FALLBACK_REACTION)
        return cls(compiled, default)
