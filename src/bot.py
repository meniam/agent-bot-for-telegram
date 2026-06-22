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
from functools import partial
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from .config import BotConfig
from .config import load as load_config
from .handlers import register_all
from .handlers.context import BotContext
from .i18n import Translator
from .infra.access_control import make_acl
from .infra.agent_factory import create_agent_backend
from .infra.commands import CommandDef, load_commands
from .infra.interactions import TelegramInteractionGate
from .infra.logs import BotLogs, setup_console
from .infra.session_store import SessionStore
from .infra.skills_linker import link_skills
from .infra.streaming import DraftStreamer
from .infra.task_runner import TaskRunner
from .infra.task_scheduler import TaskScheduler
from .infra.task_store import TaskStore
from .infra.task_tool import build_task_server
from .services.system_prompt_builder import compose_system_prompt
from .services.task_service import TaskService
from .services.transcribe import GroqTranscriber
from .services.upload_store import UploadStore
from .ui.album import AlbumDebouncer
from .ui.markdown import send_md_to_chat, to_html
from .ui.middleware import AclMiddleware
from .ui.plan_router import PlanRouter
from .ui.reactions import ReactionPicker
from .ui.tool_status import ToolStatusMirror

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "var" / "sessions"


def _build_bot_command_list(
    tr: Translator, commands: list[CommandDef], *, tasks_enabled: bool = False
) -> list[BotCommand]:
    """Build the Telegram command menu: built-ins plus custom commands.

    The ``/tasks`` and ``/task`` entries are included only when
    ``tasks_enabled`` is set.
    """
    builtin = [
        BotCommand(command="start",   description=tr.t("bot_command_start")),
        BotCommand(command="new",     description=tr.t("bot_command_new")),
        BotCommand(command="sess",    description=tr.t("bot_command_sess")),
        *(
            [
                BotCommand(command="tasks", description=tr.t("bot_command_tasks")),
                BotCommand(command="task", description=tr.t("bot_command_task")),
            ]
            if tasks_enabled
            else []
        ),
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
    """Construct the aiogram ``Bot`` with HTML parse mode and previews off."""
    return Bot(
        token=cfg.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


def _messages_dir(cfg: BotConfig) -> Path | None:
    """Per-chat SQLite dir: explicit `messages_dir`, else `<logs_dir>/messages`.

    None when no `logs_dir` (SQLite message logging disabled); `SessionStore`
    then falls back to its own dir under `var/sessions`.
    """
    if cfg.messages_dir:
        return Path(cfg.messages_dir)
    if cfg.logs_dir:
        return Path(cfg.logs_dir) / "messages"
    return None


def _make_logs(cfg: BotConfig) -> tuple[BotLogs, logging.Logger, Path | None]:
    """Build the bot's logging stack; return logs, general logger, and log dir."""
    bot_log_dir: Path | None = None
    if cfg.logs_dir:
        bot_log_dir = Path(cfg.logs_dir) / cfg.name
    messages_dir = _messages_dir(cfg)
    bot_logs = BotLogs(
        name=cfg.name,
        base_dir=bot_log_dir,
        capacity=cfg.chat_logger_capacity,
        messages_dir=messages_dir,
    )
    return bot_logs, bot_logs.general, bot_log_dir


def _make_transcriber(
    cfg: BotConfig, http: aiohttp.ClientSession, glog: logging.Logger
) -> GroqTranscriber | None:
    """Build the Groq voice transcriber, or None when no API key is configured."""
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
    """Build the upload store, or None when no uploads directory is configured."""
    if not cfg.uploads_dir:
        return None
    uploads = UploadStore(Path(cfg.uploads_dir))
    glog.info("[%s] uploads enabled at %s", cfg.name, uploads.base_dir)
    return uploads


def _load_custom_commands(
    cfg: BotConfig, glog: logging.Logger
) -> list[CommandDef]:
    """Load custom command definitions from the configured commands directory."""
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


async def run_bot(cfg: BotConfig, http: aiohttp.ClientSession) -> None:
    """Build one bot's dependency graph, register handlers, and long-poll.

    Constructs the agent backend, sessions, gate, streamer and `BotContext`,
    wires the ACL middleware and handlers, starts the task scheduler when
    enabled, then runs `start_polling` until cancellation, closing the agent
    and bot session on shutdown.
    """
    bot = _make_bot(cfg)
    me = await bot.get_me()
    bot_username = me.username or f"bot_{me.id}"

    bot_logs, glog, bot_log_dir = _make_logs(cfg)

    glog.info("[%s] starting as @%s", cfg.name, bot_username)
    glog.info("[%s] lang: %s", cfg.name, cfg.lang)
    if cfg.working_dir:
        glog.info("[%s] working_dir: %s", cfg.name, cfg.working_dir)
        link_skills(cfg.working_dir, cfg.agent_provider, glog, cfg.name)
    if bot_log_dir:
        glog.info("[%s] logs: %s", cfg.name, bot_log_dir)

    tr = Translator(cfg.lang)
    system_prompt = compose_system_prompt(cfg, tr)
    reaction_picker = ReactionPicker.from_translator(tr)
    is_allowed = make_acl(cfg, glog)

    # Sessions live in the same per-chat .db as the message log; without a
    # logs/messages dir they get a standalone db dir under var/sessions.
    session_base = _messages_dir(cfg) or (SESSIONS_DIR / cfg.name)
    sessions = SessionStore(
        session_base,
        default_title=tr.t("session_default_title"),
    )
    # Tag SQLite message rows with the chat's current session (BotLogs is built
    # before SessionStore, so the resolver is injected here).
    bot_logs.set_session_resolver(sessions.current_sync)
    glog.info("[%s] sessions: %s", cfg.name, session_base)

    streamer = DraftStreamer(
        bot,
        interval_sec=cfg.draft_interval_sec,
        convert=to_html,
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

    # Build the task store + service before the agent so the Claude backend can
    # expose a per-chat `task` tool that reuses the same permission/scan rules.
    tasks: TaskStore | None = None
    task_service: TaskService | None = None
    if cfg.tasks_enabled and cfg.tasks_dir:
        tasks = TaskStore(Path(cfg.tasks_dir), history_limit=cfg.tasks_history_limit)
        task_service = TaskService(tasks, cfg)
        glog.info("[%s] tasks: %s", cfg.name, cfg.tasks_dir)
    elif cfg.tasks_enabled:
        glog.warning(
            "[%s] tasks_enabled but tasks_dir is unset — tasks disabled", cfg.name
        )

    task_server_factory = (
        (lambda chat_id: build_task_server(chat_id, task_service))
        if task_service is not None
        else None
    )

    glog.info("[%s] agent_provider: %s", cfg.name, cfg.agent_provider)
    if cfg.agent_model:
        glog.info("[%s] agent_model: %s", cfg.name, cfg.agent_model)
    agent = create_agent_backend(
        cfg,
        session_store=sessions,
        on_permission=gate.can_use_tool,
        system_prompt=system_prompt,
        add_dirs=add_dirs,
        on_tool_event=tool_mirror.handle,
        task_server_factory=task_server_factory,
    )

    transcriber = _make_transcriber(cfg, http, glog)
    uploads = _make_uploads(cfg, glog)
    plan_router = PlanRouter(agent, gate, tr, glog, cfg.name)
    album = AlbumDebouncer(glog, cfg.name)
    commands = _load_custom_commands(cfg, glog)
    bot_command_list = _build_bot_command_list(
        tr, commands, tasks_enabled=cfg.tasks_enabled
    )

    ctx = BotContext(
        cfg=cfg,
        bot=bot,
        tr=tr,
        glog=glog,
        bot_logs=bot_logs,
        agent=agent,
        sessions=sessions,
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
        tasks=tasks,
        task_service=task_service,
    )

    dp = Dispatcher()
    middleware = AclMiddleware(ctx)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    register_all(dp, commands)

    scheduler: TaskScheduler | None = None
    if tasks is not None:
        workdir_lock = asyncio.Lock()
        runner = TaskRunner(
            deliver=partial(send_md_to_chat, bot),
            cfg=cfg,
            store=tasks,
            agent=agent,
            log_for_chat=bot_logs.for_chat,
            workdir_lock=workdir_lock,
        )
        scheduler = TaskScheduler(
            store=tasks,
            runner=runner,
            cfg=cfg,
            glog=glog,
            is_allowed=is_allowed,
            tick_interval=cfg.tasks_tick_interval_sec,
        )
        scheduler.start()

    await bot.set_my_commands(bot_command_list)
    await bot.set_my_commands(
        bot_command_list, scope=BotCommandScopeAllPrivateChats()
    )
    try:
        await dp.start_polling(bot)
    finally:
        glog.info("[%s] shutting down", cfg.name)
        if scheduler is not None:
            await scheduler.stop()
        await agent.close_all()
        await bot.session.close()
        bot_logs.close()


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
    """Load the config and supervise every configured bot concurrently."""
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
