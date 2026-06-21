"""Pure factories in `src/bot.py`: ACL builder + bot command list."""

import logging

from src.bot import _build_bot_command_list, _compose_system_prompt, _make_acl
from src.config import BotConfig
from src.i18n import Translator
from src.infra.commands import CommandDef


def _cfg(**overrides: object) -> BotConfig:
    base: dict[str, object] = {
        "name": "test",
        "telegram_bot_token": "1:abc",
    }
    base.update(overrides)
    return BotConfig.model_validate(base)


def test_acl_blacklist_beats_allowed_for_all() -> None:
    cfg = _cfg(allowed_for_all=True, blacklist_chat_ids=(7,))
    is_allowed = _make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(7) is False
    assert is_allowed(1) is True


def test_acl_default_denies_everyone() -> None:
    cfg = _cfg()
    is_allowed = _make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(123) is False


def test_acl_whitelist_admits_listed_chats() -> None:
    cfg = _cfg(allowed_chat_ids=(1, 2, 3))
    is_allowed = _make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(1) is True
    assert is_allowed(4) is False


def test_acl_allowed_for_all_admits_nonblacklisted() -> None:
    cfg = _cfg(allowed_for_all=True)
    is_allowed = _make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(999_999) is True


def test_acl_blacklist_beats_whitelist() -> None:
    cfg = _cfg(allowed_chat_ids=(5,), blacklist_chat_ids=(5,))
    is_allowed = _make_acl(cfg, logging.getLogger("test"))
    assert is_allowed(5) is False


def test_command_list_includes_all_builtins() -> None:
    tr = Translator("en")
    out = _build_bot_command_list(tr, [])
    names = {bc.command for bc in out}
    assert {
        "start", "new", "sess", "context", "plan", "cancel",
        "stop", "mode", "model", "mcp", "info", "whoami", "help",
    }.issubset(names)


def test_command_list_appends_custom_commands() -> None:
    tr = Translator("en")
    custom = [
        CommandDef(name="recall", description="Search memory", body="x", source=None),  # type: ignore[arg-type]
    ]
    out = _build_bot_command_list(tr, custom)
    by_name = {bc.command: bc.description for bc in out}
    assert by_name["recall"] == "Search memory"


def test_command_list_builtin_descriptions_translated() -> None:
    tr = Translator("en")
    out = _build_bot_command_list(tr, [])
    for bc in out:
        # Translation keys would surface as `bot_command_<x>`; that means the
        # i18n file is missing the entry.
        assert not bc.description.startswith("bot_command_")


def test_system_prompt_combines_builtin_contract_and_bot_prompt() -> None:
    cfg = _cfg(system_prompt="Speak like Brain.")
    prompt = _compose_system_prompt(cfg, Translator("en"))

    assert "Structured questionnaire format" in prompt
    assert "Bot-specific instructions:" in prompt
    assert prompt.endswith("Speak like Brain.")
