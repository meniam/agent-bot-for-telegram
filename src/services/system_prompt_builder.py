"""Compose the agent system prompt: built-in contract + bot-specific text."""

from pathlib import Path

from ..config import BotConfig
from ..i18n import Translator

BUILTIN_SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "system_prompt.md"
)


def load_builtin_system_prompt() -> str:
    """Read and return the built-in system-prompt contract file."""
    return BUILTIN_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def compose_system_prompt(cfg: BotConfig, tr: Translator) -> str:
    """Combine the built-in contract with the bot-specific prompt text."""
    bot_prompt = (cfg.system_prompt or tr.t("default_system_prompt")).strip()
    builtin_prompt = load_builtin_system_prompt()
    if not bot_prompt:
        return builtin_prompt
    return f"{builtin_prompt}\n\nBot-specific instructions:\n\n{bot_prompt}"
