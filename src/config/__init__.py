"""Loads settings from config.yaml or config.json.

Format: top-level is a `<internal_bot_name>: BotConfig` dict.
Every bot can have its own token / working_dir / logs_dir.
"""

import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

CONFIG_DIR = Path(__file__).resolve().parent
CONFIG_YAML_PATH = CONFIG_DIR / "config.yaml"
CONFIG_YML_PATH = CONFIG_DIR / "config.yml"
CONFIG_JSON_PATH = CONFIG_DIR / "config.json"
CONFIG_PATH = CONFIG_YAML_PATH
DEFAULT_CONFIG_PATHS = (CONFIG_YAML_PATH, CONFIG_YML_PATH, CONFIG_JSON_PATH)

NESTED_CONFIG_SECTIONS: dict[str, dict[str, str]] = {
    "access": {
        "allowed_for_all": "allowed_for_all",
        "allowed_chat_ids": "allowed_chat_ids",
        "blacklist_chat_ids": "blacklist_chat_ids",
        "admin_chat_ids": "admin_chat_ids",
    },
    "tasks": {
        "enabled": "tasks_enabled",
        "dir": "tasks_dir",
        "scripts_dir": "tasks_scripts_dir",
        "tick_interval_sec": "tasks_tick_interval_sec",
        "max_output_chars": "tasks_max_output_chars",
        "script_timeout_sec": "tasks_script_timeout_sec",
        "history_limit": "tasks_history_limit",
        "allowed_tools": "tasks_allowed_tools",
    },
    "agent": {
        "provider": "agent_provider",
        "model": "agent_model",
        "agent_provider": "agent_provider",
        "agent_model": "agent_model",
    },
    "codex": {
        "sandbox": "codex_sandbox",
        "approval_mode": "codex_approval_mode",
        "codex_sandbox": "codex_sandbox",
        "codex_approval_mode": "codex_approval_mode",
    },
    "paths": {
        "working_dir": "working_dir",
        "logs_dir": "logs_dir",
        "messages_dir": "messages_dir",
        "commands_dir": "commands_dir",
    },
    "pi": {
        "cli_bin": "pi_cli_bin",
        "tools_mode": "pi_tools_mode",
        "session_persistence": "pi_session_persistence",
        "pi_cli_bin": "pi_cli_bin",
        "pi_tools_mode": "pi_tools_mode",
        "pi_session_persistence": "pi_session_persistence",
    },
    "streaming": {
        "draft_interval_sec": "draft_interval_sec",
        "approval_timeout_sec": "approval_timeout_sec",
        "agent_timeout_sec": "agent_timeout_sec",
        "session_idle_ttl_sec": "session_idle_ttl_sec",
        "chat_logger_capacity": "chat_logger_capacity",
    },
    "uploads": {
        "dir": "uploads_dir",
        "max_bytes": "upload_max_bytes",
        "uploads_dir": "uploads_dir",
        "upload_max_bytes": "upload_max_bytes",
    },
    "voice": {
        "api_key": "groq_api_key",
        "model": "groq_model",
        "timeout_sec": "groq_timeout_sec",
        "max_duration_sec": "voice_max_duration_sec",
        "groq_api_key": "groq_api_key",
        "groq_model": "groq_model",
        "groq_timeout_sec": "groq_timeout_sec",
        "voice_max_duration_sec": "voice_max_duration_sec",
    },
}

GATEWAY_CONFIG_FIELDS: dict[str, str] = {
    "telegram_bot_token": "telegram_bot_token",
    "lang": "lang",
    "logs_dir": "logs_dir",
    "messages_dir": "messages_dir",
    "commands_dir": "commands_dir",
    "allowed_for_all": "allowed_for_all",
    "allowed_chat_ids": "allowed_chat_ids",
    "blacklist_chat_ids": "blacklist_chat_ids",
    "draft_interval_sec": "draft_interval_sec",
    "approval_timeout_sec": "approval_timeout_sec",
    "chat_logger_capacity": "chat_logger_capacity",
}

GATEWAY_ACCESS_FIELDS: dict[str, str] = {
    "allowed_for_all": "allowed_for_all",
    "allowed_chat_ids": "allowed_chat_ids",
    "blacklist_chat_ids": "blacklist_chat_ids",
    "admin_chat_ids": "admin_chat_ids",
}

AGENT_CONFIG_FIELDS: dict[str, str] = {
    "provider": "agent_provider",
    "model": "agent_model",
    "agent_provider": "agent_provider",
    "agent_model": "agent_model",
    "system_prompt": "system_prompt",
    "working_dir": "working_dir",
    "working_path": "working_dir",
    "timeout_sec": "agent_timeout_sec",
    "agent_timeout_sec": "agent_timeout_sec",
    "session_idle_ttl_sec": "session_idle_ttl_sec",
}

