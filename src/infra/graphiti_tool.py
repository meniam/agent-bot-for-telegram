"""Per-chat Graphiti memory tools, scoped by ``group_id``.

The Graphiti MCP server (knowledge-graph memory, Neo4j backend) partitions all
data by ``group_id``. Pointed at it directly every chat shares one namespace, so
behind a multi-user gateway one chat could read or write another's memory. This
module wraps the upstream HTTP MCP server in a *per-chat* in-process SDK MCP
server that forces ``group_id`` / ``group_ids`` to the owning ``chat_id`` on
every call, overriding whatever the model passes.

For the isolation to hold, the raw upstream server must NOT also be exposed to
the agent (disable it in the project ``.mcp.json`` / ``settings.json``); the
proxy re-registers under the same name (``graphiti-memory``) so existing prompts
keep working.

Lifecycle: ``proxy = await GraphitiProxy.discover(url, host)`` once at startup
reads the upstream tool list; ``build_graphiti_server(chat_id, proxy)`` then
mints the per-chat server, mirroring ``task_tool.build_task_server``.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

# Server name the agent already knows; tools resolve to mcp__graphiti-memory__*.
GRAPHITI_SERVER_NAME = "graphiti-memory"

# Upstream params that select the namespace. We strip them from the schema the
# agent sees and inject the chat's value ourselves.
_GROUP_ID = "group_id"
_GROUP_IDS = "group_ids"


@dataclass
class _ToolSpec:
    """One upstream tool: cleaned schema for the agent + how to scope it."""

    name: str
    description: str
    schema: dict[str, Any]
    has_group_id: bool
    has_group_ids: bool


@dataclass
class GraphitiProxy:
    """Connection details + discovered tool specs for the upstream server."""

    url: str
    host: str
    specs: list[_ToolSpec] = field(default_factory=list)

    def _http_client(self) -> httpx.AsyncClient:
        # Graphiti's FastMCP TrustedHost allowlist only accepts localhost; a
        # Host override lets the request route over the compose network while
        # still passing the check (see docs/DOCKER.md).
        headers = {"Host": self.host} if self.host else {}
        return httpx.AsyncClient(headers=headers)

    @classmethod
    async def discover(cls, url: str, host: str) -> "GraphitiProxy":
        """Connect once and snapshot the upstream tool list."""
        proxy = cls(url=url, host=host)
        async with (
            proxy._http_client() as http,
            streamable_http_client(url, http_client=http) as (r, w, _),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
        for t in listed.tools:
            schema = dict(t.inputSchema or {"type": "object", "properties": {}})
            props = dict(schema.get("properties", {}))
            has_gid = _GROUP_ID in props
            has_gids = _GROUP_IDS in props
            # Hide the namespace params from the agent — we set them.
            for key in (_GROUP_ID, _GROUP_IDS):
                props.pop(key, None)
            schema["properties"] = props
            if "required" in schema:
                schema["required"] = [
                    p for p in schema["required"] if p not in (_GROUP_ID, _GROUP_IDS)
                ]
            proxy.specs.append(
                _ToolSpec(
                    name=t.name,
                    description=t.description or t.name,
                    schema=schema,
                    has_group_id=has_gid,
                    has_group_ids=has_gids,
                )
            )
        return proxy

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Forward one tool call upstream and shape the result for the SDK."""
        async with (
            self._http_client() as http,
            streamable_http_client(self.url, http_client=http) as (r, w, _),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, args)
        content = [_block_to_dict(b) for b in (result.content or [])]
        if not content:
            content = [{"type": "text", "text": ""}]
        out: dict[str, Any] = {"content": content}
        if getattr(result, "isError", False):
            out["is_error"] = True
        return out


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an upstream MCP content block to the SDK's text-content dict."""
    text = getattr(block, "text", None)
    if text is not None:
        return {"type": "text", "text": text}
    dump = getattr(block, "model_dump", None)
    payload = dump() if callable(dump) else {"value": str(block)}
    return {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}


def make_graphiti_handler(
    chat_id: int, proxy: GraphitiProxy, spec: _ToolSpec
) -> "Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]":
    """Build the tool handler that pins this chat's namespace, then forwards."""
    gid = str(chat_id)

    async def handle(args: dict[str, Any]) -> dict[str, Any]:
        """Force ``group_id``/``group_ids`` to this chat and call upstream."""
        scoped = dict(args)
        # Override unconditionally: the model must not pick the namespace.
        if spec.has_group_id:
            scoped[_GROUP_ID] = gid
        if spec.has_group_ids:
            scoped[_GROUP_IDS] = [gid]
        try:
            return await proxy.call(spec.name, scoped)
        except Exception as e:  # never surface a raw traceback to the model
            log.exception(
                "graphiti tool %s failed (chat %s)", spec.name, chat_id
            )
            text = json.dumps(
                {"success": False, "error": f"{type(e).__name__}: {e}"},
                ensure_ascii=False,
            )
            return {"content": [{"type": "text", "text": text}], "is_error": True}

    return handle


def build_graphiti_server(chat_id: int, proxy: GraphitiProxy) -> McpSdkServerConfig:
    """Build the in-process MCP server exposing Graphiti tools for ``chat_id``."""
    tools = [
        tool(spec.name, spec.description, spec.schema)(
            make_graphiti_handler(chat_id, proxy, spec)
        )
        for spec in proxy.specs
    ]
    return create_sdk_mcp_server(
        name=GRAPHITI_SERVER_NAME, version="1.0.0", tools=tools
    )
