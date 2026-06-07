"""Compatibility exports for the agent backend layer."""

from .agent_types import AgentBackend, ToolEventCallback
from .claude_agent import ClaudeAgentBackend, PermissionCallback
from .codex_agent import CodexAgentBackend
from .pi_agent import PiAgentBackend

AgentSessionManager = ClaudeAgentBackend

__all__ = [
    "AgentBackend",
    "AgentSessionManager",
    "ClaudeAgentBackend",
    "CodexAgentBackend",
    "PermissionCallback",
    "PiAgentBackend",
    "ToolEventCallback",
]
