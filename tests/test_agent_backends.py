"""Agent backends: factory selection, Codex/PI/Claude streaming and lifecycle."""

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

import src.infra.pi_agent as pi_agent_module
from src.config import BotConfig
from src.infra.agent_factory import create_agent_backend
from src.infra.agent_types import StreamChunk
from src.infra.claude_agent import ClaudeAgentBackend
from src.infra.codex_agent import CodexAgentBackend
from src.infra.pi_agent import PiAgentBackend
from src.infra.session_store import SessionStore


def _store() -> SessionStore:
    """Build a SessionStore rooted at a fresh temp directory."""
    return SessionStore(Path(tempfile.mkdtemp()), default_title="Новая сессия")


def _cfg(**overrides: object) -> BotConfig:
    """Build a minimal BotConfig, overridable per call."""
    payload: dict[str, object] = {
        "name": "test",
        "telegram_bot_token": "1:abc",
    }
    payload.update(overrides)
    return BotConfig.model_validate(payload)


def test_factory_creates_claude_backend_by_default() -> None:
    """Verify the factory builds a Claude backend by default."""
    backend = create_agent_backend(
        _cfg(),
        session_store=_store(),
        on_permission=None,
        system_prompt="x",
        add_dirs=[],
        on_tool_event=None,
    )
    assert isinstance(backend, ClaudeAgentBackend)


def test_factory_creates_codex_backend() -> None:
    """Verify the factory builds a Codex backend when configured."""
    backend = create_agent_backend(
        _cfg(agent_provider="codex"),
        session_store=_store(),
        on_permission=None,
        system_prompt="x",
        add_dirs=[],
        on_tool_event=None,
        codex_factory=lambda: _FakeCodex(),
    )
    assert isinstance(backend, CodexAgentBackend)


def test_factory_creates_pi_backend() -> None:
    """Verify the factory builds a PI backend when configured."""
    backend = create_agent_backend(
        _cfg(agent_provider="pi"),
        session_store=_store(),
        on_permission=None,
        system_prompt="x",
        add_dirs=[],
        on_tool_event=None,
        pi_transport_factory=lambda _model: _FakePiTransport(),
    )
    assert isinstance(backend, PiAgentBackend)


async def test_claude_resume_failure_falls_back_to_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a failed resume falls back to a freshly minted session."""
    from claude_agent_sdk import ProcessError

    import src.infra.claude_agent as claude_module

    created: list[Any] = []

    class _FakeClient:
        """Stub SDK client that fails to enter when given a resume id."""

        def __init__(self, options: Any) -> None:
            """Record the options and register this client instance."""
            self.options = options
            created.append(self)

        async def __aenter__(self) -> "_FakeClient":
            """Enter the context, raising if a resume id was requested."""
            if self.options.resume is not None:
                raise ProcessError("boom", exit_code=1)
            return self

        async def __aexit__(self, *_args: object) -> None:
            """Exit the context."""
            return

    monkeypatch.setattr(claude_module, "ClaudeSDKClient", _FakeClient)

    store = _store()
    await store.set_current(42, "missing-session-id")
    backend = ClaudeAgentBackend(store, system_prompt="x", idle_ttl_sec=0)

    client = await backend._get_client(42)

    # Resume client built first (raised), fresh session client built second.
    assert len(created) == 2
    assert created[0].options.resume == "missing-session-id"
    assert created[1].options.resume is None
    assert created[1].options.session_id is not None
    assert client is created[1]
    # Store now points at the freshly minted session, not the broken one.
    assert await store.current_id(42) == created[1].options.session_id


@pytest.mark.asyncio
async def test_claude_backend_times_out_silent_event_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a silent Claude event stream times out and interrupts the turn."""
    import src.infra.claude_agent as claude_module

    created: list[Any] = []

    class _SilentClient:
        """Stub SDK client whose event stream never yields."""

        def __init__(self, options: Any) -> None:
            """Record the options and register this client instance."""
            self.options = options
            self.interrupted = False
            created.append(self)

        async def __aenter__(self) -> "_SilentClient":
            """Enter the context."""
            return self

        async def __aexit__(self, *_args: object) -> None:
            """Exit the context."""
            return

        async def query(self, _prompt: str) -> None:
            """Accept the prompt without emitting anything."""
            return

        async def interrupt(self) -> None:
            """Record that the stalled turn was interrupted."""
            self.interrupted = True

        async def _stream(self) -> Any:
            """Yield nothing — block until cancelled."""
            await asyncio.sleep(3600)
            yield  # pragma: no cover - never reached

        def receive_response(self) -> Any:
            """Return the never-yielding event stream."""
            return self._stream()

    monkeypatch.setattr(claude_module, "ClaudeSDKClient", _SilentClient)

    backend = ClaudeAgentBackend(
        _store(), system_prompt="x", idle_ttl_sec=0, event_timeout_sec=0.01
    )

    with pytest.raises(RuntimeError, match="event stream timed out"):
        _ = [chunk async for chunk in backend.ask_stream(10, "hello")]

    assert len(created) == 1
    assert created[0].interrupted is True


