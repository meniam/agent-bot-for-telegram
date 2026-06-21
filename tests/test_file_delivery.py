from pathlib import Path

from src.ui.file_delivery import _resolve_requested_path, parse_file_delivery


def test_parse_fenced_file_delivery() -> None:
    payload = """```bot_files
{
  "type": "send_files",
  "files": [
    {
      "path": "10-Collect/Articles/example.md",
      "caption": "Article"
    }
  ]
}
```"""

    delivery = parse_file_delivery(payload)

    assert delivery is not None
    assert delivery.files[0].path == "10-Collect/Articles/example.md"
    assert delivery.files[0].caption == "Article"


def test_parse_rejects_normal_text() -> None:
    assert parse_file_delivery("File is at /tmp/x.md") is None


def test_resolve_relative_path_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = root / "dir" / "file.md"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    resolved = _resolve_requested_path("dir/file.md", [root.resolve()])

    assert resolved == target.resolve()


def test_resolve_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")

    resolved = _resolve_requested_path("../outside.md", [root.resolve()])

    assert resolved is None
