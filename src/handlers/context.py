"""Shared wiring object passed to every handler-registration module.

`BotContext` is built once in `bot.run_bot` after every dependency has been
instantiated. Handlers reach into it for the bot, agent, gate, translator,
logs and so on, instead of capturing a closure-tangle of references each.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import BotCommand

from ..config import BotConfig
from ..i18n import Translator
from ..infra.agent import AgentBackend
from ..infra.interactions import TelegramInteractionGate
from ..infra.logs import BotLogs
from ..infra.session_store import SessionStore
from ..infra.streaming import DraftStreamer
from ..services.transcribe import GroqTranscriber
from ..services.upload_store import UploadStore
from ..ui.album import AlbumDebouncer
from ..ui.plan_router import PlanRouter
from ..ui.reactions import ReactionPicker
from ..ui.tool_status import ToolStatusMirror


@dataclass(slots=True, frozen=True)
class BotContext:
    cfg: BotConfig
    bot: Bot
    tr: Translator
    glog: logging.Logger
    bot_logs: BotLogs
    agent: AgentBackend
    sessions: SessionStore
    gate: TelegramInteractionGate
    streamer: DraftStreamer
    tool_mirror: ToolStatusMirror
    reaction_picker: ReactionPicker
    transcriber: GroqTranscriber | None
    uploads: UploadStore | None
    plan_router: PlanRouter
    album: AlbumDebouncer
    bot_command_list: list[BotCommand]
    is_allowed: Callable[[int], bool]