async def test_is_busy_tracks_per_chat_lock() -> None:
    """is_busy reflects whether the chat's lock is held by an in-flight turn."""
    backend = ClaudeAgentBackend(_store(), system_prompt="x", idle_ttl_sec=0)

    # No lock yet for an untouched chat.
    assert backend.is_busy(7) is False

    lock = backend._lock(7)
    await lock.acquire()
    try:
        assert backend.is_busy(7) is True
        # A different chat is unaffected.
        assert backend.is_busy(8) is False
    finally:
        lock.release()

    assert backend.is_busy(7) is False


class _FakeResult:
    """Stub Codex run result carrying canned event records."""

    final_response = "done"

    def __init__(self) -> None:
        """Populate the canned command-execution events."""
        self.events = [
            {
                "method": "item/started",
                "params": {
                    "item": {
                        "type": "commandExecution",
                        "status": "inProgress",
                        "command": "pytest",
                    }
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "commandExecution",
                        "status": "completed",
                        "command": "pytest",
                        "output": "ok",
                    }
                },
            },
        ]


class _FakeNotification:
    """Stub Codex stream notification with a method and payload."""

    def __init__(self, method: str, payload: dict[str, Any]) -> None:
        """Store the notification method and payload."""
        self.method = method
        self.payload = payload


class _FakeStreamTurn:
    """Stub Codex turn that streams a fixed sequence of notifications."""

    def __init__(self) -> None:
        """Populate the canned notification sequence and reset flags."""
        self.cancelled = False
        self.closed = False
        self._events = [
            _FakeNotification(
                "item/started",
                {
                    "item": {
                        "type": "commandExecution",
                        "status": "inProgress",
                        "command": "pytest",
                    }
                },
            ),
            _FakeNotification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "status": "completed",
                        "command": "pytest",
                        "aggregated_output": "ok",
                    }
                },
            ),
            _FakeNotification(
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "done",
                    }
                },
            ),
            _FakeNotification(
                "turn/completed",
                {"turn": {"id": "turn-1", "status": "completed"}},
            ),
        ]

    def stream(self) -> Any:
        """Return the async event stream."""
        return self._stream()

    async def _stream(self) -> Any:
        """Yield the canned notifications one at a time."""
        for event in self._events:
            await asyncio.sleep(0)
            yield event

    async def cancel(self) -> None:
        """Mark the turn as cancelled."""
        self.cancelled = True

    async def close(self) -> None:
        """Mark the turn as closed."""
        self.closed = True


class _ThreadBase:
    """Base stub Codex thread tracking prompts, model, and close state."""

    id = "thread-1"

    def __init__(self) -> None:
        """Reset prompt history, model, and closed flag."""
        self.prompts: list[str] = []
        self.model: str | None = None
        self.closed = False

    async def set_model(self, model: str | None) -> None:
        """Record the requested model."""
        self.model = model

    async def close(self) -> None:
        """Mark the thread as closed."""
        self.closed = True


class _FakeThread(_ThreadBase):
    """Stub thread whose run returns a canned result."""

    async def run(self, prompt: str) -> _FakeResult:
        """Record the prompt and return a canned result."""
        self.prompts.append(prompt)
        return _FakeResult()


