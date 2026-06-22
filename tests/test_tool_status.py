"""Tool status helpers: path briefs, display emoji, one-line truncation."""

from pathlib import Path

from src.ui.tool_status import (
    _one_line,
    _tool_brief,
    _tool_display,
    _tool_input_from_payload,
)


def test_tool_brief_replaces_working_dir_with_at() -> None:
    """A path under the working dir is shown with an `@/` prefix."""
    assert (
        _tool_brief(
            "Edit",
            {"file_path": "/repo/src/ui/tool_status.py"},
            Path("/repo"),
        )
        == "@/src/ui/tool_status.py"
    )


def test_tool_brief_keeps_only_tail_for_external_path() -> None:
    """An external path is abbreviated to its trailing components."""
    assert (
        _tool_brief(
            "Edit",
            {
                "file_path": (
                    "/Users/eugene/.claude/projects/"
                    "-Users-eugene-Documents-Obsidian-Brain/memory/MEMORY.md"
                )
            },
            Path("/repo"),
        )
        == ".../-Users-eugene-Documents-Obsidian-Brain/memory/MEMORY.md"
    )


def test_tool_brief_treats_relative_path_as_working_dir_path() -> None:
    """A relative path is treated as living under the working dir."""
    assert (
        _tool_brief("Read", {"file_path": "README.md"}, Path("/repo"))
        == "@/README.md"
    )


def test_tool_brief_leaves_non_path_fields_unchanged() -> None:
    """A non-path field is returned unchanged."""
    assert _tool_brief("Bash", {"command": "pytest -q"}, Path("/repo")) == "pytest -q"


def test_tool_display_adds_emoji_case_insensitively() -> None:
    """Tool display prefixes an emoji matched case-insensitively."""
    assert _tool_display("bash") == "⌨️ bash"
    assert _tool_display("Read") == "📖 Read"
    assert _tool_display("Unknown") == "🔧 Unknown"


def test_one_line_collapses_and_truncates() -> None:
    """one_line collapses whitespace and truncates with an ellipsis."""
    assert _one_line("hello\n   world", 20) == "hello world"
    assert _one_line("0123456789abcdef", 8) == "0123456…"


def test_tool_input_from_payload_prefers_nested_tool_input() -> None:
    """Extracting tool input prefers the nested tool_input field."""
    assert _tool_input_from_payload(
        {"tool_input": {"command": "pytest -q"}, "tool_call_id": "call_1"}
    ) == {"command": "pytest -q"}
