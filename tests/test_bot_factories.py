"""Pure factories in `src/bot.py`: ACL builder + bot command list."""

import logging

from src.bot import _build_bot_command_list
from src.config import BotConfig
from src.i18n import Translator
from src.infra.access_control import make_acl
from src.infra.commands import CommandDef
from src.services.system_prompt_builder import compose_system_prompt


def _cfg(**overrides: object) -> BotConfig:
    """Build a minimal BotConfig with the given overrides."""
    base: dict[str, object] = {
        "name": "test",
        "telegram_bot_token": "1:abc",
    }
    base.update(overrides)
    return BotConfig.model_validate(base)


def test_acl_blacklist_beats_allowed_for_all() -> None:
    """Blacklist denies a chat even when allowed_for_all is set."""
    cfg = _cfg(allowed_for_all=True, blacklist_chat_ids=(7,))
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(7) is False
    assert is_allowed(1) is True


def test_acl_default_denies_everyone() -> None:
    """A default config admits nobody."""
    cfg = _cfg()
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(123) is False


def test_acl_whitelist_admits_listed_chats() -> None:
    """Whitelisted chat ids are admitted; others denied."""
    cfg = _cfg(allowed_chat_ids=(1, 2, 3))
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(1) is True
    assert is_allowed(4) is False


def test_acl_allowed_for_all_admits_nonblacklisted() -> None:
    """allowed_for_all admits any non-blacklisted chat."""
    cfg = _cfg(allowed_for_all=True)
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(999_999) is True


def test_acl_blacklist_beats_whitelist() -> None:
    """Blacklist overrides a whitelisted chat id."""
    cfg = _cfg(allowed_chat_ids=(5,), blacklist_chat_ids=(5,))
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(5) is False


def test_acl_empty_whitelist_denies_everyone() -> None:
    """AGENTS invariant: empty allowed_chat_ids + allowed_for_all=False → nobody."""
    cfg = _cfg(allowed_chat_ids=())
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(0) is False
    assert is_allowed(1) is False
    assert is_allowed(-100) is False


def test_acl_whitelist_ignored_when_open_to_everyone() -> None:
    """allowed_for_all bypasses the whitelist entirely; only blacklist still bites."""
    cfg = _cfg(allowed_for_all=True, allowed_chat_ids=(1,), blacklist_chat_ids=(2,))
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(1) is True
    assert is_allowed(999) is True  # not in whitelist, still admitted
    assert is_allowed(2) is False  # blacklist overrides


def test_acl_admits_negative_group_chat_id() -> None:
    """Telegram group/supergroup ids are negative — must whitelist/blacklist cleanly."""
    cfg = _cfg(allowed_chat_ids=(-1001234567890,))
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(-1001234567890) is True
    assert is_allowed(1001234567890) is False  # sign matters, no abs() confusion


def test_acl_blacklist_denies_negative_group_under_open() -> None:
    """Blacklist denies a negative group id under allowed_for_all."""
    cfg = _cfg(allowed_for_all=True, blacklist_chat_ids=(-42,))
    is_allowed = make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(-42) is False
    assert is_allowed(42) is True


def test_command_list_includes_all_builtins() -> None:
    """The built bot command list contains every built-in command."""
    tr = Translator("en")
    out = _build_bot_command_list(tr, [])
    names = {bc.command for bc in out}
    assert {
        "start", "new", "sess", "context", "plan", "cancel",
        "stop", "mode", "model", "mcp", "info", "whoami", "help",
    }.issubset(names)


def test_command_list_appends_custom_commands() -> None:
    """Custom commands are appended to the bot command list."""
    tr = Translator("en")
    custom = [
        CommandDef(name="recall", description="Search memory", body="x", source=None),  # type: ignore[arg-type]
    ]
    out = _build_bot_command_list(tr, custom)
    by_name = {bc.command: bc.description for bc in out}
    assert by_name["recall"] == "Search memory"


def test_command_list_builtin_descriptions_translated() -> None:
    """Built-in command descriptions are translated, not raw i18n keys."""
    tr = Translator("en")
    out = _build_bot_command_list(tr, [])
    for bc in out:
        # Translation keys would surface as `bot_command_<x>`; that means the
        # i18n file is missing the entry.
        assert not bc.description.startswith("bot_command_")


def test_system_prompt_combines_builtin_contract_and_bot_prompt() -> None:
    """The system prompt joins the built-in contract with the bot prompt."""
    cfg = _cfg(system_prompt="Speak like Brain.")
    prompt = compose_system_prompt(cfg, Translator("en"))

    assert "Structured questionnaire format" in prompt
    assert "Bot-specific instructions:" in prompt
    assert prompt.endswith("Speak like Brain.")
