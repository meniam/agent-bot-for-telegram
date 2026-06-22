"""SDK response formatters: context usage, MCP status, server info."""

from src.i18n import Translator
from src.ui.sdk_views import (
    format_context_usage,
    format_mcp_status,
    format_server_info,
)


def test_format_context_usage_includes_totals() -> None:
    """Context usage shows totals, model, and sorted non-empty categories."""
    tr = Translator("en")
    usage = {
        "totalTokens": 12345,
        "maxTokens": 200000,
        "percentage": 6.17,
        "model": "claude-opus-4-7",
        "categories": [
            {"name": "system", "tokens": 100},
            {"name": "tools", "tokens": 500},
            {"name": "empty", "tokens": 0},
        ],
    }
    out = format_context_usage(usage, tr)
    assert "12,345" in out
    assert "200,000" in out
    assert "claude-opus-4-7" in out
    # Sorted desc by tokens; empty category dropped.
    tools_pos = out.find("tools")
    system_pos = out.find("system")
    assert tools_pos != -1 and system_pos != -1
    assert tools_pos < system_pos
    assert "empty" not in out


def test_format_context_usage_zero_tokens_safe() -> None:
    """Empty usage data formats without raising."""
    tr = Translator("en")
    out = format_context_usage({}, tr)
    assert isinstance(out, str)


def test_format_mcp_status_empty_servers() -> None:
    """An empty server list uses the translated empty message."""
    tr = Translator("en")
    out = format_mcp_status({"mcpServers": []}, tr)
    # Empty status produces the `mcp_empty` translation, not raw key.
    assert "mcp_empty" not in out


def test_format_mcp_status_groups_by_state() -> None:
    """MCP status groups connected servers before failed ones and shows errors."""
    tr = Translator("en")
    status = {
        "mcpServers": [
            {"name": "alpha", "status": "connected", "tools": ["a", "b"]},
            {"name": "beta", "status": "failed", "error": "boom"},
            {"name": "gamma", "status": "connected", "tools": []},
        ]
    }
    out = format_mcp_status(status, tr)
    assert "alpha" in out and "beta" in out and "gamma" in out
    # Connected group renders before failed group.
    assert out.find("alpha") < out.find("beta")
    # Error string surfaces.
    assert "boom" in out


def test_format_mcp_status_unknown_status_falls_back_to_raw_name() -> None:
    """An unknown status falls back to showing the raw status name."""
    tr = Translator("en")
    status = {"mcpServers": [{"name": "x", "status": "weird-unknown"}]}
    out = format_mcp_status(status, tr)
    assert "weird-unknown" in out
    assert "x" in out


def test_format_server_info_lists_commands() -> None:
    """Server info lists each command with a slash prefix and the output style."""
    tr = Translator("en")
    info = {
        "commands": [{"name": "foo"}, {"name": "bar"}],
        "output_style": "default",
    }
    out = format_server_info(info, tr)
    assert "/foo" in out and "/bar" in out
    assert "default" in out


def test_format_server_info_handles_missing_fields() -> None:
    """Server info with missing fields falls back to defaults."""
    tr = Translator("en")
    out = format_server_info({}, tr)
    assert "default" in out


def test_format_server_info_caps_command_list_at_30() -> None:
    """Server info caps the listed commands at thirty."""
    tr = Translator("en")
    info = {"commands": [{"name": f"c{i}"} for i in range(50)]}
    out = format_server_info(info, tr)
    assert "/c0" in out and "/c29" in out
    assert "/c30" not in out
