"""PI.dev RPC backend adapter.

PI's public SDK is TypeScript-first, but its CLI exposes a JSONL RPC mode.
This adapter keeps that subprocess protocol behind the provider-neutral
AgentBackend contract used by Telegram handlers.
"""

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .agent_types import ToolEventCallback

log = logging.getLogger(__name__)

PI_MODES: tuple[str, ...] = ("default", "read_only", "no_tools", "plan")
PI_MODELS: tuple[tuple[str, str], ...] = (("", ""),)
_READ_ONLY_TOOLS = ("read", "grep", "find", "ls")


class PiRpcTransport(Protocol):
    async def request(self, command: dict[str, Any]) -> dict[str, Any]: ...

    async def next_event(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class PiRpcProcess:
    """Small JSONL client for `pi --mode rpc`."""

    def __init__(
        self,
        *,
        cli_bin: str,
        cwd: str | None,
        model: str | None,
        persist_session: bool,
    ) -> None:
        self._cli_bin = cli_bin
        self._cwd = cwd
        self._model = model
        self._persist_session = persist_session
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        args = [self._cli_bin, "--mode", "rpc"]
        if not self._persist_session:
            args.append("--no-session")
        if self._model:
            args.extend(["--model", self._model])
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())

    async def request(self, command: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_started()
        req_id = str(command.get("id") or uuid.uuid4())
        command = dict(command)
        command["id"] = req_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut
        try:
            await self._write_json(command)
            return await fut
        finally:
            self._pending.pop(req_id, None)

    async def next_event(self) -> dict[str, Any]:
        await self._ensure_started()
        return await self._events.get()

    async def close(self) -> None:
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
            self._reader_task = None
        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=3)
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("PI RPC process closed"))
        self._pending.clear()

    async def _ensure_started(self) -> None:
        if self._proc is None:
            await self.start()
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("PI RPC process failed to start")
        if self._proc.returncode is not None:
            raise RuntimeError(f"PI RPC process exited with code {self._proc.returncode}")

    async def _write_json(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("PI RPC stdin unavailable")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        async with self._write_lock:
            self._proc.stdin.write((line + "\n").encode("utf-8"))
            await self._proc.stdin.drain()

    async def _read_stdout(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").removesuffix("\n")
                line = line.removesuffix("\r")
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("invalid PI RPC JSONL: %r", line[:500])
                    continue
                if isinstance(payload, dict) and payload.get("type") == "response":
                    req_id = payload.get("id")
                    fut = self._pending.get(str(req_id)) if req_id is not None else None
                    if fut is not None and not fut.done():
                        fut.set_result(payload)
                        continue
                if isinstance(payload, dict):
                    self._events.put_nowait(payload)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("PI RPC stdout closed"))


@dataclass(slots=True)
class _PiSession:
    transport: PiRpcTransport
    last_used: float
    model: str | None
    mode: str
    state: dict[str, Any] | None = None
    commands: list[dict[str, Any]] | None = None
    models: tuple[tuple[str, str], ...] | None = None


class PiAgentBackend:
    provider = "pi"

    def __init__(
        self,
        *,
        system_prompt: str,
        cwd: str | None = None,
        idle_ttl_sec: int = 86400,
        add_dirs: list[str] | None = None,
        on_tool_event: ToolEventCallback | None = None,
        initial_model: str | None = None,
        cli_bin: str | None = None,
        tools_mode: str = "default",
        session_persistence: bool = False,
        transport_factory: Callable[[str | None], PiRpcTransport] | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._idle_ttl = idle_ttl_sec
        self._add_dirs = list(add_dirs) if add_dirs else []
        self._on_tool_event = on_tool_event
        self._initial_model = initial_model
        self._cli_bin = cli_bin
        self._tools_mode = tools_mode
        self._session_persistence = session_persistence
        self._transport_factory = transport_factory
        self._sessions: dict[int, _PiSession] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._gc_task: asyncio.Task[None] | None = None
        self._active: set[int] = set()

    def available_modes(self) -> tuple[str, ...]:
        return PI_MODES

    def available_models(self) -> tuple[tuple[str, str], ...]:
        cached: list[tuple[str, str]] = []
        for session in self._sessions.values():
            if session.models:
                cached.extend(session.models)
                break
        return tuple(cached) if cached else PI_MODELS

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
            session = self._sessions.pop(chat_id, None)
            self._locks.pop(chat_id, None)
            if session is not None:
                await session.transport.close()
                log.info("idle gc: closed PI RPC session for chat_id=%s", chat_id)

    async def ask(self, chat_id: int, prompt: str) -> str:
        chunks: list[str] = []
        async for chunk in self.ask_stream(chat_id, prompt):
            chunks.append(chunk)
        return "".join(chunks).strip() or "(empty response)"

    async def ask_stream(self, chat_id: int, prompt: str) -> AsyncIterator[str]:
        async with self._lock(chat_id):
            session = await self._get_session(chat_id)
            run_prompt = self._prepare_prompt(prompt, session.mode)
            command: dict[str, Any] = {"type": "prompt", "message": run_prompt}
            images = self._extract_images(prompt)
            if images:
                command["images"] = images
            self._active.add(chat_id)
            saw_delta = False
            try:
                response = await session.transport.request(command)
                if not bool(response.get("success")):
                    raise RuntimeError(str(response.get("error") or response))
                while True:
                    event = await session.transport.next_event()
                    delta = await self._handle_event(chat_id, event)
                    if delta:
                        saw_delta = True
                        yield delta
                    if event.get("type") == "agent_end":
                        break
                if not saw_delta:
                    text = await self._last_assistant_text(session)
                    if text:
                        yield text
            finally:
                self._active.discard(chat_id)
                session.last_used = time.monotonic()

    def _prepare_prompt(self, prompt: str, mode: str) -> str:
        parts: list[str] = []
        if self._system_prompt:
            parts.append(f"System instructions for this Telegram bot:\n{self._system_prompt}")
        if mode == "plan":
            parts.append(
                "Plan mode is active. Produce a concrete implementation plan first. "
                "Do not change files and do not run mutating commands until the user "
                "explicitly approves the plan in a later message."
            )
        elif mode == "read_only":
            parts.append(
                "Read-only mode is active. Use only non-mutating inspection tools "
                f"({', '.join(_READ_ONLY_TOOLS)}) and do not edit files or run mutating commands."
            )
        elif mode == "no_tools":
            parts.append("No-tools mode is active. Answer from context without using tools.")
        parts.append(prompt)
        return "\n\n".join(part for part in parts if part)

    async def _handle_event(self, chat_id: int, event: dict[str, Any]) -> str | None:
        event_type = str(event.get("type") or "")
        if event_type == "message_update":
            assistant_event = event.get("assistantMessageEvent")
            if (
                isinstance(assistant_event, dict)
                and assistant_event.get("type") == "text_delta"
            ):
                delta = assistant_event.get("delta")
                return delta if isinstance(delta, str) else None
        tool_event = self._extract_tool_event(event)
        if tool_event is not None and self._on_tool_event is not None:
            phase, name, payload = tool_event
            await self._on_tool_event(chat_id, phase, name, payload)
        return None

    def _extract_tool_event(
        self, event: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]] | None:
        event_type = str(event.get("type") or "")
        if not event_type.startswith("tool_execution_"):
            return None
        tool_name = str(event.get("toolName") or "Tool")
        payload = {
            "tool_input": event.get("args") or {},
            "tool_call_id": event.get("toolCallId"),
        }
        if event_type == "tool_execution_start":
            return ("pre", tool_name, payload)
        if event_type == "tool_execution_end":
            return (
                "post",
                tool_name,
                {
                    **payload,
                    "tool_response": event.get("result") or {},
                    "is_error": bool(event.get("isError")),
                },
            )
        if event_type == "tool_execution_update":
            return (
                "post",
                tool_name,
                {
                    **payload,
                    "tool_response": event.get("partialResult") or {},
                    "partial": True,
                },
            )
        return None

    async def _last_assistant_text(self, session: _PiSession) -> str | None:
        response = await session.transport.request({"type": "get_last_assistant_text"})
        data = response.get("data")
        text = data.get("text") if isinstance(data, dict) else None
        if isinstance(text, str):
            return text
        return None

    def _extract_images(self, prompt: str) -> list[dict[str, str]]:
        images: list[dict[str, str]] = []
        for path in self._attachment_image_paths(prompt):
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if not mime.startswith("image/"):
                continue
            try:
                data = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError:
                continue
            images.append({"type": "image", "data": data, "mimeType": mime})
        return images

    def _attachment_image_paths(self, prompt: str) -> list[Path]:
        paths: list[Path] = []
        for line in prompt.splitlines():
            if "(image," not in line:
                continue
            _, _, tail = line.partition(". ")
            raw_path, _, _ = tail.partition(" (image,")
            if raw_path:
                paths.append(Path(raw_path))
        return paths

    async def _get_session(self, chat_id: int) -> _PiSession:
        self._ensure_gc_running()
        session = self._sessions.get(chat_id)
        if session is not None:
            session.last_used = time.monotonic()
            return session
        transport = await self._create_transport(self._initial_model)
        session = _PiSession(
            transport=transport,
            last_used=time.monotonic(),
            model=self._initial_model,
            mode=self._tools_mode,
        )
        self._sessions[chat_id] = session
        return session

    async def _create_transport(self, model: str | None) -> PiRpcTransport:
        if self._transport_factory is not None:
            return self._transport_factory(model)
        cli_bin = self._cli_bin or shutil.which("pi")
        if cli_bin is None:
            raise RuntimeError(
                "PI backend requires the pi CLI. Install PI.dev CLI or set pi_cli_bin."
            )
        transport = PiRpcProcess(
            cli_bin=cli_bin,
            cwd=self._cwd,
            model=model,
            persist_session=self._session_persistence,
        )
        await transport.start()
        return transport

    async def get_context_usage(self, chat_id: int) -> dict[str, Any]:
        session = await self._get_session(chat_id)
        stats = await self._request_optional(session, {"type": "get_session_stats"})
        state = await self._state(session)
        context = self._dig(stats, "contextUsage")
        tokens = self._dig(stats, "tokens")
        total = int(self._pick(context, "tokens", default=0) or 0)
        max_tokens = int(self._pick(context, "contextWindow", default=0) or 0)
        pct = float(self._pick(context, "percent", default=0.0) or 0.0)
        if total == 0 and isinstance(tokens, dict):
            total = int(tokens.get("total") or 0)
        return {
            "percentage": pct,
            "totalTokens": total,
            "maxTokens": max_tokens,
            "model": self._state_model(state) or self.current_model(chat_id) or "default",
            "provider": self.provider,
            "categories": [],
            "sessionId": self._pick(state, "sessionId"),
            "sessionFile": self._pick(state, "sessionFile"),
            "messageCount": self._pick(state, "messageCount", "totalMessages"),
            "pendingMessageCount": self._pick(state, "pendingMessageCount"),
            "isStreaming": self._pick(state, "isStreaming"),
        }

    async def set_permission_mode(self, chat_id: int, mode: str) -> None:
        if mode not in PI_MODES:
            raise ValueError(f"unsupported PI mode: {mode}")
        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if session is None:
                session = await self._get_session(chat_id)
            session.mode = mode

    async def set_model(self, chat_id: int, model: str | None) -> None:
        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if session is None:
                self._initial_model = model
                return
            response = await session.transport.request(self._set_model_command(model))
            if not bool(response.get("success")):
                raise RuntimeError(str(response.get("error") or response))
            session.model = model

    def _set_model_command(self, model: str | None) -> dict[str, Any]:
        if not model:
            return {"type": "set_model", "model": None}
        provider, sep, model_id = model.partition("/")
        if sep:
            model_id, _, thinking = model_id.partition(":")
            command: dict[str, Any] = {
                "type": "set_model",
                "provider": provider,
                "modelId": model_id,
            }
            if thinking:
                command["thinkingLevel"] = thinking
            return command
        return {"type": "set_model", "model": model}

    async def interrupt(self, chat_id: int) -> bool:
        session = self._sessions.get(chat_id)
        if session is None or chat_id not in self._active:
            return False
        with contextlib.suppress(Exception):
            await session.transport.request({"type": "abort_bash"})
        response = await session.transport.request({"type": "abort"})
        return bool(response.get("success"))

    async def get_mcp_status(self, chat_id: int) -> dict[str, Any]:
        await self._get_session(chat_id)
        return {"mcpServers": []}

    async def get_server_info(self, chat_id: int) -> dict[str, Any] | None:
        session = await self._get_session(chat_id)
        state = await self._state(session)
        commands = await self._commands(session)
        await self._models(session)
        return {
            "provider": self.provider,
            "model": self._state_model(state) or self.current_model(chat_id) or "default",
            "cwd": self._cwd,
            "mode": session.mode,
            "sessionId": self._pick(state, "sessionId"),
            "sessionFile": self._pick(state, "sessionFile"),
            "isStreaming": self._pick(state, "isStreaming"),
            "commands": commands,
        }

    def current_mode(self, chat_id: int) -> str:
        session = self._sessions.get(chat_id)
        return session.mode if session else self._tools_mode

    def current_model(self, chat_id: int) -> str | None:
        session = self._sessions.get(chat_id)
        return session.model if session else self._initial_model

    def has_session(self, chat_id: int) -> bool:
        return chat_id in self._sessions

    async def reset(self, chat_id: int) -> None:
        async with self._lock(chat_id):
            session = self._sessions.get(chat_id)
            if session is None:
                return
            if self._session_persistence:
                response = await session.transport.request({"type": "new_session"})
                if not bool(response.get("success")):
                    raise RuntimeError(str(response.get("error") or response))
                session.state = None
                session.commands = None
                session.last_used = time.monotonic()
                return
            self._sessions.pop(chat_id, None)
            await session.transport.close()
        self._locks.pop(chat_id, None)

    async def close_all(self) -> None:
        if self._gc_task is not None and not self._gc_task.done():
            self._gc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._gc_task
            self._gc_task = None
        for chat_id in list(self._sessions):
            session = self._sessions.pop(chat_id)
            await session.transport.close()
            self._locks.pop(chat_id, None)

    async def _state(self, session: _PiSession) -> dict[str, Any]:
        response = await self._request_optional(session, {"type": "get_state"})
        session.state = response if response else session.state
        return session.state or {}

    async def _commands(self, session: _PiSession) -> list[dict[str, Any]]:
        if session.commands is not None:
            return session.commands
        response = await self._request_optional(session, {"type": "get_commands"})
        commands = response.get("commands") if isinstance(response, dict) else None
        session.commands = [
            c for c in commands if isinstance(c, dict)
        ] if isinstance(commands, list) else []
        return session.commands

    async def _models(self, session: _PiSession) -> tuple[tuple[str, str], ...]:
        if session.models is not None:
            return session.models
        response = await self._request_optional(
            session, {"type": "get_available_models"}
        )
        raw_models = response.get("models") if isinstance(response, dict) else None
        models: list[tuple[str, str]] = [("", "")]
        if isinstance(raw_models, list):
            for item in raw_models:
                model_id, label = self._model_choice(item)
                if model_id:
                    models.append((model_id, label or model_id))
        session.models = tuple(models)
        return session.models

    def _model_choice(self, item: object) -> tuple[str, str]:
        if isinstance(item, str):
            return item, item
        if not isinstance(item, dict):
            return "", ""
        provider = item.get("provider") or item.get("providerId")
        model_id = item.get("id") or item.get("modelId") or item.get("name")
        if not model_id:
            return "", ""
        value = f"{provider}/{model_id}" if provider else str(model_id)
        label = item.get("displayName") or item.get("label") or item.get("name")
        return value, str(label or value)

    async def _request_optional(
        self, session: _PiSession, command: dict[str, Any]
    ) -> dict[str, Any]:
        response = await session.transport.request(command)
        if not bool(response.get("success")):
            return {}
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _dig(payload: dict[str, Any], key: str) -> dict[str, Any]:
        value = payload.get(key)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _pick(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return default

    @staticmethod
    def _state_model(state: dict[str, Any]) -> str | None:
        model = state.get("model")
        if isinstance(model, str):
            return model
        if isinstance(model, dict):
            provider = model.get("provider") or model.get("providerId")
            model_id = model.get("id") or model.get("modelId") or model.get("name")
            if provider and model_id:
                return f"{provider}/{model_id}"
            if model_id:
                return str(model_id)
        return None
