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
from ..infra.task_store import TaskStore
from ..services.task_service import TaskService
from ..services.transcribe import GroqTranscriber
from ..services.upload_store import UploadStore
from ..ui.album import AlbumDebouncer
from ..ui.plan_router import PlanRouter
from ..ui.reactions import ReactionPicker
from ..ui.tool_status import ToolStatusMirror


@dataclass(slots=True, frozen=True)
class BotContext:
    """Per-bot dependency aggregate injected into every handler by middleware.

    Built once in `bot.run_bot`; frozen so handlers cannot mutate shared wiring.
    The optional fields are ``None`` when their feature is disabled in config:
    ``transcriber`` (no ``groq_api_key``), ``uploads`` (no ``uploads_dir``),
    ``tasks`` / ``task_service`` (tasks disabled or no ``tasks_dir``).
    """

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
    # Scheduled-task store; None when the `tasks` feature is disabled.
    tasks: TaskStore | None = None
    # Permission-checked task CRUD over `tasks`; None when disabled.
    task_service: TaskService | None = None
