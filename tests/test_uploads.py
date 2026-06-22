"""Uploads: filename sanitization (no path traversal) + prompt formatting."""

from pathlib import Path

from src.services.upload_store import (
    PendingFile,
    UploadStore,
    _safe_filename,
    format_attachment_prompt,
)


def test_safe_filename_strips_traversal() -> None:
    """Sanitizing a filename strips path separators."""
    # Slashes are replaced with `_`; the dot in `..` is in the allowlist
    # (legitimate for extensions). What matters is the result is a single
    # path component with no separators.
    out = _safe_filename("../etc/passwd")
    assert "/" not in out and "\\" not in out
    assert _safe_filename("a/b\\c") == "a_b_c"
    assert _safe_filename("normal_name.txt") == "normal_name.txt"


def test_safe_filename_empty_default() -> None:
    """An empty name defaults to `file`; all-slash input has no separators."""
    assert _safe_filename("") == "file"
    # All-slash input → all chars become `_`; result still has no separators.
    out = _safe_filename("///")
    assert "/" not in out


def test_build_path_contains_no_traversal(tmp_path: Path) -> None:
    """A built upload path stays inside the chat directory."""
    store = UploadStore(tmp_path)
    p = store.build_path(123, "fileidABCDEFGH", "../evil.txt")
    assert tmp_path in p.parents
    # The sanitized basename must not climb out of the chat dir.
    assert "/.." not in str(p)


def test_pending_queue_drains(tmp_path: Path) -> None:
    """Pending uploads can be queued and drained per chat."""
    store = UploadStore(tmp_path)
    item = PendingFile(path=tmp_path / "x.bin", kind="document", name="x.bin")
    store.add_pending(7, item)
    assert store.has_pending(7) is True
    drained = store.pop_pending(7)
    assert drained == [item]
    assert store.has_pending(7) is False


def test_format_attachment_prompt_includes_paths(tmp_path: Path) -> None:
    """The attachment prompt lists each file and the user text."""
    items = [
        PendingFile(path=tmp_path / "a.jpg", kind="image", name="a.jpg"),
        PendingFile(path=tmp_path / "b.pdf", kind="document", name="b.pdf"),
    ]
    out = format_attachment_prompt(items, "describe these")
    assert "a.jpg" in out and "b.pdf" in out
    assert "describe these" in out
    assert "Read tool" in out


def test_format_attachment_prompt_no_user_text(tmp_path: Path) -> None:
    """With no user text, the prompt omits the user message section."""
    items = [PendingFile(path=tmp_path / "a.jpg", kind="image", name="a.jpg")]
    out = format_attachment_prompt(items, "")
    assert "User message" not in out
