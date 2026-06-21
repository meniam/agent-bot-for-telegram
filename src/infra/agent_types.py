"""Provider-neutral agent backend contracts.

Telegram handlers should depend on these shapes instead of concrete SDK
classes. Provider adapters keep Claude / Codex wire details inside infra.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

ToolEventCallback = Callable[
    [int, str, str, dict[str, Any]],
    Awaitable[None],
]


class AgentTurnReset(RuntimeError):
    """Raised when an in-flight agent turn is intentionally reset."""


class AgentEventStreamTimeout(RuntimeError):
    """Raised when an agent backend stops emitting progress events."""


class AgentBackend(Protocol):
    @property
    def provider(self) -> str: ...

    def available_modes(self) -> tuple[str, ...]: ...

    def available_models(self) -> tuple[tuple[str, str], ...]: ...

    async def ask(self, chat_id: int, prompt: str) -> str: ...

    def ask_stream(self, chat_id: int, prompt: str) -> AsyncIterator[str]: ...

    async def get_context_usage(self, chat_id: int) -> dict[str, Any]: ...

    async def set_permission_mode(self, chat_id: int, mode: str) -> None: ...

    async def set_model(self, chat_id: int, model: str | None) -> None: ...

    async def interrupt(self, chat_id: int) -> bool: ...

    async def get_mcp_status(self, chat_id: int) -> dict[str, Any]: ...

    async def get_server_info(self, chat_id: int) -> dict[str, Any] | None: ...

    def current_mode(self, chat_id: int) -> str: ...

    def current_model(self, chat_id: int) -> str | None: ...

    def has_session(self, chat_id: int) -> bool: ...

    async def reset(self, chat_id: int) -> None: ...

    async def close_all(self) -> None: ...
