"""Structured file delivery from agent responses to Telegram documents."""

from __future__ import annotations

import json
import logging
import re
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
    path: str
    caption: str | None = None


@dataclass(slots=True, frozen=True)
class FileDelivery:
    files: tuple[RequestedFile, ...]


def parse_file_delivery(text: str) -> FileDelivery | None:
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
    allowed_roots = [root.resolve() for root in roots if root.exists()]
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
        if not path.exists():
            errors.append(t.t("file_delivery_missing", path=str(path)))
            continue
        if not path.is_file():
            errors.append(t.t("file_delivery_not_file", path=str(path)))
            continue
        size = path.stat().st_size
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
    stripped = text.strip()
    match = _FENCE_RE.fullmatch(stripped)
    if match is not None:
        return match.group("body")
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    return None


def _resolve_requested_path(path_text: str, roots: list[Path]) -> Path | None:
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
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
