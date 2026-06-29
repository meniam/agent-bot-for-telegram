"""Provider-neutral agent backend contracts.

Telegram handlers should depend on these shapes instead of concrete SDK
classes. Provider adapters keep Claude / Codex / PI wire details inside infra.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from .session_store import Session

ToolEventCallback = Callable[
    [int, str, str, dict[str, Any]],
    Awaitable[None],
]


@dataclass(slots=True)
class StreamChunk:
    """One chunk emitted by ask_stream(). kind='thinking' for extended-thinking tokens."""

    kind: Literal["text", "thinking"]
    text: str


class AgentTurnReset(RuntimeError):
    """Raised when an in-flight agent turn is intentionally reset."""


class AgentEventStreamTimeout(RuntimeError):
    """Raised when an agent backend stops emitting progress events."""


class AgentBackend(Protocol):
    """Provider-neutral contract for one live agent backend (one per bot).

    Concurrency: every method keyed by ``chat_id`` serializes that chat's turns
    on a per-chat lock, so one live SDK session is never used concurrently;
    different chats run in parallel. ``interrupt`` is the deliberate exception —
    it does not take the lock (the running turn holds it). Methods that touch a
    live session lazily create or resume it.
    """

    @property
    def provider(self) -> str:
        """Backend id used in logs and views: ``"claude"`` / ``"codex"`` / ``"pi"``."""
        ...

    def available_modes(self) -> tuple[str, ...]:
        """Permission/tool modes this backend offers for ``/mode`` (provider-specific)."""
        ...

    def available_models(self) -> tuple[tuple[str, str], ...]:
        """``(model_id, label)`` choices for ``/model``; ``("", "")`` means CLI default."""
        ...

    async def ask(self, chat_id: int, prompt: str) -> str:
        """Run one turn and return the full reply text (drains ``ask_stream``)."""
        ...

    def ask_stream(self, chat_id: int, prompt: str) -> AsyncIterator[StreamChunk]:
        """Run one turn, yielding reply chunks as they stream.

        An async generator despite the non-``async`` signature (Protocol quirk).
        Holds the per-chat lock for the whole turn. May raise ``AgentTurnReset``
        if the session is reset mid-turn or ``AgentEventStreamTimeout`` if the
        backend stops emitting progress.
        """
        ...

    async def ask_ephemeral(
        self, chat_id: int, prompt: str, *, allowed_tools: tuple[str, ...]
    ) -> str:
        """One-shot turn in a throwaway session (for scheduled LLM tasks).

        Must not mutate the chat's live session or ``current`` pointer.
        Permissions are non-interactive: only ``allowed_tools`` are permitted.
        Backends without a stateless turn primitive may raise
        ``NotImplementedError`` (Codex and PI currently do).
        """
        ...

    async def get_context_usage(self, chat_id: int) -> dict[str, Any]:
        """Token/context-window stats for ``/context`` (shape is provider-specific)."""
        ...

    async def set_permission_mode(self, chat_id: int, mode: str) -> None:
        """Switch the live session's mode; raises ``ValueError`` for an unknown mode."""
        ...

    async def set_model(self, chat_id: int, model: str | None) -> None:
        """Switch the live session's model; ``None`` selects the CLI default."""
        ...

    async def interrupt(self, chat_id: int) -> bool:
        """Interrupt the chat's running turn (lock-free). False if none is active."""
        ...

    def is_busy(self, chat_id: int) -> bool:
        """Whether a turn currently holds the chat's lock (another is in flight)."""
        ...

    async def get_mcp_status(self, chat_id: int) -> dict[str, Any]:
        """MCP server status for ``/mcp`` (``{"mcpServers": [...]}``)."""
        ...

    async def get_server_info(self, chat_id: int) -> dict[str, Any] | None:
        """Backend/server info for ``/info``; None when unavailable."""
        ...

    def current_mode(self, chat_id: int) -> str:
        """Mirrored current mode (the SDK exposes no getter)."""
        ...

    def current_model(self, chat_id: int) -> str | None:
        """Mirrored current model, or the initial model if no live session yet."""
        ...

    def has_session(self, chat_id: int) -> bool:
        """Whether a live session object currently exists for this chat."""
        ...

    async def new_session(self, chat_id: int) -> "Session":
        """Start a fresh session and make it current; the previous one is kept."""
        ...

    async def switch_session(
        self, chat_id: int, sid: str
    ) -> "Session | None":
        """Make session ``sid`` current (next turn resumes its history); None if unknown."""
        ...

    async def delete_session(
        self, chat_id: int, sid: str
    ) -> "Session | None":
        """Delete session ``sid``; drops the live client if it was current. None if unknown."""
        ...

    async def list_sessions(self, chat_id: int) -> "list[Session]":
        """All stored sessions for the chat, ordered by creation."""
        ...

    async def current_session(self, chat_id: int) -> "Session | None":
        """Return the chat's current session, or None if it has none yet."""
        ...

    async def generate_title(self, text: str) -> str | None:
        """Derive a short session title from the first message; None on failure."""
        ...

    async def reset(self, chat_id: int) -> None:
        """Tear down the chat's live session (the stored session list is untouched)."""
        ...

    async def close_all(self) -> None:
        """Stop the idle-GC task and close every live session (shutdown)."""
        ...