class _StreamingThread(_FakeThread):
    """Stub thread that exposes a streaming turn handle."""

    def __init__(self) -> None:
        """Attach a streaming turn handle."""
        super().__init__()
        self.turn_handle = _FakeStreamTurn()

    async def turn(self, prompt: str) -> _FakeStreamTurn:
        """Record the prompt and return the streaming turn handle."""
        self.prompts.append(prompt)
        return self.turn_handle


class _SlowTurn:
    """Stub turn that blocks until cancelled or closed."""

    def __init__(self) -> None:
        """Reset flags and the completion event."""
        self.cancelled = False
        self.closed = False
        self._done = asyncio.Event()

    def __await__(self) -> Any:
        """Await the turn's completion."""
        return self._wait().__await__()

    async def _wait(self) -> _FakeResult:
        """Block until done, then return a canned result."""
        await self._done.wait()
        return _FakeResult()

    async def cancel(self) -> None:
        """Mark the turn cancelled and release any waiter."""
        self.cancelled = True
        self._done.set()

    async def close(self) -> None:
        """Mark the turn closed and release any waiter."""
        self.closed = True
        self._done.set()


class _SlowThread(_ThreadBase):
    """Stub thread whose run blocks via a slow turn handle."""

    def __init__(self) -> None:
        """Attach a slow turn handle."""
        super().__init__()
        self.turn_handle = _SlowTurn()

    def run(self, prompt: str) -> _SlowTurn:
        """Record the prompt and return the slow turn handle."""
        self.prompts.append(prompt)
        return self.turn_handle


class _SlowCodex:
    """Stub Codex client whose threads block until cancelled."""

    def __init__(self) -> None:
        """Attach a slow thread and reset start kwargs."""
        self.thread = _SlowThread()
        self.thread_start_kwargs: dict[str, object] = {}

    async def __aenter__(self) -> "_SlowCodex":
        """Enter the client context."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Exit the client context."""
        return

    async def thread_start(self, **_kwargs: object) -> _SlowThread:
        """Record the start kwargs and return the slow thread."""
        self.thread_start_kwargs = dict(_kwargs)
        return self.thread


class _FakeCodex:
    """Stub Codex client returning a canned-result thread."""

    def __init__(self) -> None:
        """Attach a fake thread and reset start kwargs."""
        self.thread = _FakeThread()
        self.thread_start_kwargs: dict[str, object] = {}

    async def __aenter__(self) -> "_FakeCodex":
        """Enter the client context."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Exit the client context."""
        return

    async def thread_start(self, **_kwargs: object) -> _FakeThread:
        """Record the start kwargs and return the fake thread."""
        self.thread_start_kwargs = dict(_kwargs)
        return self.thread


class _StreamingCodex(_FakeCodex):
    """Stub Codex client whose thread streams notifications."""

    thread: _StreamingThread

    def __init__(self) -> None:
        """Attach a streaming thread and reset start kwargs."""
        self.thread = _StreamingThread()
        self.thread_start_kwargs: dict[str, object] = {}

    async def thread_start(self, **_kwargs: object) -> _StreamingThread:
        """Record the start kwargs and return the streaming thread."""
        self.thread_start_kwargs = dict(_kwargs)
        return self.thread


