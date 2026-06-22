"""Codex backend adapter.

The public Python Codex SDK exposes async thread/run primitives. Richer
client events and approvals are app-server concepts, so this adapter keeps a
small normalization layer that can be wired to SDK/app-server event sources as
they are exposed without leaking Codex wire shapes into Telegram handlers.
"""

import asyncio
import contextlib
import importlib
import logging
import shutil
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from .agent_types import AgentEventStreamTimeout, AgentTurnReset, StreamChunk, ToolEventCallback
from .session_store import Session, SessionStore

log = logging.getLogger(__name__)

# Codex has no SDK resume, so switching/restoring a session here just resets
# the live thread; conversation history is not replayed. See AGENTS.md.
_TITLE_MAX_LEN = 60

CODEX_MODES: tuple[str, ...] = ("default", "on_request", "never", "full_auto", "plan")
CODEX_MODELS: tuple[tuple[str, str], ...] = (
    ("gpt-5.4", "GPT-5.4"),
    ("gpt-5.3-codex", "GPT-5.3 Codex"),
    ("", ""),
)
CODEX_RUN_TIMEOUT_SEC = 120.0


@dataclass(slots=True)
class _CodexSession:
    thread: Any
    last_used: float
    model: str | None
    mode: str


class CodexAgentBackend:
    provider = "codex"

    def __init__(
        self,
        session_store: SessionStore,
        system_prompt: str,
        cwd: str | None = None,
        idle_ttl_sec: int = 86400,
        add_dirs: list[str] | None = None,
        on_tool_event: ToolEventCallback | None = None,
        initial_model: str | None = None,
        sandbox: str = "workspace_write",
        approval_mode: str = "default",
        codex_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._store = session_store
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._idle_ttl = idle_ttl_sec
        self._add_dirs = list(add_dirs) if add_dirs else []
        self._on_tool_event = on_tool_event
        self._initial_model = initial_model
        self._sandbox_name = sandbox
        self._approval_mode = approval_mode
        self._codex_factory = codex_factory
        self._codex: Any | None = None
        self._sessions: dict[int, _CodexSession] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._gc_task: asyncio.Task[None] | None = None
        self._active_turns: dict[int, Any] = {}

    def available_modes(self) -> tuple[str, ...]:
        return CODEX_MODES

    def available_models(self) -> tuple[tuple[str, str], ...]:
        return CODEX_MODELS

    def _lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

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
            for chat_id, session in self._sessions.items()
            if now - session.last_used > self._idle_ttl
        ]
        for chat_id in stale:
            lock = self._locks.get(chat_id)
            if lock is not None and lock.locked():
                continue
            self._sessions.pop(chat_id, None)
            self._locks.pop(chat_id, None)
            log.info("idle gc: dropped Codex thread for chat_id=%s", chat_id)

    async def _ensure_runtime(self) -> Any:
        if self._codex is not None:
            return self._codex
        if self._codex_factory is not None:
            codex = self._codex_factory()
        else:
            try:
                module = importlib.import_module("openai_codex")
            except ModuleNotFoundError as e:
                raise RuntimeError(
                    "Codex backend requires the openai-codex package. "
                    "Install project dependencies and authenticate Codex first."
                ) from e
            config_cls = getattr(module, "CodexConfig", None)
            codex_bin = self._resolve_codex_bin()
            if config_cls is not None and codex_bin is not None:
                codex = module.AsyncCodex(
                    config=config_cls(codex_bin=codex_bin, cwd=self._cwd)
                )
            else:
                if config_cls is not None and self._cwd is not None:
                    codex = module.AsyncCodex(config=config_cls(cwd=self._cwd))
                else:
                    codex = module.AsyncCodex()
        enter = getattr(codex, "__aenter__", None)
        if enter is not None:
            codex = await enter()
        self._codex = codex
        return codex

    def _resolve_codex_bin(self) -> str | None:
        for candidate in (
            shutil.which("codex"),
            "/Applications/Codex.app/Contents/Resources/codex",
        ):
            if candidate:
                return candidate
        return None

    async def _start_thread(self, chat_id: int) -> Any:
        codex = await self._ensure_runtime()
        kwargs: dict[str, Any] = {}
        model = self._initial_model
        if model:
            kwargs["model"] = model
        sandbox = self._resolve_sandbox()
        if sandbox is not None:
            kwargs["sandbox"] = sandbox
        if self._cwd:
            kwargs["cwd"] = self._cwd
        if self._system_prompt:
            kwargs["base_instructions"] = self._system_prompt
        approval_mode = self._resolve_approval_mode()
        if approval_mode is not None:
            kwargs["approval_mode"] = approval_mode
        thread = await codex.thread_start(**kwargs)
        self._sessions[chat_id] = _CodexSession(
            thread=thread,
            last_used=time.monotonic(),
            model=model,
            mode=self._approval_mode,
        )
        return thread

    def _resolve_approval_mode(self) -> Any | None:
        if self._codex_factory is not None:
            return self._approval_mode
        try:
            module = importlib.import_module("openai_codex")
        except ModuleNotFoundError:
            return self._approval_mode
        approval_cls = getattr(module, "ApprovalMode", None)
        if approval_cls is None:
            return self._approval_mode
        if self._approval_mode == "never":
            return getattr(approval_cls, "auto_review", "auto_review")
        if self._approval_mode == "full_auto":
            return getattr(approval_cls, "auto_review", "auto_review")
        if self._approval_mode == "on_request":
            return getattr(approval_cls, "auto_review", "auto_review")
        return getattr(approval_cls, "auto_review", "auto_review")

    def _resolve_sandbox(self) -> Any | None:
        sandbox_name = (
            "full_access"
            if self._sandbox_name == "danger_full_access"
            else self._sandbox_name
        )
        if self._codex_factory is not None:
            return sandbox_name
        try:
            module = importlib.import_module("openai_codex")
        except ModuleNotFoundError:
            return sandbox_name
        sandbox_cls = getattr(module, "Sandbox", None)
        if sandbox_cls is None:
            return sandbox_name
        return getattr(sandbox_cls, sandbox_name, sandbox_name)

    async def _get_thread(self, chat_id: int) -> Any:
        self._ensure_gc_running()
        session = self._sessions.get(chat_id)
        if session is None:
            return await self._start_thread(chat_id)
        session.last_used = time.monotonic()
        return session.thread

    async def ask(self, chat_id: int, prompt: str) -> str:
        chunks: list[str] = []
        async for chunk in self.ask_stream(chat_id, prompt):
            if chunk.kind == "text":
                chunks.append(chunk.text)
        return "".join(chunks).strip() or "(empty response)"

    async def ask_stream(self, chat_id: int, prompt: str) -> AsyncIterator[StreamChunk]:
        async with self._lock(chat_id):
            thread = await self._get_thread(chat_id)
            session = self._sessions[chat_id]
            run_prompt = self._prepare_prompt(prompt, session.mode)
            turn = await self._start_turn(thread, run_prompt)
            self._active_turns[chat_id] = turn
            try:
                await self._emit_lifecycle(
                    chat_id,
                    "pre",
                    "prompt sent",
                    mode=session.mode,
                    model=session.model,
                )
                log.info(
                    "Codex prompt sent chat_id=%s mode=%s model=%s",
                    chat_id,
                    session.mode,
                    session.model or "default",
                )
                result = await self._wait_for_turn(chat_id, turn)
                if chat_id not in self._sessions:
                    raise AgentTurnReset("Codex session reset")
                text = self._extract_final_response(result)
                await self._emit_result_tool_events(chat_id, result)
                await self._emit_lifecycle(
                    chat_id,
                    "post",
                    "run completed",
                    mode=session.mode,
                    model=session.model,
                )
                if text:
                    yield StreamChunk(kind="text", text=text)
            finally:
                self._active_turns.pop(chat_id, None)
                session.last_used = time.monotonic()

    async def _start_turn(self, thread: Any, prompt: str) -> Any:
        turn_method = getattr(thread, "turn", None)
        if turn_method is not None:
            turn = turn_method(prompt)
            return await turn if hasattr(turn, "__await__") else turn
        return thread.run(prompt)

    async def _wait_for_turn(self, chat_id: int, turn: Any) -> Any:
        if hasattr(turn, "stream"):
            return await self._wait_for_streamed_turn(chat_id, turn)
        if not hasattr(turn, "__await__"):
            return turn
        try:
            return await asyncio.wait_for(turn, timeout=CODEX_RUN_TIMEOUT_SEC)
        except TimeoutError:
            msg = (
                "Codex run timed out after "
                f"{CODEX_RUN_TIMEOUT_SEC:.0f}s waiting for completion"
            )
            log.warning("%s (chat_id=%s)", msg, chat_id)
            session = self._sessions.get(chat_id)
            await self._emit_lifecycle(
                chat_id,
                "post",
                msg,
                mode=session.mode if session else self._approval_mode,
                model=session.model if session else self._initial_model,
            )
            await self._cancel_turn(turn)
            raise AgentEventStreamTimeout(msg) from None

    async def _wait_for_streamed_turn(self, chat_id: int, turn: Any) -> dict[str, Any]:
        stream = turn.stream()
        items: list[dict[str, Any]] = []
        completed: dict[str, Any] | None = None
        usage: dict[str, Any] | None = None
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        stream.__anext__(),
                        timeout=CODEX_RUN_TIMEOUT_SEC,
                    )
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    await self._handle_turn_timeout(chat_id, turn)

                await self._handle_sdk_notification(chat_id, event)
                payload = self._to_plain(getattr(event, "payload", None))
                method = str(getattr(event, "method", ""))
                if (
                    method == "item/completed"
                    and isinstance(payload, dict)
                    and isinstance(payload.get("item"), dict)
                ):
                    items.append(payload["item"])
                elif (
                    method == "thread/tokenUsage/updated"
                    and isinstance(payload, dict)
                    and isinstance(payload.get("token_usage"), dict)
                ):
                    usage = payload["token_usage"]
                elif (
                    method == "turn/completed"
                    and isinstance(payload, dict)
                    and isinstance(payload.get("turn"), dict)
                ):
                    completed = payload["turn"]
                    break
                if chat_id not in self._sessions:
                    raise AgentTurnReset("Codex session reset")
        finally:
            closer = getattr(stream, "aclose", None)
            if closer is not None:
                with contextlib.suppress(Exception):
                    await closer()

        if completed is not None:
            error = completed.get("error")
            if completed.get("status") == "failed":
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    raise RuntimeError(error["message"])
                raise RuntimeError("turn failed with status failed")
        return {
            "final_response": self._final_assistant_response_from_items(items),
            "items": items,
            "usage": usage,
            "turn": completed,
        }

    async def _handle_turn_timeout(self, chat_id: int, turn: Any) -> None:
        msg = (
            "Codex run timed out after "
            f"{CODEX_RUN_TIMEOUT_SEC:.0f}s waiting for completion"
        )
        log.warning("%s (chat_id=%s)", msg, chat_id)
        session = self._sessions.get(chat_id)
        await self._emit_lifecycle(
            chat_id,
            "post",
            msg,
            mode=session.mode if session else self._approval_mode,
            model=session.model if session else self._initial_model,
        )
        await self._cancel_turn(turn)
        raise AgentEventStreamTimeout(msg) from None

    async def _cancel_turn(self, turn: Any) -> None:
        for method_name in ("interrupt", "cancel", "close"):
            method = getattr(turn, method_name, None)
            if method is None:
                continue
            result = method()
            if hasattr(result, "__await__"):
                await result
            return

    async def _emit_lifecycle(
        self,
        chat_id: int,
        phase: str,
        status: str,
        *,
        mode: str,
        model: str | None,
    ) -> None:
        if self._on_tool_event is None:
            return
        payload: dict[str, Any] = {
            "tool_input": {
                "status": status,
                "mode": mode,
                "model": model or "default",
            }
        }
        if phase == "post":
            payload["tool_response"] = status
        await self._on_tool_event(chat_id, phase, "Codex", payload)

    async def _handle_sdk_notification(self, chat_id: int, event: Any) -> None:
        method = str(getattr(event, "method", ""))
        payload = self._to_plain(getattr(event, "payload", None))
        if isinstance(payload, dict):
            await self.handle_app_server_event(
                chat_id,
                {"method": method, "params": payload},
            )

    def _to_plain(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if isinstance(value, dict) and set(value) == {"root"}:
            return self._to_plain(value["root"])
        if isinstance(value, dict):
            return {key: self._to_plain(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._to_plain(item) for item in value]
        return value

    def _prepare_prompt(self, prompt: str, mode: str) -> str:
        if mode != "plan":
            return prompt
        return (
            "Plan mode is active. First produce a concrete implementation plan "
            "and wait for explicit user approval before changing files or "
            "running mutating commands.\n\n"
            f"Task:\n{prompt}"
        )

    def _extract_final_response(self, result: Any) -> str:
        for attr in ("final_response", "final", "text", "output_text"):
            value = getattr(result, attr, None)
            if isinstance(value, str):
                return value
        if isinstance(result, dict):
            for key in ("final_response", "final", "text", "output_text"):
                value = result.get(key)
                if isinstance(value, str):
                    return value
        return str(result) if result is not None else ""

    def _final_assistant_response_from_items(self, items: list[dict[str, Any]]) -> str:
        fallback: str | None = None
        for item in reversed(items):
            if item.get("type") != "agentMessage":
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            if item.get("phase") == "final_answer":
                return text
            if fallback is None:
                fallback = text
        return fallback or ""

    async def _emit_result_tool_events(self, chat_id: int, result: Any) -> None:
        if self._on_tool_event is None:
            return
        for event in self._result_events(result):
            await self.handle_app_server_event(chat_id, event)

    def _result_events(self, result: Any) -> list[dict[str, Any]]:
        events = getattr(result, "events", None)
        if isinstance(events, list):
            return [e for e in events if isinstance(e, dict)]
        if isinstance(result, dict) and isinstance(result.get("events"), list):
            return [e for e in result["events"] if isinstance(e, dict)]
        return []

    async def handle_app_server_event(
        self, chat_id: int, event: dict[str, Any]
    ) -> str | None:
        """Normalize a Codex app-server event.

        Returns assistant text deltas when present and mirrors tool lifecycle
        events through the provider-neutral callback.
        """
        method = str(event.get("method") or event.get("type") or "")
        params = event.get("params") if isinstance(event.get("params"), dict) else event
        if not isinstance(params, dict):
            return None

        delta = self._extract_delta(method, params)
        if delta:
            return delta

        tool_event = self._extract_tool_event(method, params)
        if tool_event is not None and self._on_tool_event is not None:
            phase, name, payload = tool_event
            await self._on_tool_event(chat_id, phase, name, payload)
        return None

    def _extract_delta(self, method: str, params: dict[str, Any]) -> str | None:
        if "delta" not in method and "message" not in method:
            return None
        for key in ("delta", "text", "content"):
            value = params.get(key)
            if isinstance(value, str):
                return value
        item = params.get("item")
        if isinstance(item, dict):
            value = item.get("delta") or item.get("text")
            if isinstance(value, str):
                return value
        return None

    def _extract_tool_event(
        self, method: str, params: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]] | None:
        item = params.get("item")
        if not isinstance(item, dict):
            item = params
        item_type = str(item.get("type") or "")
        if not item_type:
            return None
        name = self._tool_name(item_type, item)
        if not name:
            return None
        if method.endswith("started") or item.get("status") == "inProgress":
            return ("pre", name, self._tool_payload(item))
        if method.endswith("completed") or item.get("status") in {
            "completed",
            "failed",
            "declined",
        }:
            return ("post", name, {"tool_response": item, "tool_input": self._tool_payload(item)})
        return None

    def _tool_name(self, item_type: str, item: dict[str, Any]) -> str:
        if item_type == "commandExecution":
            return "Bash"
        if item_type == "fileChange":
            return "Edit"
        if item_type == "mcpToolCall":
            return str(item.get("tool") or "MCP")
        if item_type == "dynamicToolCall":
            return str(item.get("tool") or "Tool")
        return ""

    def _tool_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for src, dst in (
            ("command", "command"),
            ("cwd", "cwd"),
            ("path", "file_path"),
            ("file", "file_path"),
            ("tool", "tool"),
            ("arguments", "arguments"),
        ):
            if src in item:
                payload[dst] = item[src]
        return payload

    async def get_context_usage(self, chat_id: int) -> dict[str, Any]:
        session = self._sessions.get(chat_id)
        model = self.current_model(chat_id) or "default"
        return {
            "percentage": 0.0,
            "totalTokens": 0,
            "maxTokens": 0,
            "model": model,
            "provider": self.provider,
            "categories": [],
            "threadId": self._thread_id(session.thread) if session else None,
        }

    async def set_permission_mode(self, chat_id: int, mode: str) -> None:
        if mode not in CODEX_MODES:
            raise ValueError(f"unsupported Codex mode: {mode}")
        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if session is None:
                await self._start_thread(chat_id)
                session = self._sessions[chat_id]
            session.mode = mode

    async def set_model(self, chat_id: int, model: str | None) -> None:
        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if session is None:
                self._initial_model = model
                return
            session.model = model
            setter = getattr(session.thread, "set_model", None)
            if setter is not None:
                result = setter(model)
                if hasattr(result, "__await__"):
                    await result

    async def interrupt(self, chat_id: int) -> bool:
        turn = self._active_turns.get(chat_id)
        if turn is None:
            return False
        for method_name in ("interrupt", "cancel", "close"):
            method = getattr(turn, method_name, None)
            if method is None:
                continue
            result = method()
            if hasattr(result, "__await__"):
                await result
            return True
        return False

    async def get_mcp_status(self, chat_id: int) -> dict[str, Any]:
        thread = await self._get_thread(chat_id)
        for method_name in ("get_mcp_status", "mcp_status"):
            method = getattr(thread, method_name, None)
            if method is None:
                continue
            result = method()
            if hasattr(result, "__await__"):
                result = await result
            return dict(result)
        return {"mcpServers": []}

    async def get_server_info(self, chat_id: int) -> dict[str, Any] | None:
        thread = await self._get_thread(chat_id)
        for method_name in ("get_server_info", "server_info", "info"):
            method = getattr(thread, method_name, None)
            if method is None:
                continue
            result = method()
            if hasattr(result, "__await__"):
                result = await result
            return dict(result) if result else None
        return {
            "provider": self.provider,
            "model": self.current_model(chat_id) or "default",
            "commands": [],
        }

    def current_mode(self, chat_id: int) -> str:
        session = self._sessions.get(chat_id)
        return session.mode if session else self._approval_mode

    def current_model(self, chat_id: int) -> str | None:
        session = self._sessions.get(chat_id)
        return session.model if session else self._initial_model

    def has_session(self, chat_id: int) -> bool:
        return chat_id in self._sessions

    async def new_session(self, chat_id: int) -> Session:
        await self.reset(chat_id)
        return self._store.create(chat_id)

    async def switch_session(self, chat_id: int, sid: str) -> Session | None:
        session = self._store.get_by_id(chat_id, sid)
        if session is None:
            return None
        await self.reset(chat_id)
        self._store.set_current(chat_id, session.id)
        return session

    async def delete_session(self, chat_id: int, sid: str) -> Session | None:
        target = self._store.get_by_id(chat_id, sid)
        if target is None:
            return None
        if self._store.current_id(chat_id) == target.id:
            await self.reset(chat_id)
        self._store.delete(chat_id, target.id)
        return target

    def list_sessions(self, chat_id: int) -> list[Session]:
        return self._store.all_sessions(chat_id)

    def current_session(self, chat_id: int) -> Session | None:
        return self._store.current(chat_id)

    async def generate_title(self, text: str) -> str | None:
        title = " ".join(text.split())[:_TITLE_MAX_LEN].strip()
        return title or None

    async def ask_ephemeral(
        self, chat_id: int, prompt: str, *, allowed_tools: tuple[str, ...]
    ) -> str:
        _ = (chat_id, prompt, allowed_tools)
        raise NotImplementedError("Codex backend has no ephemeral-session turn")

    async def reset(self, chat_id: int) -> None:
        lock = self._locks.get(chat_id)
        if chat_id in self._active_turns or (lock is not None and lock.locked()):
            await self._force_close_session(chat_id, reason="/new during active turn")
            return
        async with self._lock(chat_id):
            session = self._sessions.pop(chat_id, None)
            if session is not None:
                closer = getattr(session.thread, "close", None)
                if closer is not None:
                    result = closer()
                    if hasattr(result, "__await__"):
                        await result
        self._locks.pop(chat_id, None)

    async def _force_close_session(self, chat_id: int, *, reason: str) -> None:
        turn = self._active_turns.pop(chat_id, None)
        if turn is not None:
            with contextlib.suppress(Exception):
                await self._cancel_turn(turn)
        session = self._sessions.pop(chat_id, None)
        self._locks.pop(chat_id, None)
        if session is None:
            return
        log.warning("force closing Codex session for chat_id=%s: %s", chat_id, reason)
        closer = getattr(session.thread, "close", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                result = closer()
                if hasattr(result, "__await__"):
                    await result

    async def close_all(self) -> None:
        if self._gc_task is not None and not self._gc_task.done():
            self._gc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._gc_task
            self._gc_task = None
        for chat_id in list(self._sessions):
            await self.reset(chat_id)
        if self._codex is not None:
            exit_ = getattr(self._codex, "__aexit__", None)
            if exit_ is not None:
                await exit_(None, None, None)
            self._codex = None

    def _thread_id(self, thread: Any) -> str | None:
        for attr in ("id", "thread_id", "threadId"):
            value = getattr(thread, attr, None)
            if isinstance(value, str):
                return value
        if isinstance(thread, dict):
            for key in ("id", "thread_id", "threadId"):
                value = thread.get(key)
                if isinstance(value, str):
                    return value
        return None
