"""Claude Agent SDK backend adapter."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ProcessError,
    StreamEvent,
    TextBlock,
    ToolPermissionContext,
    query,
)

from .agent_types import StreamChunk, ToolEventCallback
from .session_store import Session, SessionStore

log = logging.getLogger(__name__)

_TITLE_MAX_LEN = 60
# Cyrillic prompt text is intentional; silence ambiguous-character lint.
_TITLE_PROMPT = "Придумай короткий заголовок (3-5 слов) для диалога, начатого этим сообщением пользователя. Ответь только заголовком, без кавычек и пояснений.\n\nСообщение:\n"  # noqa: RUF001

PermissionCallback = Callable[
    [int, str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]

_TOOL_POST_PREVIEW_NAMES = ("Monitor", "TaskOutput")

CLAUDE_MODES: tuple[str, ...] = ("default", "acceptEdits", "plan")
CLAUDE_MODELS: tuple[tuple[str, str], ...] = (
    ("claude-opus-4-7", "Opus 4.7"),
    ("claude-sonnet-4-6", "Sonnet 4.6"),
    ("claude-haiku-4-5", "Haiku 4.5"),
    ("", ""),
)


class ClaudeAgentBackend:
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
    ) -> None:
        self._store = session_store
        self._on_permission = on_permission
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._idle_ttl = idle_ttl_sec
        self._add_dirs = list(add_dirs) if add_dirs else []
        self._on_tool_event = on_tool_event
        self._initial_model = initial_model
        self._clients: dict[int, tuple[ClaudeSDKClient, float]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._gc_task: asyncio.Task[None] | None = None
        self._modes: dict[int, str] = {}
        self._models: dict[int, str | None] = {}

    def available_modes(self) -> tuple[str, ...]:
        return CLAUDE_MODES

    def available_models(self) -> tuple[tuple[str, str], ...]:
        return CLAUDE_MODELS

    def _ensure_gc_running(self) -> None:
        if self._idle_ttl <= 0:
            return
        if self._gc_task is None or self._gc_task.done():
            self._gc_task = asyncio.create_task(self._gc_loop())

    async def _gc_loop(self) -> None:
        interval = max(min(self._idle_ttl / 4, 60.0), 5.0)
        try:
            while True:
                await asyncio.sleep(interval)
                await self._gc_idle()
        except asyncio.CancelledError:
            raise

    async def _gc_idle(self) -> None:
        now = time.monotonic()
        stale = [
            chat_id
            for chat_id, (_client, last_used) in self._clients.items()
            if now - last_used > self._idle_ttl
        ]
        for chat_id in stale:
            lock = self._locks.get(chat_id)
            if lock is not None and lock.locked():
                continue
            entry = self._clients.pop(chat_id, None)
            self._modes.pop(chat_id, None)
            self._models.pop(chat_id, None)
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

    def _lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    def _make_options(
        self,
        chat_id: int,
        *,
        session_id: str | None = None,
        resume: str | None = None,
    ) -> ClaudeAgentOptions:
        can_use_tool: (
            Callable[
                [str, dict[str, Any], ToolPermissionContext],
                Awaitable[PermissionResultAllow | PermissionResultDeny],
            ]
            | None
        ) = None
        if self._on_permission is not None:
            on_perm = self._on_permission

            async def _can_use_tool(
                tool_name: str,
                tool_input: dict[str, Any],
                ctx: ToolPermissionContext,
            ) -> PermissionResultAllow | PermissionResultDeny:
                return await on_perm(chat_id, tool_name, tool_input, ctx)

            can_use_tool = _can_use_tool

        hooks: dict[str, list[HookMatcher]] | None = None
        if self._on_tool_event is not None:
            on_evt = self._on_tool_event
            post_matcher = "|".join(_TOOL_POST_PREVIEW_NAMES)

            def _hook_field(input: Any, name: str, default: Any) -> Any:
                if isinstance(input, dict):
                    return input.get(name, default)
                return getattr(input, name, default)

            async def pre_hook(
                input: Any, _tool_use_id: Any, _context: Any
            ) -> dict[str, Any]:
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
            log.warning("claude cli stderr (chat_id=%s): %s", chat_id, line.rstrip())

        return ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            include_partial_messages=True,
            can_use_tool=can_use_tool,
            cwd=self._cwd,
            add_dirs=list(self._add_dirs),
            setting_sources=["user", "project", "local"],
            hooks=cast(Any, hooks),
            session_id=session_id,
            resume=resume,
            stderr=_on_stderr,
        )

    async def _get_client(self, chat_id: int) -> ClaudeSDKClient:
        self._ensure_gc_running()
        entry = self._clients.get(chat_id)
        if entry is None:
            # No live client: resume the chat's current persisted session, or
            # mint a new one if this chat has never talked before.
            sid = self._store.current_id(chat_id)
            if sid is not None:
                options = self._make_options(chat_id, resume=sid)
            else:
                sid = self._store.create(chat_id).id
                options = self._make_options(chat_id, session_id=sid)
            client = ClaudeSDKClient(options=options)
            try:
                await client.__aenter__()
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
                sid = self._store.create(chat_id).id
                options = self._make_options(chat_id, session_id=sid)
                client = ClaudeSDKClient(options=options)
                await client.__aenter__()
            if self._initial_model:
                await client.set_model(self._initial_model)
                self._models[chat_id] = self._initial_model
            self._store.touch(chat_id, sid)
        else:
            client, _ = entry
        self._clients[chat_id] = (client, time.monotonic())
        return client

    async def ask(self, chat_id: int, prompt: str) -> str:
        chunks: list[str] = []
        async for chunk in self.ask_stream(chat_id, prompt):
            if chunk.kind == "text":
                chunks.append(chunk.text)
        return "".join(chunks).strip() or "(empty response)"

    async def ask_stream(self, chat_id: int, prompt: str) -> AsyncIterator[StreamChunk]:
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            await client.query(prompt)
            saw_delta = False
            async for msg in client.receive_response():
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

    async def get_context_usage(self, chat_id: int) -> dict[str, Any]:
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            result = await client.get_context_usage()
            self._clients[chat_id] = (client, time.monotonic())
            return dict(result)

    async def set_permission_mode(self, chat_id: int, mode: str) -> None:
        if mode not in CLAUDE_MODES:
            raise ValueError(f"unsupported Claude mode: {mode}")
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            await client.set_permission_mode(cast(Any, mode))
            self._clients[chat_id] = (client, time.monotonic())
            self._modes[chat_id] = mode

    async def set_model(self, chat_id: int, model: str | None) -> None:
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            await client.set_model(model)
            self._clients[chat_id] = (client, time.monotonic())
            self._models[chat_id] = model

    async def interrupt(self, chat_id: int) -> bool:
        entry = self._clients.get(chat_id)
        if entry is None:
            return False
        client, _ = entry
        await client.interrupt()
        return True

    async def get_mcp_status(self, chat_id: int) -> dict[str, Any]:
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            result = await client.get_mcp_status()
            self._clients[chat_id] = (client, time.monotonic())
            return dict(result)

    async def get_server_info(self, chat_id: int) -> dict[str, Any] | None:
        async with self._lock(chat_id):
            client = await self._get_client(chat_id)
            result = await client.get_server_info()
            self._clients[chat_id] = (client, time.monotonic())
            return dict(result) if result else None

    def current_mode(self, chat_id: int) -> str:
        return self._modes.get(chat_id, "default")

    def current_model(self, chat_id: int) -> str | None:
        return self._models.get(chat_id, self._initial_model)

    def has_session(self, chat_id: int) -> bool:
        return chat_id in self._clients

    async def _drop_client(self, chat_id: int) -> None:
        """Close and forget the live SDK client (caller holds the lock)."""
        self._modes.pop(chat_id, None)
        self._models.pop(chat_id, None)
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
            return self._store.create(chat_id)

    async def switch_session(self, chat_id: int, sid: str) -> Session | None:
        """Make the given session current; next turn resumes its history."""
        async with self._lock(chat_id):
            session = self._store.get_by_id(chat_id, sid)
            if session is None:
                return None
            await self._drop_client(chat_id)
            self._store.set_current(chat_id, session.id)
            return session

    async def delete_session(self, chat_id: int, sid: str) -> Session | None:
        """Delete the given session. If it was current, drop the live client so
        the next turn resumes the new current. Returns the deleted session, or
        None if the id is unknown."""
        async with self._lock(chat_id):
            target = self._store.get_by_id(chat_id, sid)
            if target is None:
                return None
            if self._store.current_id(chat_id) == target.id:
                await self._drop_client(chat_id)
            self._store.delete(chat_id, target.id)
            return target

    def list_sessions(self, chat_id: int) -> list[Session]:
        return self._store.all_sessions(chat_id)

    def current_session(self, chat_id: int) -> Session | None:
        return self._store.current(chat_id)

    async def generate_title(self, text: str) -> str | None:
        """One-shot Haiku call to name a session. Returns None on failure."""
        prompt = _TITLE_PROMPT + text[:2000]
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

    async def reset(self, chat_id: int) -> None:
        async with self._lock(chat_id):
            await self._drop_client(chat_id)
        self._locks.pop(chat_id, None)

    async def close_all(self) -> None:
        if self._gc_task is not None and not self._gc_task.done():
            self._gc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._gc_task
            self._gc_task = None
        for chat_id in list(self._clients):
            await self.reset(chat_id)


AgentSessionManager = ClaudeAgentBackend
