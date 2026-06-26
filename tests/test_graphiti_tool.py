"""Unit tests for the per-chat Graphiti memory proxy.

The proxy's whole job is isolation: every call must carry this chat's namespace,
overriding whatever the model passed. These tests drive the handler against a
fake upstream that records the forwarded arguments.
"""

from typing import Any

from src.infra.graphiti_tool import (
    GRAPHITI_SERVER_NAME,
    GraphitiProxy,
    _ToolSpec,
    build_graphiti_server,
    make_graphiti_handler,
)

CHAT = 4242


class _FakeProxy(GraphitiProxy):
    """GraphitiProxy whose ``call`` records args instead of hitting the network."""

    def __init__(self) -> None:
        super().__init__(url="http://x/mcp", host="localhost:8000")
        self.last_name: str | None = None
        self.last_args: dict[str, Any] | None = None

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.last_name = name
        self.last_args = args
        return {"content": [{"type": "text", "text": "ok"}]}


def _spec(name: str, *, gid: bool, gids: bool) -> _ToolSpec:
    return _ToolSpec(
        name=name,
        description=name,
        schema={"type": "object", "properties": {}},
        has_group_id=gid,
        has_group_ids=gids,
    )


async def test_forces_group_id_overriding_model() -> None:
    """A model-supplied group_id is replaced with the chat's namespace."""
    proxy = _FakeProxy()
    handle = make_graphiti_handler(
        CHAT, proxy, _spec("add_memory", gid=True, gids=False)
    )
    await handle({"episode_body": "x", "group_id": "attacker"})
    assert proxy.last_args is not None
    assert proxy.last_args["group_id"] == str(CHAT)


async def test_forces_group_ids_overriding_model() -> None:
    """A model-supplied group_ids list is replaced with [chat]."""
    proxy = _FakeProxy()
    handle = make_graphiti_handler(
        CHAT, proxy, _spec("search_nodes", gid=False, gids=True)
    )
    await handle({"query": "q", "group_ids": ["other", "main"]})
    assert proxy.last_args is not None
    assert proxy.last_args["group_ids"] == [str(CHAT)]


async def test_leaves_ungrouped_tool_untouched() -> None:
    """Tools without a group param (e.g. delete by uuid) are forwarded as-is."""
    proxy = _FakeProxy()
    handle = make_graphiti_handler(
        CHAT, proxy, _spec("delete_entity_edge", gid=False, gids=False)
    )
    await handle({"uuid": "abc"})
    assert proxy.last_args == {"uuid": "abc"}


async def test_handler_wraps_upstream_errors() -> None:
    """An upstream failure becomes an is_error result, not a raised traceback."""

    class _Boom(_FakeProxy):
        async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
            raise RuntimeError("upstream down")

    handle = make_graphiti_handler(
        CHAT, _Boom(), _spec("add_memory", gid=True, gids=False)
    )
    result = await handle({"episode_body": "x"})
    assert result["is_error"] is True
    assert "upstream down" in result["content"][0]["text"]


def test_build_server_uses_known_name() -> None:
    """The proxy server registers under the name the agent already knows."""
    proxy = GraphitiProxy(url="http://x/mcp", host="localhost:8000")
    proxy.specs = [_spec("add_memory", gid=True, gids=False)]
    server = build_graphiti_server(CHAT, proxy)
    assert server is not None
    assert GRAPHITI_SERVER_NAME == "graphiti-memory"
