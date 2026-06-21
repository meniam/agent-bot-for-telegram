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
    StreamEvent,
    TextBlock,
    ToolPermissionContext,
)

from .agent_types import StreamChunk, ToolEventCallback

log = logging.getLogger(__name__)

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
        on_permission: PermissionCallback | None = None,
        system_prompt: str = "You are a friendly Telegram assistant. Reply concisely.",
        cwd: str | None = None,
        idle_ttl_sec: int = 86400,
        add_dirs: list[str] | None = None,
        on_tool_event: ToolEventCallback | None = None,
        initial_model: str | None = None,
    ) -> None:
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

    def _make_options(self, chat_id: int) -> ClaudeAgentOptions:
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

        return ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            include_partial_messages=True,
            can_use_tool=can_use_tool,
            cwd=self._cwd,
            add_dirs=list(self._add_dirs),
            setting_sources=["user", "project", "local"],
            hooks=cast(Any, hooks),
        )

    async def _get_client(self, chat_id: int) -> ClaudeSDKClient:
        self._ensure_gc_running()
        entry = self._clients.get(chat_id)
        if entry is None:
            client = ClaudeSDKClient(options=self._make_options(chat_id))
            await client.__aenter__()
            if self._initial_model:
                await client.set_model(self._initial_model)
                self._models[chat_id] = self._initial_model
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

    async def reset(self, chat_id: int) -> None:
        async with self._lock(chat_id):
            self._modes.pop(chat_id, None)
            self._models.pop(chat_id, None)
            entry = self._clients.pop(chat_id, None)
            if entry is not None:
                client, _ = entry
                await client.__aexit__(None, None, None)
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
