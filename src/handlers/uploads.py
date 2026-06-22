"""Photo / document / sticker handlers + the per-chat `_save_upload` helper.

Each file is downloaded into the configured uploads_dir, queued on the
`UploadStore`, then the agent is fired (immediately for a single item,
debounced for an album).
"""

import contextlib
import logging

from aiogram import Dispatcher, F
from aiogram.types import Message

from ..infra.message_db import ROLE_USER
from ..services.upload_store import PendingFile
from ..ui.agent_reply import react_to, reply_with_agent
from ..ui.markdown import send_md
from .context import BotContext


async def _save_upload(
    ctx: BotContext,
    message: Message,
    file_id: str,
    original_name: str,
    kind: str,
    cl: logging.Logger,
    size_hint: int | None,
) -> PendingFile | None:
    """Download a Telegram file into the per-chat uploads dir.

    Returns the resulting PendingFile or None on a handled error
    (caller has already replied to the user).
    """
    assert ctx.uploads is not None  # caller checked  # nosec B101
    if (
        ctx.cfg.upload_max_bytes > 0
        and size_hint is not None
        and size_hint > ctx.cfg.upload_max_bytes
    ):
        await send_md(
            message,
            ctx.tr.t(
                "upload_too_large",
                size_mb=size_hint / 1024 / 1024,
                limit_mb=ctx.cfg.upload_max_bytes / 1024 / 1024,
            ),
        )
        return None
    path = ctx.uploads.build_path(message.chat.id, file_id, original_name)
    try:
        with path.open("wb") as f:
            await ctx.bot.download(file_id, destination=f)
    except Exception as e:
        ctx.glog.exception("[%s] upload download failed", ctx.cfg.name)
        cl.exception("upload download failed: %s", e)
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        await send_md(message, ctx.tr.t("upload_error", error=type(e).__name__))
        return None
    cl.info(
        "upload saved: kind=%s name=%s path=%s size=%s",
        kind,
        original_name,
        path,
        path.stat().st_size,
    )
    return PendingFile(path=path, kind=kind, name=original_name)


async def _fire_for_upload(
    ctx: BotContext, message: Message, cl: logging.Logger
) -> None:
    """Single message → fire immediately. Album → debounce via AlbumDebouncer."""
    caption = (message.caption or "").strip()

    async def _on_fire(m: Message, cap: str) -> None:
        await react_to(ctx, m, cap)
        await reply_with_agent(ctx, m, cap, cl)

    await ctx.album.schedule(message, caption, cl, _on_fire)


async def handle_photo(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    await ctx.gate.cancel_active_aq(message.chat.id)
    if ctx.uploads is None:
        await send_md(message, ctx.tr.t("upload_disabled"))
        return
    if not message.photo:
        return
    photo = message.photo[-1]  # largest available size
    cl.info(
        "photo: file_id=%s size=%sx%s bytes=%s media_group=%s",
        photo.file_id,
        photo.width,
        photo.height,
        photo.file_size,
        message.media_group_id,
        extra={"role": ROLE_USER},
    )
    item = await _save_upload(
        ctx,
        message,
        photo.file_id,
        "photo.jpg",
        "image",
        cl,
        photo.file_size,
    )
    if item is None:
        return
    ctx.uploads.add_pending(message.chat.id, item)
    await _fire_for_upload(ctx, message, cl)


async def handle_document(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    await ctx.gate.cancel_active_aq(message.chat.id)
    if ctx.uploads is None:
        await send_md(message, ctx.tr.t("upload_disabled"))
        return
    doc = message.document
    if doc is None:
        return
    original_name = doc.file_name or "document"
    cl.info(
        "document: file_id=%s name=%s mime=%s size=%s media_group=%s",
        doc.file_id,
        original_name,
        doc.mime_type,
        doc.file_size,
        message.media_group_id,
        extra={"role": ROLE_USER},
    )
    item = await _save_upload(
        ctx,
        message,
        doc.file_id,
        original_name,
        "document",
        cl,
        doc.file_size,
    )
    if item is None:
        return
    ctx.uploads.add_pending(message.chat.id, item)
    await _fire_for_upload(ctx, message, cl)


async def handle_sticker(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    await ctx.gate.cancel_active_aq(message.chat.id)
    if ctx.uploads is None:
        await send_md(message, ctx.tr.t("upload_disabled"))
        return
    sticker = message.sticker
    if sticker is None:
        return
    if sticker.is_animated:
        ext, kind = ".tgs", "binary (animated sticker, Lottie JSON)"
    elif sticker.is_video:
        ext, kind = ".webm", "binary (video sticker)"
    else:
        # Static stickers are plain WebP images — Claude can Read them.
        ext, kind = ".webp", "image"
    name = f"sticker_{sticker.set_name or 'unknown'}{ext}"
    cl.info(
        "sticker: file_id=%s set=%s emoji=%s kind=%s size=%s",
        sticker.file_id,
        sticker.set_name,
        sticker.emoji,
        kind,
        sticker.file_size,
        extra={"role": ROLE_USER},
    )
    item = await _save_upload(
        ctx,
        message,
        sticker.file_id,
        name,
        kind,
        cl,
        sticker.file_size,
    )
    if item is None:
        return
    ctx.uploads.add_pending(message.chat.id, item)
    await _fire_for_upload(ctx, message, cl)


def register(dp: Dispatcher) -> None:
    dp.message.register(handle_photo, F.photo)
    dp.message.register(handle_document, F.document)
    dp.message.register(handle_sticker, F.sticker)
