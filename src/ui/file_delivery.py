"""Structured file delivery from agent responses to Telegram documents."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram.types import FSInputFile, Message

from .markdown import send_md

if TYPE_CHECKING:
    from ..i18n import Translator

log = logging.getLogger(__name__)

MAX_DELIVERY_FILES = 10
MAX_DELIVERY_BYTES = 50 * 1024 * 1024

_FENCE_RE = re.compile(
    r"```bot_files\s*(?P<body>\{.*?\})\s*```",
    re.DOTALL,
)


@dataclass(slots=True, frozen=True)
class RequestedFile:
    """One file the agent asked to send, with an optional caption."""

    path: str
    caption: str | None = None


@dataclass(slots=True, frozen=True)
class FileDelivery:
    """A validated batch of files requested in a `send_files` payload."""

    files: tuple[RequestedFile, ...]


def parse_file_delivery(text: str) -> FileDelivery | None:
    """Return a delivery when the whole answer is a `send_files` payload."""
    raw = _extract_payload(text)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "send_files":
        return None
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not 1 <= len(raw_files) <= MAX_DELIVERY_FILES:
        return None
    files: list[RequestedFile] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            return None
        path = raw_file.get("path")
        if not isinstance(path, str) or not path.strip():
            return None
        caption = raw_file.get("caption")
        if caption is not None and not isinstance(caption, str):
            return None
        files.append(RequestedFile(path=path.strip(), caption=caption))
    return FileDelivery(files=tuple(files))


async def send_file_delivery(
    message: Message,
    delivery: FileDelivery,
    *,
    roots: list[Path],
    t: Translator,
    cl: logging.Logger,
) -> None:
    """Send each requested file as a Telegram document, reporting failures.

    Paths are confined to `roots`; oversized, missing, non-file, or
    out-of-root entries are skipped with a translated error message.
    """
    allowed_roots = await asyncio.to_thread(_resolve_roots, roots)
    if not allowed_roots:
        await send_md(message, t.t("file_delivery_no_roots"))
        return

    sent = 0
    errors: list[str] = []
    for item in delivery.files:
        path = _resolve_requested_path(item.path, allowed_roots)
        if path is None:
            errors.append(t.t("file_delivery_denied", path=item.path))
            continue
        # One stat() off the event loop covers existence, file-ness, and size.
        exists, is_file, size = await asyncio.to_thread(_probe, path)
        if not exists:
            errors.append(t.t("file_delivery_missing", path=str(path)))
            continue
        if not is_file:
            errors.append(t.t("file_delivery_not_file", path=str(path)))
            continue
        if size > MAX_DELIVERY_BYTES:
            errors.append(
                t.t(
                    "file_delivery_too_large",
                    path=str(path),
                    size_mb=size / 1024 / 1024,
                    limit_mb=MAX_DELIVERY_BYTES / 1024 / 1024,
                )
            )
            continue
        cl.info("sending file: %s (%d bytes)", path, size)
        await message.answer_document(
            FSInputFile(path, filename=path.name),
            caption=item.caption,
        )
        sent += 1

    if errors:
        await send_md(message, "\n".join(errors))
    if sent == 0 and not errors:
        await send_md(message, t.t("file_delivery_empty"))


def _extract_payload(text: str) -> str | None:
    """Pull the JSON body from a fenced block or a bare `{...}` answer."""
    stripped = text.strip()
    match = _FENCE_RE.search(stripped)
    if match is not None:
        return match.group("body")
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    return None


def _resolve_roots(roots: list[Path]) -> list[Path]:
    """Resolve and keep only existing roots (filesystem I/O; run off the loop)."""
    resolved: list[Path] = []
    for root in roots:
        if root.exists():
            resolved.append(root.resolve())
    return resolved


def _probe(path: Path) -> tuple[bool, bool, int]:
    """Single stat() → (exists, is_regular_file, size). Missing/bad → (False, …)."""
    try:
        st = path.stat()
    except (OSError, ValueError):
        return (False, False, 0)
    return (True, stat_module.S_ISREG(st.st_mode), st.st_size)


def _resolve_requested_path(path_text: str, roots: list[Path]) -> Path | None:
    """Resolve a requested path, returning it only if it stays within a root."""
    raw_path = Path(path_text).expanduser()
    candidates = (
        [raw_path]
        if raw_path.is_absolute()
        else [root / raw_path for root in roots]
    )

    for candidate in candidates:
        resolved = candidate.resolve()
        if any(_is_relative_to(resolved, root) for root in roots):
            return resolved
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    """Return whether `path` lies under `root`."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