class _FakePiTransport:
    """Stub PI transport replaying a canned event/response script."""

    def __init__(self) -> None:
        """Seed the event queue with a canned interaction script."""
        self.requests: list[dict[str, Any]] = []
        self.closed = False
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.events.put_nowait(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "hel",
                },
            }
        )
        self.events.put_nowait(
            {
                "type": "tool_execution_start",
                "toolCallId": "call-1",
                "toolName": "bash",
                "args": {"command": "ls"},
            }
        )
        self.events.put_nowait(
            {
                "type": "tool_execution_end",
                "toolCallId": "call-1",
                "toolName": "bash",
                "args": {"command": "ls"},
                "result": {"content": [{"type": "text", "text": "ok"}]},
                "isError": False,
            }
        )
        self.events.put_nowait(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "lo",
                },
            }
        )
        self.events.put_nowait({"type": "agent_end", "messages": []})

    async def request(self, command: dict[str, Any]) -> dict[str, Any]:
        """Record the command and return a canned response for its type."""
        self.requests.append(command)
        command_type = command["type"]
        if command_type == "get_last_assistant_text":
            return {
                "type": "response",
                "command": command_type,
                "success": True,
                "data": {"text": "fallback"},
            }
        if command_type == "get_state":
            return {
                "type": "response",
                "command": command_type,
                "success": True,
                "data": {
                    "sessionId": "sess-1",
                    "sessionFile": "/private/var/pi-test/sess.jsonl",
                    "messageCount": 4,
                    "pendingMessageCount": 0,
                    "isStreaming": False,
                    "model": {"provider": "openai", "id": "gpt-5.5"},
                },
            }
        if command_type == "get_session_stats":
            return {
                "type": "response",
                "command": command_type,
                "success": True,
                "data": {
                    "contextUsage": {
                        "tokens": 1000,
                        "contextWindow": 2000,
                        "percent": 50,
                    }
                },
            }
        if command_type == "get_commands":
            return {
                "type": "response",
                "command": command_type,
                "success": True,
                "data": {"commands": [{"name": "fix-tests"}]},
            }
        if command_type == "get_available_models":
            return {
                "type": "response",
                "command": command_type,
                "success": True,
                "data": {
                    "models": [
                        {
                            "provider": "openai",
                            "id": "gpt-5.5",
                            "displayName": "GPT-5.5",
                        }
                    ]
                },
            }
        return {"type": "response", "command": command_type, "success": True}

    async def next_event(self) -> dict[str, Any]:
        """Return the next queued event."""
        return await self.events.get()

    async def close(self) -> None:
        """Mark the transport as closed."""
        self.closed = True


class _SilentPiTransport:
    """Stub PI transport that acks requests but never emits events."""

    def __init__(self) -> None:
        """Reset the request log."""
        self.requests: list[dict[str, Any]] = []

    async def request(self, command: dict[str, Any]) -> dict[str, Any]:
        """Record the command and return a generic success response."""
        self.requests.append(command)
        return {"type": "response", "command": command["type"], "success": True}

    async def next_event(self) -> dict[str, Any]:
        """Block forever, never yielding an event."""
        await asyncio.Event().wait()
        return {}

    async def close(self) -> None:
        """Close the transport (no-op)."""
        return


class _ClosableSilentPiTransport(_SilentPiTransport):
    """Silent PI transport whose close unblocks the event stream."""

    def __init__(self) -> None:
        """Add a closed flag and an event queue."""
        super().__init__()
        self.closed = False
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def next_event(self) -> dict[str, Any]:
        """Return the next queued event."""
        return await self.events.get()

    async def close(self) -> None:
        """Mark closed and emit an rpc_closed event to unblock waiters."""
        self.closed = True
        self.events.put_nowait({"type": "rpc_closed"})


@pytest.mark.asyncio
async def test_codex_backend_thread_lifecycle_and_stream() -> None:
    """Verify the Codex backend streams text, emits tool events, and resets."""
    events: list[tuple[int, str, str, dict[str, Any]]] = []
    fake = _StreamingCodex()
    backend = CodexAgentBackend(
        session_store=_store(),
        system_prompt="system",
        on_tool_event=lambda *args: _record(events, *args),
        codex_factory=lambda: fake,
    )

    chunks = [chunk async for chunk in backend.ask_stream(10, "hello")]

    assert len(chunks) == 1
    assert chunks[0].kind == "text"
    assert chunks[0].text == "done"
    assert backend.has_session(10) is True
    assert fake.thread.prompts == ["hello"]
    assert [(phase, tool) for _, phase, tool, _ in events] == [
        ("pre", "Codex"),
        ("pre", "Bash"),
        ("post", "Bash"),
        ("post", "Codex"),
    ]

    await backend.set_model(10, "gpt-5.4")
    assert backend.current_model(10) == "gpt-5.4"
    assert fake.thread.model == "gpt-5.4"

    await backend.reset(10)
    assert backend.has_session(10) is False
    assert fake.thread.closed is True