PROVIDER_CONFIG_SECTIONS: dict[str, dict[str, str]] = {
    "claude": {},
    "codex": NESTED_CONFIG_SECTIONS["codex"],
    "pi": NESTED_CONFIG_SECTIONS["pi"],
}


class BotConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str  # internal name taken from the key in the config file
    telegram_bot_token: SecretStr
    # If None, bot.py falls back to translation key `default_system_prompt`.
    system_prompt: str | None = None
    agent_provider: Literal["claude", "codex", "pi"] = "claude"
    agent_model: str | None = None
    codex_sandbox: Literal[
        "read_only", "workspace_write", "danger_full_access"
    ] = "workspace_write"
    codex_approval_mode: Literal[
        "default", "on_request", "never", "full_auto"
    ] = "default"
    pi_cli_bin: str | None = None
    pi_tools_mode: Literal["default", "read_only", "no_tools"] = "default"
    pi_session_persistence: bool = False
    draft_interval_sec: float = 0.2
    approval_timeout_sec: int = 300
    agent_timeout_sec: int = 600
    session_idle_ttl_sec: int = 86400
    chat_logger_capacity: int = 256
    working_dir: str | None = None
    logs_dir: str | None = None
    # Directory for per-chat SQLite message logs. None → <logs_dir>/messages.
    messages_dir: str | None = None
    lang: str = "ru"
    # Voice/audio transcription via Groq. None disables the feature; the
    # voice handler then replies with `voice_disabled`.
    groq_api_key: SecretStr | None = None
    groq_model: str = "whisper-large-v3-turbo"
    groq_timeout_sec: float = 60.0
    # Reject audio longer than this (seconds). 0 disables the check.
    voice_max_duration_sec: int = 600
    # File / photo upload storage. None disables both handlers — the bot
    # responds with `upload_disabled` and the file is dropped.
    uploads_dir: str | None = None
    # Reject uploads larger than this (bytes). 0 disables the check.
    # Telegram Bot API caps downloads at 20 MB without a local Bot API server.
    upload_max_bytes: int = 20 * 1024 * 1024
    # Directory with user-defined slash commands (`*.md`). Each file becomes
    # a Telegram bot command whose body is sent to Claude as the prompt.
    # `null` / missing → no extra commands.
    commands_dir: str | None = None
    # Fail-closed access control. Evaluation order in `is_allowed`:
    #   1. `blacklist_chat_ids` — if the sender is here, deny outright.
    #      Takes priority over `allowed_for_all` and `allowed_chat_ids`.
    #   2. `allowed_for_all=True` — every non-blacklisted chat is allowed.
    #   3. otherwise — accept only chats listed in `allowed_chat_ids`.
    #      Default is the empty tuple, i.e. nobody is allowed.
    allowed_for_all: bool = False
    allowed_chat_ids: tuple[int, ...] = ()
    blacklist_chat_ids: tuple[int, ...] = ()
    # Chats allowed to manage global tasks and create script tasks. Fail-closed:
    # empty means no admins. Subset semantics are enforced by handlers, not ACL.
    admin_chat_ids: tuple[int, ...] = ()
    # Scheduled tasks. `tasks_enabled=False` (default) keeps the scheduler off
    # and makes /task reply "disabled".
    tasks_enabled: bool = False
    # Per-bot directory for task definitions + run history. None disables.
    tasks_dir: str | None = None
    # Directory holding runnable *.sh/*.py task scripts. None disables scripts.
    tasks_scripts_dir: str | None = None
    tasks_tick_interval_sec: int = 60
    tasks_max_output_chars: int = 4000
    tasks_script_timeout_sec: int = 300
    tasks_history_limit: int = 100
    # Tools an LLM task may use without interactive approval. None → read-only
    # default (Read/Glob/Grep/WebFetch); empty tuple → no tools at all.
    tasks_allowed_tools: tuple[str, ...] | None = None

    @field_validator("lang", mode="before")
    @classmethod
    def _lang_lower(cls, v: object) -> object:
        return str(v).lower() if v is not None else v


def is_admin(cfg: BotConfig, chat_id: int) -> bool:
    """Whether ``chat_id`` may manage global tasks and create script tasks.

    Fail-closed: an empty ``admin_chat_ids`` means there are no admins.
    """
    return chat_id in cfg.admin_chat_ids


