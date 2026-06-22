"""Config loader: multi-bot dict, flat-legacy wrap, ACL evaluation, env fallback."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import src.config as config_module
from src.config import BotConfig, load


def _write(tmp_path: Path, payload: dict[str, Any]) -> Path:
    """Write a JSON config file and return its path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _write_yaml(tmp_path: Path, text: str, name: str = "config.yaml") -> Path:
    """Write a YAML config file and return its path."""
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_multi_bot_dict(tmp_path: Path) -> None:
    """Verify a multi-bot dict config loads each bot under its key."""
    p = _write(
        tmp_path,
        {
            "alpha": {"telegram_bot_token": "1:abc"},
            "beta": {"telegram_bot_token": "2:def"},
        },
    )
    bots = load(p)
    assert set(bots) == {"alpha", "beta"}
    assert bots["alpha"].telegram_bot_token.get_secret_value() == "1:abc"


def test_yaml_config_loads(tmp_path: Path) -> None:
    """Verify a YAML config loads bots, chat ids, and multiline prompts."""
    p = _write_yaml(
        tmp_path,
        """
        alpha:
          telegram_bot_token: "1:abc"
          allowed_chat_ids: [123, 456]
          system_prompt: |
            Reply briefly.
            Keep answers practical.

        beta:
          telegram_bot_token: "2:def"
          allowed_for_all: true
        """,
    )
    bots = load(p)
    assert set(bots) == {"alpha", "beta"}
    assert bots["alpha"].allowed_chat_ids == (123, 456)
    assert bots["alpha"].system_prompt is not None
    assert "Keep answers practical." in bots["alpha"].system_prompt
    assert bots["beta"].allowed_for_all is True


def test_yml_config_loads(tmp_path: Path) -> None:
    """Verify a config with the .yml extension loads."""
    p = _write_yaml(
        tmp_path,
        """
        alpha:
          telegram_bot_token: "1:abc"
        """,
        name="config.yml",
    )

    bots = load(p)

    assert list(bots) == ["alpha"]


def test_yaml_nested_sections_load(tmp_path: Path) -> None:
    """Verify nested gateway, agent, and providers sections flatten onto the config."""
    p = _write_yaml(
        tmp_path,
        """
        alpha:
          gateway:
            telegram_bot_token: "1:abc"
            lang: en
            chat_logger_capacity: 12
            access:
              allowed_chat_ids: [123, 456]
              blacklist_chat_ids: [999]
            voice:
              api_key: gsk_real
              model: whisper-large-v3
              timeout_sec: 30.5
              max_duration_sec: 90
            uploads:
              max_bytes: 1024

          agent:
            provider: codex
            model: gpt-5.4
            system_prompt: "Reply briefly."
            working_path: null
            agent_timeout_sec: 120

          providers:
            claude: {}
            codex:
              sandbox: read_only
              approval_mode: on_request
            pi:
              tools_mode: read_only
        """,
    )

    bots = load(p)
    cfg = bots["alpha"]

    assert cfg.allowed_chat_ids == (123, 456)
    assert cfg.blacklist_chat_ids == (999,)
    assert cfg.agent_provider == "codex"
    assert cfg.agent_model == "gpt-5.4"
    assert cfg.codex_sandbox == "read_only"
    assert cfg.codex_approval_mode == "on_request"
    assert cfg.agent_timeout_sec == 120
    assert cfg.chat_logger_capacity == 12
    assert cfg.groq_api_key is not None
    assert cfg.groq_api_key.get_secret_value() == "gsk_real"
    assert cfg.groq_model == "whisper-large-v3"
    assert cfg.groq_timeout_sec == 30.5
    assert cfg.voice_max_duration_sec == 90
    assert cfg.upload_max_bytes == 1024
    assert cfg.pi_tools_mode == "read_only"


def test_yaml_nested_sections_reject_conflicts(tmp_path: Path) -> None:
    """Verify a key set both directly and in a section is rejected."""
    p = _write_yaml(
        tmp_path,
        """
        alpha:
          telegram_bot_token: "1:abc"
          agent_provider: claude
          agent:
            provider: codex
        """,
    )

    with pytest.raises(ValueError, match="both directly and in section agent"):
        load(p)


def test_default_load_prefers_yaml_over_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the default loader prefers a YAML config over a JSON one."""
    yaml_path = _write_yaml(
        tmp_path,
        """
        alpha:
          telegram_bot_token: "1:abc"
        """,
    )
    json_path = _write(tmp_path, {"beta": {"telegram_bot_token": "2:def"}})
    monkeypatch.setattr(
        config_module, "DEFAULT_CONFIG_PATHS", (yaml_path, json_path)
    )

    bots = load()

    assert list(bots) == ["alpha"]


def test_default_load_falls_back_to_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the default loader falls back to JSON when no YAML exists."""
    yaml_path = tmp_path / "config.yaml"
    json_path = _write(tmp_path, {"beta": {"telegram_bot_token": "2:def"}})
    monkeypatch.setattr(
        config_module, "DEFAULT_CONFIG_PATHS", (yaml_path, json_path)
    )

    bots = load()

    assert list(bots) == ["beta"]


def test_flat_legacy_format_wraps_as_default(tmp_path: Path) -> None:
    """Verify a flat legacy config wraps into a single 'default' bot."""
    p = _write(tmp_path, {"telegram_bot_token": "1:abc"})
    bots = load(p)
    assert list(bots) == ["default"]


def test_placeholder_token_rejected(tmp_path: Path) -> None:
    """Verify a placeholder bot token is rejected."""
    p = _write(tmp_path, {"alpha": {"telegram_bot_token": "put-it-here"}})
    with pytest.raises(ValueError, match="telegram_bot_token"):
        load(p)


