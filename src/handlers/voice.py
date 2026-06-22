"""Voice / audio messages → Groq transcript → agent turn.

Disabled (`voice_disabled`) when `cfg.groq_api_key` is unset.
"""

import logging
import tempfile
from functools import partial
from typing import BinaryIO, cast

import aiohttp
from aiogram import Dispatcher, F
from aiogram.types import Message

from ..infra.message_db import ROLE_USER
from ..services.transcribe import TranscriptionError
from ..ui.agent_reply import react_to, reply_with_agent
from ..ui.markdown import audio_filename, format_quote, send_md
from .context import BotContext

# In-memory threshold for SpooledTemporaryFile. Audio smaller than this stays
# in RAM; anything larger spills to disk transparently. The Bot API caps
# downloads at 20 MB without a self-hosted server, so this is mostly a hedge
# against many concurrent long voice notes piling up.
_VOICE_SPOOL_MAX_BYTES = 4 * 1024 * 1024


async def handle_voice(
    message: Message, ctx: BotContext, cl: logging.Logger, **_: object
) -> None:
    """Transcribe a voice/audio message and route the text to plan or agent."""
    await ctx.gate.cancel_active_aq(message.chat.id)
    media = message.voice or message.audio
    if media is None:
        return
    cl.info(
        "voice: file_id=%s duration=%ss mime=%s",
        media.file_id,
        getattr(media, "duration", None),
        getattr(media, "mime_type", None),
        extra={"role": ROLE_USER},
    )
    if ctx.transcriber is None:
        await send_md(message, ctx.tr.t("voice_disabled"))
        return
    duration = getattr(media, "duration", 0) or 0
    if (
        ctx.cfg.voice_max_duration_sec > 0
        and duration > ctx.cfg.voice_max_duration_sec
    ):
        await send_md(
            message,
            ctx.tr.t(
                "voice_too_long", seconds=ctx.cfg.voice_max_duration_sec
            ),
        )
        return

    await ctx.bot.send_chat_action(message.chat.id, "typing")
    try:
        with tempfile.SpooledTemporaryFile(
            max_size=_VOICE_SPOOL_MAX_BYTES
        ) as buf:
            # SpooledTemporaryFile satisfies the BinaryIO protocol at
            # runtime but mypy's aiogram stubs don't accept it directly.
            await ctx.bot.download(
                media.file_id, destination=cast(BinaryIO, buf)
            )
            buf.seek(0)
            transcript = await ctx.transcriber.transcribe(
                buf,
                filename=audio_filename(message),
            )
    except (TimeoutError, TranscriptionError, aiohttp.ClientError) as e:
        ctx.glog.warning(
            "[%s] transcription failed: %s", ctx.cfg.name, e
        )
        cl.warning("transcription failed: %s", e)
        await send_md(message, ctx.tr.t("voice_error", error=str(e)[:200]))
        return
    except Exception as e:
        ctx.glog.exception("[%s] transcription error", ctx.cfg.name)
        cl.exception("transcription error: %s", e)
        await send_md(
            message, ctx.tr.t("voice_error", error=type(e).__name__)
        )
        return

    if not transcript:
        await send_md(message, ctx.tr.t("voice_empty"))
        return

    cl.info("transcript: %s", transcript, extra={"role": ROLE_USER})
    await send_md(
        message,
        f"{ctx.tr.t('voice_recognized')}:\n{format_quote(transcript)}",
    )
    # If `/plan` was just armed without args, route the transcript into
    # plan mode instead of the regular agent flow.
    if ctx.plan_router.is_armed(message.chat.id):
        ctx.plan_router.disarm(message.chat.id)
        cl.info("plan-armed voice transcript taken as plan prompt")
        await ctx.plan_router.fire(
            message,
            transcript,
            cl,
            partial(react_to, ctx),
            partial(reply_with_agent, ctx),
        )
        return
    await react_to(ctx, message, transcript)
    await reply_with_agent(ctx, message, transcript, cl)


def register(dp: Dispatcher) -> None:
    """Register the voice/audio handler on ``dp``."""
    dp.message.register(handle_voice, F.voice | F.audio)