def _flatten_nested_sections(name: str, data: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    nested: dict[str, dict[str, Any]] = {}

    def set_field(target: str, value: Any, source: str) -> None:
        if target in flat:
            raise ValueError(
                f"[{name}] config field {target} is set both directly and in section {source}"
            )
        flat[target] = value

    def flatten_section(
        source: str, values: dict[str, Any], section_map: dict[str, str]
    ) -> None:
        for nested_key, nested_value in values.items():
            target = section_map.get(nested_key)
            if target is None:
                raise ValueError(
                    f"[{name}] unknown config key in section {source}: {nested_key}"
                )
            set_field(target, nested_value, source)

    for key, value in data.items():
        if isinstance(value, dict):
            nested[key] = value
        else:
            flat[key] = value

    for section, values in nested.items():
        if section == "gateway":
            gateway_values: dict[str, Any] = {}
            for gateway_key, gateway_value in values.items():
                if gateway_key == "access":
                    if not isinstance(gateway_value, dict):
                        raise ValueError(
                            f"[{name}] config section gateway.access must be an object"
                        )
                    flatten_section(
                        "gateway.access", gateway_value, GATEWAY_ACCESS_FIELDS
                    )
                elif gateway_key == "voice":
                    if not isinstance(gateway_value, dict):
                        raise ValueError(
                            f"[{name}] config section gateway.voice must be an object"
                        )
                    flatten_section("gateway.voice", gateway_value, NESTED_CONFIG_SECTIONS["voice"])
                elif gateway_key == "uploads":
                    if not isinstance(gateway_value, dict):
                        raise ValueError(
                            f"[{name}] config section gateway.uploads must be an object"
                        )
                    flatten_section(
                        "gateway.uploads", gateway_value, NESTED_CONFIG_SECTIONS["uploads"]
                    )
                else:
                    gateway_values[gateway_key] = gateway_value
            flatten_section("gateway", gateway_values, GATEWAY_CONFIG_FIELDS)
            continue

        if section == "agent":
            flatten_section("agent", values, AGENT_CONFIG_FIELDS)
            continue

        if section == "providers":
            for provider, provider_values in values.items():
                provider_map = PROVIDER_CONFIG_SECTIONS.get(provider)
                if provider_map is None:
                    raise ValueError(f"[{name}] unknown provider config section: {provider}")
                if not isinstance(provider_values, dict):
                    raise ValueError(
                        f"[{name}] config section providers.{provider} must be an object"
                    )
                flatten_section(f"providers.{provider}", provider_values, provider_map)
            continue

        section_map = NESTED_CONFIG_SECTIONS.get(section)
        if section_map is None:
            raise ValueError(f"[{name}] unknown config section: {section}")
        flatten_section(section, values, section_map)

    return flat


def _resolve_path(raw: str, base_dir: Path) -> Path:
    """Expand ``~`` and anchor relative paths to the config file's directory
    (not the process CWD), then resolve to an absolute path."""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = base_dir / p
    return p.resolve()


def _build(name: str, data: dict[str, Any], base_dir: Path) -> BotConfig:
    data = _flatten_nested_sections(name, data)

    raw_token = data.get("telegram_bot_token") or os.environ.get(
        f"TELEGRAM_BOT_TOKEN_{name.upper()}", ""
    )
    if not raw_token or raw_token.startswith("put-"):
        raise ValueError(
            f"[{name}] telegram_bot_token is missing (or still a placeholder)."
        )

    raw_groq = (
        data.get("groq_api_key")
        or os.environ.get(f"GROQ_API_KEY_{name.upper()}")
        or os.environ.get("GROQ_API_KEY")
    )
    if raw_groq and str(raw_groq).startswith("put-"):
        raw_groq = None

    working_dir = data.get("working_dir")
    if working_dir:
        wd = _resolve_path(working_dir, base_dir)
        if not wd.is_dir():
            raise ValueError(
                f"[{name}] working_dir does not exist or is not a directory: {wd}"
            )
        working_dir = str(wd)

    logs_dir = data.get("logs_dir")
    if logs_dir:
        ld = _resolve_path(logs_dir, base_dir)
        ld.mkdir(parents=True, exist_ok=True)
        logs_dir = str(ld)

    messages_dir = data.get("messages_dir")
    if messages_dir:
        md = _resolve_path(messages_dir, base_dir)
        md.mkdir(parents=True, exist_ok=True)
        messages_dir = str(md)

    uploads_dir = data.get("uploads_dir")
    if uploads_dir:
        ud = _resolve_path(uploads_dir, base_dir)
        ud.mkdir(parents=True, exist_ok=True)
        uploads_dir = str(ud)

    commands_dir = data.get("commands_dir")
    if commands_dir:
        cd = _resolve_path(commands_dir, base_dir)
        if not cd.is_dir():
            raise ValueError(
                f"[{name}] commands_dir does not exist or is not a directory: {cd}"
            )
        commands_dir = str(cd)

    tasks_dir = data.get("tasks_dir")
    if tasks_dir:
        td = _resolve_path(tasks_dir, base_dir)
        td.mkdir(parents=True, exist_ok=True)
        tasks_dir = str(td)

    tasks_scripts_dir = data.get("tasks_scripts_dir")
    if tasks_scripts_dir:
        tsd = _resolve_path(tasks_scripts_dir, base_dir)
        tsd.mkdir(parents=True, exist_ok=True)
        tasks_scripts_dir = str(tsd)

    raw_allowed_tools = data.get("tasks_allowed_tools")
    if raw_allowed_tools is None:
        tasks_allowed_tools: tuple[str, ...] | None = None
    elif isinstance(raw_allowed_tools, list):
        tasks_allowed_tools = tuple(str(x) for x in raw_allowed_tools)
    else:
        raise ValueError(
            f"[{name}] tasks.allowed_tools must be null or a list of tool names"
        )

    def _parse_chat_id_list(field: str) -> tuple[int, ...]:
        raw = data.get(field)
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise ValueError(
                f"[{name}] {field} must be null or a list of integers"
            )
        try:
            return tuple(int(x) for x in raw)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"[{name}] {field} must contain integer chat IDs"
            ) from e

    allowed_chat_ids = _parse_chat_id_list("allowed_chat_ids")
    blacklist_chat_ids = _parse_chat_id_list("blacklist_chat_ids")
    admin_chat_ids = _parse_chat_id_list("admin_chat_ids")

    raw_for_all = data.get("allowed_for_all", False)
    if not isinstance(raw_for_all, bool):
        raise ValueError(
            f"[{name}] allowed_for_all must be a boolean"
        )

    payload: dict[str, Any] = {
        "name": name,
        "telegram_bot_token": raw_token,
        "system_prompt": data.get("system_prompt"),
        "agent_provider": data.get("agent_provider", "claude"),
        "agent_model": data.get("agent_model"),
        "codex_sandbox": data.get("codex_sandbox", "workspace_write"),
        "codex_approval_mode": data.get("codex_approval_mode", "default"),
        "pi_cli_bin": data.get("pi_cli_bin"),
        "pi_tools_mode": data.get("pi_tools_mode", "default"),
        "pi_session_persistence": data.get("pi_session_persistence", False),
        "working_dir": working_dir,
        "logs_dir": logs_dir,
        "messages_dir": messages_dir,
        "uploads_dir": uploads_dir,
        "commands_dir": commands_dir,
        "allowed_chat_ids": allowed_chat_ids,
        "blacklist_chat_ids": blacklist_chat_ids,
        "admin_chat_ids": admin_chat_ids,
        "allowed_for_all": raw_for_all,
        "tasks_dir": tasks_dir,
        "tasks_scripts_dir": tasks_scripts_dir,
        "tasks_allowed_tools": tasks_allowed_tools,
    }
    if raw_groq:
        payload["groq_api_key"] = raw_groq
    for key in (
        "draft_interval_sec",
        "approval_timeout_sec",
        "agent_timeout_sec",
        "session_idle_ttl_sec",
        "chat_logger_capacity",
        "lang",
        "groq_model",
        "groq_timeout_sec",
        "voice_max_duration_sec",
        "upload_max_bytes",
        "tasks_enabled",
        "tasks_tick_interval_sec",
        "tasks_max_output_chars",
        "tasks_script_timeout_sec",
        "tasks_history_limit",
    ):
        if key in data:
            payload[key] = data[key]

    return BotConfig.model_validate(payload)