@pytest.mark.asyncio
async def test_codex_idle_gc_closes_thread() -> None:
    """Verify the idle sweep closes a dropped Codex thread."""
    fake = _FakeCodex()
    backend = CodexAgentBackend(
        session_store=_store(),
        system_prompt="system",
        idle_ttl_sec=3600,
        codex_factory=lambda: fake,
    )

    await backend.ask(10, "hello")
    assert backend.has_session(10) is True

    # Force the session past its TTL, then run the idle sweep directly: the
    # dropped thread must be closed, not just forgotten (no resource leak).
    backend._sessions[10].last_used -= 7200
    await backend._gc_idle()

    assert backend.has_session(10) is False
    assert fake.thread.closed is True


@pytest.mark.asyncio
async def test_codex_backend_passes_working_dir_to_thread_start() -> None:
    """Verify the Codex backend forwards cwd, model, sandbox, and approval mode."""
    fake = _FakeCodex()
    backend = CodexAgentBackend(
        session_store=_store(),
        system_prompt="system",
        cwd="/Users/example/Brain",
        initial_model="gpt-5.5",
        sandbox="workspace_write",
        approval_mode="never",
        codex_factory=lambda: fake,
    )

    _ = [chunk async for chunk in backend.ask_stream(10, "hello")]

    assert fake.thread_start_kwargs["cwd"] == "/Users/example/Brain"
    assert fake.thread_start_kwargs["model"] == "gpt-5.5"
    assert fake.thread_start_kwargs["sandbox"] == "workspace_write"
    assert fake.thread_start_kwargs["approval_mode"] == "never"


@pytest.mark.asyncio
async def test_codex_backend_times_out_silent_run() -> None:
    """Verify a silent Codex run times out and cancels the turn."""
    events: list[tuple[int, str, str, dict[str, Any]]] = []
    fake = _SlowCodex()
    backend = CodexAgentBackend(
        session_store=_store(),
        system_prompt="system",
        on_tool_event=lambda *args: _record(events, *args),
        codex_factory=lambda: fake,
        event_timeout_sec=0.01,
    )

    with pytest.raises(RuntimeError, match="Codex run timed out"):
        _ = [chunk async for chunk in backend.ask_stream(10, "hello")]

    assert fake.thread.turn_handle.cancelled is True
    assert [(phase, tool) for _, phase, tool, _ in events] == [
        ("pre", "Codex"),
        ("post", "Codex"),
    ]


@pytest.mark.asyncio
async def test_codex_reset_force_closes_active_session() -> None:
    """Verify resetting an active Codex session cancels and closes its turn."""
    fake = _SlowCodex()
    backend = CodexAgentBackend(
        session_store=_store(),
        system_prompt="system",
        codex_factory=lambda: fake,
    )

    async def consume() -> list[StreamChunk]:
        """Drain the backend's stream into a list of chunks."""
        return [chunk async for chunk in backend.ask_stream(10, "hello")]

    task = asyncio.create_task(consume())
    for _ in range(50):
        if fake.thread.prompts:
            break
        await asyncio.sleep(0.01)

    assert fake.thread.prompts == ["hello"]

    await backend.reset(10)

    assert fake.thread.turn_handle.cancelled is True
    assert fake.thread.closed is True
    assert backend.has_session(10) is False
    with pytest.raises(RuntimeError, match="Codex session reset"):
        await task


@pytest.mark.asyncio
async def test_pi_backend_streams_events_and_tool_status() -> None:
    """Verify the PI backend streams text deltas and emits tool events."""
    events: list[tuple[int, str, str, dict[str, Any]]] = []
    fake = _FakePiTransport()
    backend = PiAgentBackend(
        session_store=_store(),
        system_prompt="system",
        tools_mode="default",
        on_tool_event=lambda *args: _record(events, *args),
        transport_factory=lambda _model: fake,
    )

    chunks = [chunk async for chunk in backend.ask_stream(10, "hello")]

    assert [c.text for c in chunks] == ["hel", "lo"]
    assert backend.has_session(10) is True
    prompt_request = fake.requests[0]
    assert prompt_request["type"] == "prompt"
    assert "system" in prompt_request["message"]
    assert [(phase, tool) for _, phase, tool, _ in events] == [
        ("pre", "PI"),
        ("pre", "bash"),
        ("post", "bash"),
        ("post", "PI"),
    ]


