"""Factory for provider-specific agent backends."""

from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..config import BotConfig
from .agent_types import AgentBackend, ToolEventCallback
from .claude_agent import ClaudeAgentBackend
from .codex_agent import CodexAgentBackend
from .pi_agent import PiAgentBackend

PermissionCallback = Callable[
    [int, str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]


def create_agent_backend(
    cfg: BotConfig,
    *,
    on_permission: PermissionCallback | None,
    system_prompt: str,
    add_dirs: list[str],
    on_tool_event: ToolEventCallback | None,
    codex_factory: Callable[[], Any] | None = None,
    pi_transport_factory: Callable[[str | None], Any] | None = None,
) -> AgentBackend:
    if cfg.agent_provider == "claude":
        return ClaudeAgentBackend(
            on_permission=on_permission,
            system_prompt=system_prompt,
            cwd=cfg.working_dir,
            idle_ttl_sec=cfg.session_idle_ttl_sec,
            add_dirs=add_dirs,
            on_tool_event=on_tool_event,
            initial_model=cfg.agent_model,
        )
    if cfg.agent_provider == "codex":
        return CodexAgentBackend(
            system_prompt=system_prompt,
            cwd=cfg.working_dir,
            idle_ttl_sec=cfg.session_idle_ttl_sec,
            add_dirs=add_dirs,
            on_tool_event=on_tool_event,
            initial_model=cfg.agent_model,
            sandbox=cfg.codex_sandbox,
            approval_mode=cfg.codex_approval_mode,
            codex_factory=codex_factory,
        )
    if cfg.agent_provider == "pi":
        return PiAgentBackend(
            system_prompt=system_prompt,
            cwd=cfg.working_dir,
            idle_ttl_sec=cfg.session_idle_ttl_sec,
            add_dirs=add_dirs,
            on_tool_event=on_tool_event,
            initial_model=cfg.agent_model,
            cli_bin=cfg.pi_cli_bin,
            tools_mode=cfg.pi_tools_mode,
            session_persistence=cfg.pi_session_persistence,
            transport_factory=pi_transport_factory,
        )
    raise ValueError(f"unsupported agent provider: {cfg.agent_provider}")
