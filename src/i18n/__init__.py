"""Translations: per-bot language via Translator(lang)."""

import json
from pathlib import Path
from typing import Any

# Language selected when a bot specifies none / an unknown one.
DEFAULT_LANG = "ru"
# Source of per-key fallback strings for any locale missing a key.
FALLBACK_LANG = "en"
_DIR = Path(__file__).resolve().parent


def available_languages() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.json"))


class Translator:
    def __init__(self, lang: str = DEFAULT_LANG) -> None:
        self.lang = lang
        path = _DIR / f"{lang}.json"
        if not path.exists():
            path = _DIR / f"{DEFAULT_LANG}.json"
            self.lang = DEFAULT_LANG
        with path.open(encoding="utf-8") as f:
            self._strings: dict[str, str] = json.load(f)
        # Per-key fallback to FALLBACK_LANG so newly added keys do not surface
        # as raw identifiers in not-yet-translated language files.
        self._fallback: dict[str, str] = {}
        if self.lang != FALLBACK_LANG:
            fallback_path = _DIR / f"{FALLBACK_LANG}.json"
            if fallback_path.exists():
                with fallback_path.open(encoding="utf-8") as f:
                    self._fallback = json.load(f)

    def t(self, key: str, **kwargs: object) -> str:
        s = self._strings.get(key)
        if s is None:
            s = self._fallback.get(key, key)
        if kwargs:
            try:
                return s.format(**kwargs)
            except (KeyError, IndexError):
                return s
        return s

    def get(self, key: str, default: Any = None) -> Any:
        """Return the raw value for a key (list/dict/str).

        Returns Any because JSON values are heterogeneous (the `reactions`
        key is a list of dicts, `default_reaction` is a string, etc.).
        Callers are expected to know the shape of the key they request.
        """
        if key in self._strings:
            return self._strings[key]
        if key in self._fallback:
            return self._fallback[key]
        return default