def test_env_fallback_for_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a missing token falls back to the per-bot environment variable."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_ALPHA", "9:zzz")
    p = _write(tmp_path, {"alpha": {}})
    bots = load(p)
    assert bots["alpha"].telegram_bot_token.get_secret_value() == "9:zzz"


def test_acl_defaults_are_fail_closed() -> None:
    """Verify ACL and provider defaults are fail-closed."""
    cfg = BotConfig.model_validate(
        {"name": "x", "telegram_bot_token": "1:abc"}
    )
    assert cfg.allowed_for_all is False
    assert cfg.allowed_chat_ids == ()
    assert cfg.blacklist_chat_ids == ()
    assert cfg.agent_provider == "claude"


def test_codex_agent_config_accepted() -> None:
    """Verify a valid Codex agent config is accepted."""
    cfg = BotConfig.model_validate(
        {
            "name": "x",
            "telegram_bot_token": "1:abc",
            "agent_provider": "codex",
            "agent_model": "gpt-5.4",
            "codex_sandbox": "workspace_write",
            "codex_approval_mode": "on_request",
        }
    )
    assert cfg.agent_provider == "codex"
    assert cfg.agent_model == "gpt-5.4"
    assert cfg.codex_approval_mode == "on_request"


def test_pi_agent_config_accepted() -> None:
    """Verify a valid PI agent config is accepted."""
    cfg = BotConfig.model_validate(
        {
            "name": "x",
            "telegram_bot_token": "1:abc",
            "agent_provider": "pi",
            "agent_model": None,
            "pi_cli_bin": "/opt/bin/pi",
            "pi_tools_mode": "read_only",
            "pi_session_persistence": True,
        }
    )
    assert cfg.agent_provider == "pi"
    assert cfg.pi_cli_bin == "/opt/bin/pi"
    assert cfg.pi_tools_mode == "read_only"
    assert cfg.pi_session_persistence is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agent_provider", "other"),
        ("codex_sandbox", "root"),
        ("codex_approval_mode", "sometimes"),
        ("pi_tools_mode", "unsafe"),
    ],
)
def test_invalid_agent_config_rejected(field: str, value: str) -> None:
    """Verify invalid agent enum values are rejected."""
    with pytest.raises(ValidationError):
        BotConfig.model_validate(
            {"name": "x", "telegram_bot_token": "1:abc", field: value}
        )


def test_allowed_chat_ids_must_be_list(tmp_path: Path) -> None:
    """Verify a non-list allowed_chat_ids is rejected."""
    p = _write(
        tmp_path,
        {"alpha": {"telegram_bot_token": "1:abc", "allowed_chat_ids": "not-list"}},
    )
    with pytest.raises(ValueError, match="allowed_chat_ids"):
        load(p)


def test_blacklist_parses_integers(tmp_path: Path) -> None:
    """Verify blacklist_chat_ids coerces string entries to integers."""
    p = _write(
        tmp_path,
        {
            "alpha": {
                "telegram_bot_token": "1:abc",
                "blacklist_chat_ids": [1, "2", 3],
            }
        },
    )
    bots = load(p)
    assert bots["alpha"].blacklist_chat_ids == (1, 2, 3)


def test_extra_fields_rejected() -> None:
    """Verify unknown config fields are rejected."""
    with pytest.raises(ValidationError):
        BotConfig.model_validate(
            {"name": "x", "telegram_bot_token": "1:abc", "garbage": True}
        )


def test_tasks_section_loads(tmp_path: Path) -> None:
    """Verify the tasks section loads and resolves directories to absolute paths."""
    p = _write_yaml(
        tmp_path,
        """
        alpha:
          gateway:
            telegram_bot_token: "1:abc"
            access:
              admin_chat_ids: [111, 222]
          tasks:
            enabled: true
            dir: ./var/alpha/tasks
            scripts_dir: ./var/alpha/scripts
            tick_interval_sec: 30
            max_output_chars: 1000
            script_timeout_sec: 120
            history_limit: 50
            allowed_tools: [Read, Grep]
        """,
    )
    bots = load(p)
    cfg = bots["alpha"]
    assert cfg.tasks_enabled is True
    assert cfg.tasks_dir is not None and Path(cfg.tasks_dir).is_absolute()
    assert Path(cfg.tasks_dir).is_dir()
    assert cfg.tasks_scripts_dir is not None and Path(cfg.tasks_scripts_dir).is_dir()
    assert cfg.tasks_tick_interval_sec == 30
    assert cfg.tasks_history_limit == 50
    assert cfg.tasks_allowed_tools == ("Read", "Grep")
    assert cfg.admin_chat_ids == (111, 222)


def test_tasks_absent_defaults_disabled(tmp_path: Path) -> None:
    """Verify tasks default to disabled when no tasks section is present."""
    p = _write(tmp_path, {"alpha": {"telegram_bot_token": "1:abc"}})
    cfg = load(p)["alpha"]
    assert cfg.tasks_enabled is False
    assert cfg.tasks_dir is None
    assert cfg.tasks_allowed_tools is None  # read-only default resolved at runtime
    assert cfg.admin_chat_ids == ()


def test_is_admin_fail_closed() -> None:
    """Verify is_admin matches only configured admin ids and is fail-closed."""
    cfg = BotConfig.model_validate(
        {"name": "x", "telegram_bot_token": "1:abc", "admin_chat_ids": [7]}
    )
    assert config_module.is_admin(cfg, 7) is True
    assert config_module.is_admin(cfg, 8) is False
    empty = BotConfig.model_validate({"name": "y", "telegram_bot_token": "1:abc"})
    assert config_module.is_admin(empty, 7) is False
