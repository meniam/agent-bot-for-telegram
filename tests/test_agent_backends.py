import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.config import BotConfig
from src.infra.agent_factory import create_agent_backend
from src.infra.claude_agent import ClaudeAgentBackend
from src.infra.codex_agent import CodexAgentBackend
from src.infra.pi_agent import PiAgentBackend


def _cfg(**overrides: object) -> BotConfig:
    payload: dict[str, object] = {
        "name": "test",
        "telegram_bot_token": "1:abc",
    }
    payload.update(overrides)
    return BotConfig.model_validate(payload)


def test_factory_creates_claude_backend_by_default() -> None:
    backend = create_agent_backend(
        _cfg(),
        on_permission=None,
        system_prompt="x",
        add_dirs=[],
        on_tool_event=None,
    )
    assert isinstance(backend, ClaudeAgentBackend)


def test_factory_creates_codex_backend() -> None:
    backend = create_agent_backend(
        _cfg(agent_provider="codex"),
        on_permission=None,
        system_prompt="x",
        add_dirs=[],
        on_tool_event=None,
        codex_factory=lambda: _FakeCodex(),
    )
    assert isinstance(backend, CodexAgentBackend)


def test_factory_creates_pi_backend() -> None:
    backend = create_agent_backend(
        _cfg(agent_provider="pi"),
        on_permission=None,
        system_prompt="x",
        add_dirs=[],
        on_tool_event=None,
        pi_transport_factory=lambda _model: _FakePiTransport(),
    )
    assert isinstance(backend, PiAgentBackend)


class _FakeResult:
    final_response = "done"

    def __init__(self) -> None:
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


class _FakeThread:
    id = "thread-1"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.model: str | None = None
        self.closed = False

    async def run(self, prompt: str) -> _FakeResult:
        self.prompts.append(prompt)
        return _FakeResult()

    async def set_model(self, model: str | None) -> None:
        self.model = model

    async def close(self) -> None:
        self.closed = True


class _FakeCodex:
    def __init__(self) -> None:
        self.thread = _FakeThread()
        self.thread_start_kwargs: dict[str, object] = {}

    async def __aenter__(self) -> "_FakeCodex":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def thread_start(self, **_kwargs: object) -> _FakeThread:
        self.thread_start_kwargs = dict(_kwargs)
        return self.thread


class _FakePiTransport:
    def __init__(self) -> None:
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
        return await self.events.get()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_codex_backend_thread_lifecycle_and_stream() -> None:
    events: list[tuple[int, str, str, dict[str, Any]]] = []
    fake = _FakeCodex()
    backend = CodexAgentBackend(
        system_prompt="system",
        on_tool_event=lambda *args: _record(events, *args),
        codex_factory=lambda: fake,
    )

    chunks = [chunk async for chunk in backend.ask_stream(10, "hello")]

    assert chunks == ["done"]
    assert backend.has_session(10) is True
    assert fake.thread.prompts == ["hello"]
    assert [(phase, tool) for _, phase, tool, _ in events] == [
        ("pre", "Bash"),
        ("post", "Bash"),
    ]

    await backend.set_model(10, "gpt-5.4")
    assert backend.current_model(10) == "gpt-5.4"
    assert fake.thread.model == "gpt-5.4"

    await backend.reset(10)
    assert backend.has_session(10) is False
    assert fake.thread.closed is True


@pytest.mark.asyncio
async def test_codex_backend_passes_working_dir_to_thread_start() -> None:
    fake = _FakeCodex()
    backend = CodexAgentBackend(
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
async def test_pi_backend_streams_events_and_tool_status() -> None:
    events: list[tuple[int, str, str, dict[str, Any]]] = []
    fake = _FakePiTransport()
    backend = PiAgentBackend(
        system_prompt="system",
        tools_mode="default",
        on_tool_event=lambda *args: _record(events, *args),
        transport_factory=lambda _model: fake,
    )

    chunks = [chunk async for chunk in backend.ask_stream(10, "hello")]

    assert chunks == ["hel", "lo"]
    assert backend.has_session(10) is True
    prompt_request = fake.requests[0]
    assert prompt_request["type"] == "prompt"
    assert "system" in prompt_request["message"]
    assert [(phase, tool) for _, phase, tool, _ in events] == [
        ("pre", "bash"),
        ("post", "bash"),
    ]


@pytest.mark.asyncio
async def test_pi_backend_context_info_model_and_reset() -> None:
    fake = _FakePiTransport()
    backend = PiAgentBackend(
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
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    fake = _FakePiTransport()
    backend = PiAgentBackend(
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


async def _record(
    events: list[tuple[int, str, str, dict[str, Any]]],
    chat_id: int,
    phase: str,
    tool_name: str,
    payload: dict[str, Any],
) -> None:
    events.append((chat_id, phase, tool_name, payload))
