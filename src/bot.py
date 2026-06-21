"""Entry point: wire dependencies, register handlers, run polling per bot.

Heavy lifting lives in submodules:
- `handlers/context.py`: the `BotContext` aggregate handlers consume.
- `handlers/`: aiogram message and callback handlers, grouped by feature.
- `ui/`: formatting helpers, ACL middleware, PlanRouter, AlbumDebouncer,
  ToolStatusMirror, agent-reply pipeline, reaction picker.

`run_bot(cfg, http)` constructs the dependency graph for one bot, builds a
`BotContext`, registers handlers, and starts long-polling. `_supervise`
restarts a crashed bot with exponential backoff. `main` loads the config file
and gathers every bot under one `asyncio.gather`.
"""

import asyncio
import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import BotConfig
from .config import load as load_config
from .handlers import register_all
from .handlers.context import BotContext
from .i18n import Translator
from .infra.agent_factory import create_agent_backend
from .infra.commands import CommandDef, load_commands
from .infra.interactions import TelegramInteractionGate
from .infra.logs import BotLogs, setup_console
from .infra.streaming import DraftStreamer
from .services.transcribe import GroqTranscriber
from .services.upload_store import UploadStore
from .ui.album import AlbumDebouncer
from .ui.markdown import send_md_to_chat
from .ui.middleware import AclMiddleware
from .ui.plan_router import PlanRouter
from .ui.reactions import ReactionPicker
from .ui.tool_status import ToolStatusMirror

BUILTIN_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "config" / "system_prompt.md"


def _build_bot_command_list(
    tr: Translator, commands: list[CommandDef]
) -> list[BotCommand]:
    builtin = [
        BotCommand(command="start",   description=tr.t("bot_command_start")),
        BotCommand(command="new",     description=tr.t("bot_command_new")),
        BotCommand(command="context", description=tr.t("bot_command_context")),
        BotCommand(command="plan",    description=tr.t("bot_command_plan")),
        BotCommand(command="cancel",  description=tr.t("bot_command_cancel")),
        BotCommand(command="stop",    description=tr.t("bot_command_stop")),
        BotCommand(command="mode",    description=tr.t("bot_command_mode")),
        BotCommand(command="model",   description=tr.t("bot_command_model")),
        BotCommand(command="mcp",     description=tr.t("bot_command_mcp")),
        BotCommand(command="info",    description=tr.t("bot_command_info")),
        BotCommand(command="whoami",  description=tr.t("bot_command_whoami")),
        BotCommand(command="help",    description=tr.t("bot_command_help")),
    ]
    return builtin + [
        BotCommand(command=c.name, description=c.description) for c in commands
    ]


