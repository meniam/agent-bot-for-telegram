"""Factory for provider-specific agent backends."""

from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import (
    McpSdkServerConfig,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..config import BotConfig
from .agent_types import AgentBackend, ToolEventCallback
from .claude_agent import ClaudeAgentBackend
from .codex_agent import CodexAgentBackend
from .pi_agent import PiAgentBackend
from .session_store import SessionStore

PermissionCallback = Callable[
    [int, str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]


def create_agent_backend(
    cfg: BotConfig,
    *,
    session_store: SessionStore,
    on_permission: PermissionCallback | None,
    system_prompt: str,
    add_dirs: list[str],
    on_tool_event: ToolEventCallback | None,
    task_server_factory: Callable[[int], McpSdkServerConfig | None] | None = None,
    graphiti_server_factory: (
        Callable[[int], McpSdkServerConfig | None] | None
    ) = None,
    codex_factory: Callable[[], Any] | None = None,
    pi_transport_factory: Callable[[str | None], Any] | None = None,
) -> AgentBackend:
    """Build the agent backend selected by ``cfg.agent_provider``.

    Raises ``ValueError`` for an unsupported provider.
    """
    if cfg.agent_provider == "claude":
        return ClaudeAgentBackend(
            session_store=session_store,
            on_permission=on_permission,
            system_prompt=system_prompt,
            cwd=cfg.working_dir,
            idle_ttl_sec=cfg.session_idle_ttl_sec,
            add_dirs=add_dirs,
            on_tool_event=on_tool_event,
            initial_model=cfg.agent_model,
            task_server_factory=task_server_factory,
            graphiti_server_factory=graphiti_server_factory,
            lang=cfg.lang,
            dangerously_skip_permissions=cfg.agent_dangerously_skip_permissions,
            event_timeout_sec=cfg.agent_event_timeout_sec,
        )
    if cfg.agent_provider == "codex":
        return CodexAgentBackend(
            session_store=session_store,
            system_prompt=system_prompt,
            cwd=cfg.working_dir,
            idle_ttl_sec=cfg.session_idle_ttl_sec,
            add_dirs=add_dirs,
            on_tool_event=on_tool_event,
            initial_model=cfg.agent_model,
            sandbox=cfg.codex_sandbox,
            approval_mode=cfg.codex_approval_mode,
            codex_factory=codex_factory,
            event_timeout_sec=cfg.agent_event_timeout_sec,
        )
    if cfg.agent_provider == "pi":
        return PiAgentBackend(
            session_store=session_store,
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
            event_timeout_sec=cfg.agent_event_timeout_sec,
        )
    raise ValueError(f"unsupported agent provider: {cfg.agent_provider}")
