"""User-defined Telegram slash commands loaded from a directory of `.md` files.

Each `<name>.md` defines a single command:

```
---
name: recall
description: Search handoff / decisions
---
Search the project's memory for relevant entries.
```

The body (everything after the closing `---`) is sent to Claude as the user
prompt when the user types `/<name>` in Telegram. Frontmatter is a small
subset of YAML (key: value lines, no nested structures), so we don't pull in
a YAML dependency.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Telegram bot command name rules:
#   1-32 chars, lowercase letters, digits and underscores only,
#   must start with a letter.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# Reserve names handled by the bot itself.
_BUILTIN_NAMES = frozenset({
    "start", "new", "context", "plan", "cancel",
    "stop", "mode", "model", "mcp", "info", "whoami", "help",
})

# Hard cap on a single command file; anything larger is almost certainly
# misconfigured (the prompt is sent to Claude verbatim).
_MAX_COMMAND_FILE_BYTES = 1 * 1024 * 1024


@dataclass(frozen=True)
class CommandDef:
    """A user-defined slash command parsed from one `.md` file."""

    name: str
    description: str
    body: str
    source: Path


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse a leading `--- ... ---` frontmatter block.

    Returns `(metadata, body)`. If the file has no frontmatter, metadata is
    empty and body is the full text.
    """
    stripped = text.lstrip("﻿")  # tolerate UTF-8 BOM
    if not stripped.startswith("---"):
        return {}, stripped
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, stripped
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, stripped
    meta: dict[str, str] = {}
    for raw in lines[1:end_idx]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n").rstrip()
    return meta, body


def load_commands(commands_dir: Path) -> list[CommandDef]:
    """Discover and parse every `*.md` file under `commands_dir`.

    Files that fail validation are skipped with a warning so a single bad
    file does not break the bot. Names colliding with built-ins or with
    each other are dropped.
    """
    if not commands_dir.is_dir():
        log.warning("commands_dir does not exist or is not a directory: %s", commands_dir)
        return []

    out: list[CommandDef] = []
    seen: set[str] = set()
    for path in sorted(commands_dir.glob("*.md")):
        try:
            size = path.stat().st_size
        except OSError as e:
            log.warning("could not stat command file %s: %s", path, e)
            continue
        if size > _MAX_COMMAND_FILE_BYTES:
            log.warning(
                "skipping %s: command file is %d bytes (max %d)",
                path,
                size,
                _MAX_COMMAND_FILE_BYTES,
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("could not read command file %s: %s", path, e)
            continue
        meta, body = _parse_frontmatter(text)
        name = (meta.get("name") or path.stem).strip().lower()
        if not _NAME_RE.match(name):
            log.warning(
                "skipping %s: invalid command name %r (need 1-32 chars, "
                "lowercase letters/digits/_ , must start with a letter)",
                path,
                name,
            )
            continue
        if name in _BUILTIN_NAMES:
            log.warning(
                "skipping %s: command name %r clashes with a built-in", path, name
            )
            continue
        if name in seen:
            log.warning(
                "skipping %s: duplicate command name %r (already loaded)",
                path,
                name,
            )
            continue
        if not body.strip():
            log.warning("skipping %s: empty command body", path)
            continue
        description = (meta.get("description") or name).strip() or name
        # Telegram caps command descriptions at 256 chars.
        if len(description) > 256:
            description = description[:253] + "…"
        seen.add(name)
        out.append(
            CommandDef(name=name, description=description, body=body, source=path)
        )
    return out