def _make_bot(cfg: BotConfig) -> Bot:
    return Bot(
        token=cfg.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


def _make_logs(cfg: BotConfig) -> tuple[BotLogs, logging.Logger, Path | None]:
    bot_log_dir: Path | None = None
    if cfg.logs_dir:
        bot_log_dir = Path(cfg.logs_dir) / cfg.name
    bot_logs = BotLogs(
        name=cfg.name,
        base_dir=bot_log_dir,
        capacity=cfg.chat_logger_capacity,
    )
    return bot_logs, bot_logs.general, bot_log_dir


def _make_acl(
    cfg: BotConfig, glog: logging.Logger
) -> Callable[[int], bool]:
    """Build the fail-closed `is_allowed` predicate; log the resulting policy."""
    allowed_set: set[int] = set(cfg.allowed_chat_ids)
    blacklist_set: set[int] = set(cfg.blacklist_chat_ids)

    def is_allowed(chat_id: int) -> bool:
        if chat_id in blacklist_set:
            return False
        if cfg.allowed_for_all:
            return True
        return chat_id in allowed_set

    if cfg.allowed_for_all:
        glog.warning(
            "[%s] access: OPEN TO EVERYONE (allowed_for_all=true)", cfg.name
        )
    else:
        glog.info(
            "[%s] access restricted to %d chat_id(s)", cfg.name, len(allowed_set)
        )
    if blacklist_set:
        glog.info("[%s] blacklist: %d chat_id(s)", cfg.name, len(blacklist_set))

    return is_allowed


def _make_transcriber(
    cfg: BotConfig, http: aiohttp.ClientSession, glog: logging.Logger
) -> GroqTranscriber | None:
    if cfg.groq_api_key is None:
        return None
    transcriber = GroqTranscriber(
        http,
        api_key=cfg.groq_api_key.get_secret_value(),
        model=cfg.groq_model,
        timeout_sec=cfg.groq_timeout_sec,
    )
    glog.info(
        "[%s] groq transcription enabled (model=%s)", cfg.name, cfg.groq_model
    )
    return transcriber


def _make_uploads(
    cfg: BotConfig, glog: logging.Logger
) -> UploadStore | None:
    if not cfg.uploads_dir:
        return None
    uploads = UploadStore(Path(cfg.uploads_dir))
    glog.info("[%s] uploads enabled at %s", cfg.name, uploads.base_dir)
    return uploads


def _load_custom_commands(
    cfg: BotConfig, glog: logging.Logger
) -> list[CommandDef]:
    if not cfg.commands_dir:
        return []
    commands = load_commands(Path(cfg.commands_dir))
    glog.info(
        "[%s] loaded %d custom command(s) from %s",
        cfg.name,
        len(commands),
        cfg.commands_dir,
    )
    return commands


def _load_builtin_system_prompt() -> str:
    return BUILTIN_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def _compose_system_prompt(cfg: BotConfig, tr: Translator) -> str:
    bot_prompt = (cfg.system_prompt or tr.t("default_system_prompt")).strip()
    builtin_prompt = _load_builtin_system_prompt()
    if not bot_prompt:
        return builtin_prompt
    return f"{builtin_prompt}\n\nBot-specific instructions:\n\n{bot_prompt}"


async def run_bot(cfg: BotConfig, http: aiohttp.ClientSession) -> None:
    bot = _make_bot(cfg)
    me = await bot.get_me()
    bot_username = me.username or f"bot_{me.id}"

    bot_logs, glog, bot_log_dir = _make_logs(cfg)

    glog.info("[%s] starting as @%s", cfg.name, bot_username)
    glog.info("[%s] lang: %s", cfg.name, cfg.lang)
    if cfg.working_dir:
        glog.info("[%s] working_dir: %s", cfg.name, cfg.working_dir)
    if bot_log_dir:
        glog.info("[%s] logs: %s", cfg.name, bot_log_dir)

    tr = Translator(cfg.lang)
    system_prompt = _compose_system_prompt(cfg, tr)
    reaction_picker = ReactionPicker.from_translator(tr)
    is_allowed = _make_acl(cfg, glog)

    streamer = DraftStreamer(
        cfg.telegram_bot_token.get_secret_value(),
        http,
        interval_sec=cfg.draft_interval_sec,
    )
    gate = TelegramInteractionGate(
        bot,
        translator=tr,
        approval_timeout_sec=cfg.approval_timeout_sec,
        send_md_callback=partial(send_md_to_chat, bot),
        chat_logger=bot_logs.for_chat,
    )

    tool_mirror = ToolStatusMirror(
        bot, tr, bot_logs, glog, cfg.name, working_dir=cfg.working_dir
    )

    add_dirs: list[str] = []
    if cfg.uploads_dir:
        add_dirs.append(cfg.uploads_dir)
    glog.info("[%s] agent_provider: %s", cfg.name, cfg.agent_provider)
    if cfg.agent_model:
        glog.info("[%s] agent_model: %s", cfg.name, cfg.agent_model)
    agent = create_agent_backend(
        cfg,
        on_permission=gate.can_use_tool,
        system_prompt=system_prompt,
        add_dirs=add_dirs,
        on_tool_event=tool_mirror.handle,
    )

    transcriber = _make_transcriber(cfg, http, glog)
    uploads = _make_uploads(cfg, glog)
    plan_router = PlanRouter(agent, gate, tr, glog, cfg.name)
    album = AlbumDebouncer(glog, cfg.name)
    commands = _load_custom_commands(cfg, glog)
    bot_command_list = _build_bot_command_list(tr, commands)

    ctx = BotContext(
        cfg=cfg,
        bot=bot,
        tr=tr,
        glog=glog,
        bot_logs=bot_logs,
        agent=agent,
        gate=gate,
        streamer=streamer,
        tool_mirror=tool_mirror,
        reaction_picker=reaction_picker,
        transcriber=transcriber,
        uploads=uploads,
        plan_router=plan_router,
        album=album,
        bot_command_list=bot_command_list,
        is_allowed=is_allowed,
    )

    dp = Dispatcher()
    middleware = AclMiddleware(ctx)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    register_all(dp, commands)

    await bot.set_my_commands(bot_command_list)
    try:
        await dp.start_polling(bot)
    finally:
        glog.info("[%s] shutting down", cfg.name)
        await agent.close_all()
        await bot.session.close()


async def _supervise(cfg: BotConfig, http: aiohttp.ClientSession) -> None:
    """Run a bot with exponential backoff on crashes (1s -> 60s)."""
    backoff = 1.0
    while True:
        try:
            await run_bot(cfg, http)
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(
                "[%s] crashed, restarting in %.1fs", cfg.name, backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


async def main() -> None:
    setup_console()
    bots = load_config()
    logging.info("loaded %d bot(s): %s", len(bots), ", ".join(bots.keys()))

    http = aiohttp.ClientSession()
    try:
        results = await asyncio.gather(
            *(_supervise(cfg, http) for cfg in bots.values()),
            return_exceptions=True,
        )
        # `_supervise` only returns on CancelledError; anything else here is a
        # bug we want to see, not swallow.
        for name, result in zip(bots.keys(), results, strict=True):
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                logging.error(
                    "[%s] supervisor exited with %s", name, repr(result)
                )
    finally:
        await http.close()


def _cli() -> None:
    """Console-script entry point declared in pyproject.toml."""
    asyncio.run(main())


if __name__ == "__main__":
    _cli()