@pytest.mark.asyncio
async def test_pi_backend_context_info_model_and_reset() -> None:
    """Verify PI mode, model, context usage, server info, and reset behavior."""
    fake = _FakePiTransport()
    backend = PiAgentBackend(
        session_store=_store(),
        system_prompt="system",
        initial_model=None,
        tools_mode="read_only",
        session_persistence=False,
        transport_factory=lambda _model: fake,
    )

    await backend.set_permission_mode(10, "no_tools")
    assert backend.current_mode(10) == "no_tools"

    await backend.set_model(10, "openai/gpt-5.5:high")
    assert backend.current_model(10) == "openai/gpt-5.5:high"
    assert fake.requests[-1] == {
        "type": "set_model",
        "provider": "openai",
        "modelId": "gpt-5.5",
        "thinkingLevel": "high",
    }

    usage = await backend.get_context_usage(10)
    assert usage["provider"] == "pi"
    assert usage["percentage"] == 50.0
    assert usage["totalTokens"] == 1000
    assert usage["maxTokens"] == 2000
    assert usage["model"] == "openai/gpt-5.5"

    info = await backend.get_server_info(10)
    assert info is not None
    assert info["provider"] == "pi"
    assert info["commands"] == [{"name": "fix-tests"}]
    assert ("openai/gpt-5.5", "GPT-5.5") in backend.available_models()

    await backend.reset(10)
    assert backend.has_session(10) is False
    assert fake.closed is True


@pytest.mark.asyncio
async def test_pi_backend_sends_image_attachments(tmp_path: Path) -> None:
    """Verify the PI backend sends attached images as base64 image parts."""
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    fake = _FakePiTransport()
    backend = PiAgentBackend(
        session_store=_store(),
        system_prompt="",
        transport_factory=lambda _model: fake,
    )
    prompt = (
        "The user attached the following files (use the Read tool to inspect them):\n"
        f"  1. {image} (image, original name: photo.png)\n\n"
        "User message:\nwhat is this?"
    )

    _ = [chunk async for chunk in backend.ask_stream(10, prompt)]

    request = fake.requests[0]
    assert request["type"] == "prompt"
    assert request["images"] == [
        {
            "type": "image",
            "data": "iVBORw0KGgo=",
            "mimeType": "image/png",
        }
    ]


@pytest.mark.asyncio
async def test_pi_backend_times_out_silent_event_stream() -> None:
    """Verify a silent PI event stream times out and aborts the run."""
    events: list[tuple[int, str, str, dict[str, Any]]] = []
    fake = _SilentPiTransport()
    backend = PiAgentBackend(
        session_store=_store(),
        system_prompt="",
        on_tool_event=lambda *args: _record(events, *args),
        transport_factory=lambda _model: fake,
        event_timeout_sec=0.01,
    )

    with pytest.raises(RuntimeError, match="event stream timed out"):
        _ = [chunk async for chunk in backend.ask_stream(10, "hello")]

    assert [request["type"] for request in fake.requests] == [
        "prompt",
        "abort_bash",
        "abort",
    ]
    assert [(phase, tool) for _, phase, tool, _ in events] == [
        ("pre", "PI"),
        ("post", "PI"),
    ]


@pytest.mark.asyncio
async def test_pi_reset_force_closes_active_session() -> None:
    """Verify resetting an active PI session closes its transport."""
    fake = _ClosableSilentPiTransport()
    backend = PiAgentBackend(
        session_store=_store(),
        system_prompt="",
        transport_factory=lambda _model: fake,
    )

    async def consume() -> list[StreamChunk]:
        """Drain the backend's stream into a list of chunks."""
        return [chunk async for chunk in backend.ask_stream(10, "hello")]

    task = asyncio.create_task(consume())
    for _ in range(50):
        if fake.requests:
            break
        await asyncio.sleep(0.01)

    assert fake.requests[0]["type"] == "prompt"

    await backend.reset(10)

    assert fake.closed is True
    assert backend.has_session(10) is False
    with pytest.raises(RuntimeError, match="PI RPC session reset"):
        await task