def _default_config_path() -> Path:
    for path in DEFAULT_CONFIG_PATHS:
        if path.exists():
            return path
    return CONFIG_YAML_PATH


def _read_config_data(path: Path) -> dict[str, Any]:
    if path.suffix in {".yaml", ".yml"}:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    elif path.suffix == ".json":
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    else:
        raise ValueError(f"{path} has unsupported config format")

    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is empty or not an object")
    return data


def load(path: Path | str | None = None) -> dict[str, BotConfig]:
    p = Path(path) if path is not None else _default_config_path()
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Copy config.example.yaml → config.yaml and fill in telegram_bot_token."
        )
    data = _read_config_data(p)

    if not isinstance(data, dict) or not data:
        raise ValueError(f"{p.name} is empty or not an object")

    # Relative paths in the config resolve against the config file's directory.
    base_dir = p.resolve().parent

    # Backward compat: if the top level has telegram_bot_token directly,
    # this is the flat (single-bot) format — wrap it under the name "default".
    if "telegram_bot_token" in data:
        return {"default": _build("default", data, base_dir)}

    bots = {}
    for name, cfg in data.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"[{name}] bot config must be an object")
        bots[name] = _build(name, cfg, base_dir)
    if not bots:
        raise ValueError(f"{p.name} has no bot entries")
    return bots
