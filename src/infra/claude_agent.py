"""Claude Agent SDK backend adapter."""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, NoReturn, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    McpSdkServerConfig,
    PermissionResultAllow,
    PermissionResultDeny,
    ProcessError,
    StreamEvent,
    TextBlock,
    ToolPermissionContext,
    query,
)

from .agent_base import BaseAgentBackend
from .agent_types import AgentEventStreamTimeout, StreamChunk, ToolEventCallback
from .session_store import Session, SessionStore
from .task_tool import TASK_TOOL_NAME

log = logging.getLogger(__name__)

_TITLE_MAX_LEN = 60
# The title text must follow the bot's configured language; `{lang}` is the
# ISO 639-1 code from `cfg.lang`, injected per call in `generate_title`.
_TITLE_PROMPT = (
    "Generate a short title (3-5 words) for a conversation started by the user "
    "message below. Reply with the title only — no quotes, no explanations. "
    "Write the title in this language (ISO 639-1 code): {lang}.\n\nMessage:\n"
)

PermissionCallback = Callable[
    [int, str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]

_TOOL_POST_PREVIEW_NAMES = ("Monitor", "TaskOutput")

# Watchdog: max silence between SDK events before a turn is treated as stalled.
# Matches the PI/Codex backends. Any event (delta, tool, lifecycle) resets it;
# only total silence (e.g. a wedged MCP/web-fetch call) trips it.
CLAUDE_EVENT_TIMEOUT_SEC = 120.0

CLAUDE_MODES: tuple[str, ...] = ("default", "acceptEdits", "plan")
CLAUDE_MODELS: tuple[tuple[str, str], ...] = (
    ("claude-opus-4-7", "Opus 4.7"),
    ("claude-sonnet-4-6", "Sonnet 4.6"),
    ("claude-haiku-4-5", "Haiku 4.5"),
    ("", ""),
)


class ClaudeAgentBackend(BaseAgentBackend):
    """Claude Agent SDK backend: one live ``ClaudeSDKClient`` per chat."""

    provider = "claude"

    def __init__(
        self,
        session_store: SessionStore,
        on_permission: PermissionCallback | None = None,
        system_prompt: str = "You are a friendly Telegram assistant. Reply concisely.",
        cwd: str | None = None,
        idle_ttl_sec: int = 86400,
        add_dirs: list[str] | None = None,
        on_tool_event: ToolEventCallback | None = None,
        initial_model: str | None = None,
        task_server_factory: Callable[[int], "McpSdkServerConfig | None"] | None = None,
        graphiti_server_factory: (
            Callable[[int], "McpSdkServerConfig | None"] | None
        ) = None,
        lang: str = "en",
        dangerously_skip_permissions: bool = False,
        event_timeout_sec: float = CLAUDE_EVENT_TIMEOUT_SEC,
    ) -> None:
        """Configure prompt, cwd, model, hooks, and the per-chat client maps."""
        self._init_base(session_store, idle_ttl_sec)
        self._event_timeout = event_timeout_sec
        self._on_permission = on_permission
        self._system_prompt = system_prompt
        self._lang = lang
        self._cwd = cwd
        self._add_dirs = list(add_dirs) if add_dirs else []
        self._on_tool_event = on_tool_event
        self._initial_model = initial_model
        self._task_server_factory = task_server_factory
        self._graphiti_server_factory = graphiti_server_factory
        self._dangerously_skip_permissions = dangerously_skip_permissions
        self._clients: dict[int, tuple[ClaudeSDKClient, float]] = {}
        self._modes: dict[int, str] = {}
        self._models: dict[int, str | None] = {}
        # Ring buffer of the CLI's most recent stderr lines per chat. The SDK
        # routes stderr to our callback but discards it from the ProcessError
        # (its `.stderr` is a placeholder), so we keep a tail here to surface
        # the real failure reason on a spawn error.
        self._stderr_tail: dict[int, deque[str]] = {}

    def available_modes(self) -> tuple[str, ...]:
        """Return the permission modes offered for ``/mode``."""
        return CLAUDE_MODES

    def available_models(self) -> tuple[tuple[str, str], ...]:
        """Return the ``(model_id, label)`` choices for ``/model``."""
        return CLAUDE_MODELS

    async def _gc_idle(self) -> None:
        """Close and forget clients idle past the TTL whose lock is free."""
        last_used = {chat_id: ts for chat_id, (_client, ts) in self._clients.items()}
        for chat_id in self._stale_chat_ids(last_used):
            entry = self._clients.pop(chat_id, None)
            self._modes.pop(chat_id, None)
            self._models.pop(chat_id, None)
            self._stderr_tail.pop(chat_id, None)
            self._locks.pop(chat_id, None)
            if entry is None:
                continue
            client, _ = entry
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                log.exception("idle gc: failed to close client for chat_id=%s", chat_id)
            else:
                log.info("idle gc: closed client for chat_id=%s", chat_id)

    def _make_options(
        self,
        chat_id: int,
        *,
        session_id: str | None = None,
        resume: str | None = None,
    ) -> ClaudeAgentOptions:
        """Build SDK options for a chat: permission gate, tool hooks, and MCP servers."""
        can_use_tool: (
            Callable[
                [str, dict[str, Any], ToolPermissionContext],
                Awaitable[PermissionResultAllow | PermissionResultDeny],
            ]
            | None
        ) = None
        # bypassPermissions makes the SDK skip the gate callback entirely, so
        # wiring one would be dead weight. Leave can_use_tool unset in that mode.
        if self._on_permission is not None and not self._dangerously_skip_permissions:
            on_perm = self._on_permission

            async def _can_use_tool(
                tool_name: str,
                tool_input: dict[str, Any],
                ctx: ToolPermissionContext,
            ) -> PermissionResultAllow | PermissionResultDeny:
                """Auto-allow the scheduling tool; defer all else to the permission gate."""
                # The scheduling tool is permission-checked internally
                # (TaskService) and bound to this chat, so it never prompts.
                if tool_name == TASK_TOOL_NAME:
                    return PermissionResultAllow()
                return await on_perm(chat_id, tool_name, tool_input, ctx)

            can_use_tool = _can_use_tool

        hooks: dict[str, list[HookMatcher]] | None = None
        if self._on_tool_event is not None:
            on_evt = self._on_tool_event
            post_matcher = "|".join(_TOOL_POST_PREVIEW_NAMES)

            def _hook_field(input: Any, name: str, default: Any) -> Any:
                """Read a field from the hook input, whether it is a dict or an object."""
                if isinstance(input, dict):
                    return input.get(name, default)
                return getattr(input, name, default)

            async def pre_hook(
                input: Any, _tool_use_id: Any, _context: Any
            ) -> dict[str, Any]:
                """Mirror a PreToolUse hook to the tool-event callback (errors logged)."""
                try:
                    await on_evt(
                        chat_id,
                        "pre",
                        _hook_field(input, "tool_name", ""),
                        dict(_hook_field(input, "tool_input", {}) or {}),
                    )
                except Exception:
                    log.exception("pre-tool hook failed")
                return {}

            async def post_hook(
                input: Any, _tool_use_id: Any, _context: Any
            ) -> dict[str, Any]:
                """Mirror a PostToolUse hook (input + response) to the callback (errors logged)."""
                try:
                    payload = {
                        "tool_input": dict(
                            _hook_field(input, "tool_input", {}) or {}
                        ),
                        "tool_response": _hook_field(input, "tool_response", None),
                    }
                    await on_evt(
                        chat_id,
                        "post",
                        _hook_field(input, "tool_name", ""),
                        payload,
                    )
                except Exception:
                    log.exception("post-tool hook failed")
                return {}

            hooks = {
                "PreToolUse": [
                    HookMatcher(matcher=None, hooks=[cast(Any, pre_hook)])
                ],
                "PostToolUse": [
                    HookMatcher(matcher=post_matcher, hooks=[cast(Any, post_hook)])
                ],
            }

        def _on_stderr(line: str) -> None:
            """Log a Claude CLI stderr line and keep it in the per-chat tail."""
            text = line.rstrip()
            log.warning("claude cli stderr (chat_id=%s): %s", chat_id, text)
            self._stderr_tail.setdefault(chat_id, deque(maxlen=50)).append(text)

        # Per-chat scheduling tool, bound to this chat_id. Registered as an MCP
        # server; it stays callable without an allowlist and is auto-approved in
        # `_can_use_tool`, so it never prompts.
        mcp_servers: dict[str, McpSdkServerConfig] = {}
        if self._task_server_factory is not None:
            server = self._task_server_factory(chat_id)
            if server is not None:
                mcp_servers["tasks"] = server
        # Per-chat Graphiti memory: the in-process proxy pins group_id=chat_id so
        # chats can't read/write each other's graph. The raw upstream server must
        # stay disabled in the project MCP config or this isolation is bypassable.
        if self._graphiti_server_factory is not None:
            gserver = self._graphiti_server_factory(chat_id)
            if gserver is not None:
                mcp_servers["graphiti-memory"] = gserver

        return ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            include_partial_messages=True,
            can_use_tool=can_use_tool,
            permission_mode=(
                "bypassPermissions"
                if self._dangerously_skip_permissions
                else "default"
            ),
            cwd=self._cwd,
            add_dirs=list(self._add_dirs),
            setting_sources=["user", "project", "local"],
            skills="all",
            hooks=cast(Any, hooks),
            session_id=session_id,
            resume=resume,
            stderr=_on_stderr,
            mcp_servers=cast(Any, mcp_servers),
        )

    async def _enter_client(self, chat_id: int, client: ClaudeSDKClient) -> None:
        """Start the SDK client, enriching a spawn failure with the CLI's stderr.

        The SDK drops the real stderr from ``ProcessError`` (its ``.stderr`` is a
        placeholder). We re-raise carrying the captured tail so the actual reason
        (e.g. the root ``--dangerously-skip-permissions`` guard) reaches the
        per-chat log instead of a generic "Check stderr output for details".
        """
        self._stderr_tail.pop(chat_id, None)
        try:
            await client.__aenter__()
        except ProcessError as e:
            tail = list(self._stderr_tail.get(chat_id, ()))
            if not tail:
                raise
            joined = "\n".join(tail)
            raise ProcessError(
                f"claude CLI failed to start: {tail[-1]}",
                exit_code=e.exit_code,
                stderr=joined,
            ) from e

    async def _get_client(self, chat_id: int) -> ClaudeSDKClient:
        """Return the chat's live client, resuming or minting a session as needed.

        On a failed resume (missing/corrupt session) it mints a fresh session so
        the chat keeps working instead of erroring out.
        """
        self._ensure_gc_running()
        entry = self._clients.get(chat_id)
        if entry is None:
            # No live client: resume the chat's current persisted session, or
            # mint a new one if this chat has never talked before.
            sid = await self._store.current_id(chat_id)
            if sid is not None:
                options = self._make_options(chat_id, resume=sid)
            else:
                sid = (await self._store.create(chat_id)).id
                options = self._make_options(chat_id, session_id=sid)
            client = ClaudeSDKClient(options=options)
            try:
                await self._enter_client(chat_id, client)
            except ProcessError:
                # Resume target is missing/corrupt (CLI exits 1: "No
                # conversation found"). Drop it and mint a fresh session so the
                # chat keeps working instead of dead-ending on internal error.
                if options.resume is None:
                    raise
                log.warning(
                    "resume failed for chat_id=%s session=%s; minting fresh",
                    chat_id,
                    sid,
                )
                with contextlib.suppress(Exception):
                    await client.__aexit__(None, None, None)
                sid = (await self._store.create(chat_id)).id
                options = self._make_options(chat_id, session_id=sid)
                client = ClaudeSDKClient(options=options)
                await self._enter_client(chat_id, client)
            if self._initial_model:
                await client.set_model(self._initial_model)
                self._models[chat_id] = self._initial_model
            await self._store.touch(chat_id, sid)
        else:
            client, _ = entry
        self._clients[chat_id] = (client, time.monotonic())
        return client

    async def ask(self, chat_id: int, prompt: str) -> str:
        """Run one turn and return the full reply text (drains ``ask_stream``)."""
        chunks: list[str] = []
        async for chunk in self.ask_stream(chat_id, prompt):
            if chunk.kind == "text":
                chunks.append(chunk.text)
        return "".join(chunks).strip() or "(empty response)"

    async def ask_stream(self, chat_id: int, prompt: str) -> AsyncIterator[StreamChunk]:
        """Run one turn under the per-chat lock, yielding text/thinking chunks.

        Prefers streamed ``content_block_delta`` events; falls back to the final
        ``AssistantMessage`` text blocks when no delta was seen.
        """
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            await client.query(prompt)
            saw_delta = False
            events = client.receive_response()
            while True:
                try:
                    msg = await asyncio.wait_for(
                        events.__anext__(),
                        timeout=self._event_timeout,
                    )
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    await self._on_event_timeout(chat_id, client)
                if isinstance(msg, StreamEvent):
                    event = msg.event
                    delta = event.get("delta", {})
                    delta_type = delta.get("type") if isinstance(delta, dict) else None
                    if event.get("type") == "content_block_delta":
                        if delta_type == "text_delta":
                            saw_delta = True
                            yield StreamChunk(kind="text", text=delta["text"])
                        elif delta_type == "thinking_delta":
                            saw_delta = True
                            yield StreamChunk(kind="thinking", text=delta.get("thinking", ""))
                elif isinstance(msg, AssistantMessage) and not saw_delta:
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            yield StreamChunk(kind="text", text=block.text)

    async def _on_event_timeout(
        self, chat_id: int, client: ClaudeSDKClient
    ) -> NoReturn:
        """Interrupt a stalled turn and raise ``AgentEventStreamTimeout``.

        Called when the SDK emits no event for ``self._event_timeout`` seconds —
        e.g. a wedged MCP/web-fetch tool call during deep-research. Best-effort
        interrupts the live turn so the session is reusable, then always raises.
        """
        msg = (
            "Claude event stream timed out after "
            f"{self._event_timeout:.0f}s waiting for the next event"
        )
        log.warning("%s (chat_id=%s)", msg, chat_id)
        with contextlib.suppress(Exception):
            await client.interrupt()
        raise AgentEventStreamTimeout(msg) from None

    async def get_context_usage(self, chat_id: int) -> dict[str, Any]:
        """Return the SDK's token/context-window stats for the chat."""
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            result = await client.get_context_usage()
            self._clients[chat_id] = (client, time.monotonic())
            return dict(result)

    async def set_permission_mode(self, chat_id: int, mode: str) -> None:
        """Switch the live session's mode; raise ``ValueError`` for an unknown mode."""
        if mode not in CLAUDE_MODES:
            raise ValueError(f"unsupported Claude mode: {mode}")
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            await client.set_permission_mode(cast(Any, mode))
            self._clients[chat_id] = (client, time.monotonic())
            self._modes[chat_id] = mode

    async def set_model(self, chat_id: int, model: str | None) -> None:
        """Switch the live session's model; ``None`` selects the CLI default."""
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            await client.set_model(model)
            self._clients[chat_id] = (client, time.monotonic())
            self._models[chat_id] = model

    async def interrupt(self, chat_id: int) -> bool:
        """Interrupt the chat's running turn (lock-free); False if none is live."""
        entry = self._clients.get(chat_id)
        if entry is None:
            return False
        client, _ = entry
        await client.interrupt()
        return True

    async def get_mcp_status(self, chat_id: int) -> dict[str, Any]:
        """Return MCP server status for the chat (``{"mcpServers": [...]}``)."""
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            result = await client.get_mcp_status()
            self._clients[chat_id] = (client, time.monotonic())
            return dict(result)

    async def get_server_info(self, chat_id: int) -> dict[str, Any] | None:
        """Return backend/server info for the chat, or None when unavailable."""
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            result = await client.get_server_info()
            self._clients[chat_id] = (client, time.monotonic())
            return dict(result) if result else None

    def current_mode(self, chat_id: int) -> str:
        """Return the mirrored current mode, defaulting to ``"default"``."""
        return self._modes.get(chat_id, "default")

    def current_model(self, chat_id: int) -> str | None:
        """Return the mirrored current model, or the initial model if none is live."""
        return self._models.get(chat_id, self._initial_model)

    def has_session(self, chat_id: int) -> bool:
        """Return whether a live client currently exists for the chat."""
        return chat_id in self._clients

    async def _drop_client(self, chat_id: int) -> None:
        """Close and forget the live SDK client (caller holds the lock)."""
        self._modes.pop(chat_id, None)
        self._models.pop(chat_id, None)
        self._stderr_tail.pop(chat_id, None)
        entry = self._clients.pop(chat_id, None)
        if entry is not None:
            client, _ = entry
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                log.exception("failed to close client for chat_id=%s", chat_id)

    async def new_session(self, chat_id: int) -> Session:
        """Start a fresh session; the previous one stays in the list."""
        async with self._lock(chat_id):
            await self._drop_client(chat_id)
            return await self._store.create(chat_id)

    async def switch_session(self, chat_id: int, sid: str) -> Session | None:
        """Make the given session current; next turn resumes its history."""
        async with self._lock(chat_id):
            session = await self._store.get_by_id(chat_id, sid)
            if session is None:
                return None
            await self._drop_client(chat_id)
            await self._store.set_current(chat_id, session.id)
            return session

    async def delete_session(self, chat_id: int, sid: str) -> Session | None:
        """Delete the given session, returning it or None if the id is unknown.

        If it was current, drop the live client so the next turn resumes the new
        current session.
        """
        async with self._lock(chat_id):
            target = await self._store.get_by_id(chat_id, sid)
            if target is None:
                return None
            if await self._store.current_id(chat_id) == target.id:
                await self._drop_client(chat_id)
            await self._store.delete(chat_id, target.id)
            return target

    async def generate_title(self, text: str) -> str | None:
        """One-shot, single-turn SDK call to name a session.

        Runs on the bot's configured model (``self._initial_model``; None → CLI
        default) — never assumes a specific model like Haiku is available on the
        account. Returns None on failure.
        """
        prompt = _TITLE_PROMPT.format(lang=self._lang) + text[:2000]
        # Stay on the bot's configured model/provider; never assume a specific
        # model (e.g. Haiku) is available on this account. None → CLI default.
        options = ClaudeAgentOptions(
            model=self._initial_model,
            max_turns=1,
            cwd=self._cwd,
            allowed_tools=[],
        )
        parts: list[str] = []
        try:
            async for msg in query(prompt=prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
        except Exception:
            log.exception("generate_title failed")
            return None
        title = " ".join("".join(parts).split()).strip().strip('"')
        if not title:
            return None
        return title[:_TITLE_MAX_LEN]

    async def ask_ephemeral(
        self, chat_id: int, prompt: str, *, allowed_tools: tuple[str, ...]
    ) -> str:
        """Run a one-shot agent turn in a throwaway session for a scheduled task.

        Uses the SDK's stateless ``query()`` so the chat's live session and the
        ``current`` pointer are never touched. Permissions are non-interactive:
        only tools in ``allowed_tools`` are allowed; everything else is denied
        silently (no Telegram gate — nobody is watching a background run).
        """
        allow = set(allowed_tools)

        async def _can_use_tool(
            tool_name: str,
            _tool_input: dict[str, Any],
            _ctx: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            """Allow only tools in the task's allowlist; deny everything else silently."""
            if tool_name in allow:
                return PermissionResultAllow()
            return PermissionResultDeny(
                message=f"tool {tool_name} is not in tasks.allowed_tools"
            )

        options = ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            model=self._initial_model,
            cwd=self._cwd,
            add_dirs=list(self._add_dirs),
            setting_sources=["user", "project", "local"],
            allowed_tools=list(allowed_tools),
            can_use_tool=_can_use_tool,
        )
        log.debug("ask_ephemeral chat_id=%s tools=%s", chat_id, sorted(allow))

        # A `can_use_tool` callback forces streaming mode: the prompt must be an
        # AsyncIterable of message dicts, not a plain string.
        async def _prompt_stream() -> AsyncIterator[dict[str, Any]]:
            """Yield the prompt as a one-message user stream (streaming mode requires it)."""
            yield {
                "type": "user",
                "message": {"role": "user", "content": prompt},
            }

        parts: list[str] = []
        async for msg in query(prompt=_prompt_stream(), options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        return "".join(parts).strip()

    async def reset(self, chat_id: int) -> None:
        """Drop the chat's live client and forget its lock (stored list untouched)."""
        async with self._lock(chat_id):
            await self._drop_client(chat_id)
        self._locks.pop(chat_id, None)

    async def close_all(self) -> None:
        """Cancel the idle-GC task and reset every live client (shutdown)."""
        if self._gc_task is not None and not self._gc_task.done():
            self._gc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._gc_task
            self._gc_task = None
        for chat_id in list(self._clients):
            await self.reset(chat_id)


AgentSessionManager = ClaudeAgentBackend