async def _record(
    events: list[tuple[int, str, str, dict[str, Any]]],
    chat_id: int,
    phase: str,
    tool_name: str,
    payload: dict[str, Any],
) -> None:
    """Append a tool event tuple to the captured events list."""
    events.append((chat_id, phase, tool_name, payload))


async def test_ask_ephemeral_leaves_session_untouched(monkeypatch: Any) -> None:
    """Verify ask_ephemeral runs without touching the chat's live session."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    import src.infra.claude_agent as claude_module

    captured: dict[str, Any] = {}

    async def _fake_query(*, prompt: str, options: Any) -> Any:
        """Capture the query args and yield a canned assistant message."""
        captured["prompt"] = prompt
        captured["options"] = options
        yield AssistantMessage(content=[TextBlock(text="done")], model="m")

    monkeypatch.setattr(claude_module, "query", _fake_query)

    store = _store()
    await store.create(123)  # establish a current session
    before = await store.current_id(123)

    backend = ClaudeAgentBackend(store, system_prompt="x")
    out = await backend.ask_ephemeral(123, "hi", allowed_tools=("Read",))

    assert out == "done"
    # The chat's live session / current pointer must be unchanged.
    assert await store.current_id(123) == before
    assert backend.has_session(123) is False  # no live client spun up

    # Permission callback allows listed tools, denies everything else.
    can_use = captured["options"].can_use_tool
    allow = await can_use("Read", {}, None)
    deny = await can_use("Bash", {}, None)
    assert type(allow).__name__ == "PermissionResultAllow"
    assert type(deny).__name__ == "PermissionResultDeny"


async def test_ask_ephemeral_not_supported_on_codex() -> None:
    """Verify ask_ephemeral raises NotImplementedError on the Codex backend."""
    backend = create_agent_backend(
        _cfg(agent_provider="codex"),
        session_store=_store(),
        on_permission=None,
        system_prompt="x",
        add_dirs=[],
        on_tool_event=None,
        codex_factory=lambda: _FakeCodex(),
    )
    with pytest.raises(NotImplementedError):
        await backend.ask_ephemeral(1, "x", allowed_tools=())


# --- PiRpcProcess subprocess-protocol hardening -----------------------------


class _ScriptedStdout:
    """A stdout stand-in whose ``readline`` replays scripted steps in order."""

    def __init__(self, steps: list[object]) -> None:
        self._steps = list(steps)

    async def readline(self) -> bytes:
        if not self._steps:
            return b""
        step = self._steps.pop(0)
        if step == "overrun":
            raise ValueError("Separator is not found, and chunk exceed the limit")
        assert isinstance(step, bytes)
        return step


class _ChunkStderr:
    """A stderr stand-in whose ``read`` yields scripted chunks then EOF."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def _pi_process() -> Any:
    """Construct a bare ``PiRpcProcess`` (no real subprocess spawned)."""
    return pi_agent_module.PiRpcProcess(
        cli_bin="pi", cwd=None, model=None, persist_session=True
    )


async def test_pi_reader_survives_oversized_stdout_line() -> None:
    """A line over the StreamReader limit must not kill the reader task."""
    proc = _pi_process()
    proc._proc = type("P", (), {"stdout": _ScriptedStdout(
        ["overrun", b'{"type":"response","id":"1","ok":true}\n']
    )})()
    fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    proc._pending["1"] = fut
    await proc._read_stdout()
    assert fut.done() and fut.result()["ok"] is True


async def test_pi_drain_stderr_consumes_large_output_without_hanging() -> None:
    """Draining must complete even when stderr exceeds the pipe buffer size."""
    proc = _pi_process()
    proc._proc = type("P", (), {"stderr": _ChunkStderr([b"x" * 100_000, b"tail"])})()
    await asyncio.wait_for(proc._drain_stderr(), timeout=1.0)


async def test_pi_event_queue_drops_oldest_when_full() -> None:
    """A bounded event queue drops the oldest event instead of raising/leaking."""
    proc = _pi_process()
    proc._events = asyncio.Queue(maxsize=2)
    proc._offer_event({"n": 1})
    proc._offer_event({"n": 2})
    proc._offer_event({"n": 3})
    assert proc._events.qsize() == 2
    assert proc._events.get_nowait()["n"] == 2  # oldest (n=1) was dropped
