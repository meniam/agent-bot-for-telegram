"""`/task` — manage scheduled tasks from Telegram.

Text subcommands (no inline keyboards in v1):

    /task add <schedule> | <prompt>        create an LLM task
    /task add <schedule> --script <file>   create a script task (admin only)
    /task list                             list your tasks (+ global if admin)
    /task show <id>                        show one task's detail
    /task pause <id> | resume <id>         toggle a task
    /task run <id>                         trigger now (fires next tick)
    /task rm <id>                          delete a task

Flags on `add`: `--global` (admin), `--script <file>` (admin), `--name <name>`,
`--exclusive` (script mutates working_dir → serialize on the workdir lock).

Access: a normal user only sees/manages their own `scope=user` tasks; admins
(``access.admin_chat_ids``) additionally manage `scope=global` tasks and may
create `kind=script` tasks. Enforcement lives in `_visible` / the add guards.
"""

import logging
import shlex

from aiogram import Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..infra.task_types import Task, TaskScope
from ..services.task_service import (
    TaskError,
    TaskNotFoundError,
    TaskPermissionError,
)
from ..ui.markdown import send_md
from .context import BotContext


def _fmt_line(task: Task) -> str:
    """Format one task as a multi-line list/detail entry."""
    name = task.name or (task.prompt or task.script or task.id)[:32]
    nxt = task.next_run_at.strftime("%Y-%m-%d %H:%M") if task.next_run_at else "—"
    flags = f"{task.kind}/{task.scope}"
    status = task.state
    return f"`{task.id}` · {name}\n  {flags} · {task.schedule.display} · next: {nxt} · {status}"


# States considered "archived": terminal one-shots the user no longer acts on.
_ARCHIVED_STATES: frozenset[str] = frozenset({"completed"})


def _cell(text: str) -> str:
    """Sanitize a value for a Markdown table cell (pipes break the table)."""
    return text.replace("|", "/").replace("\n", " ").strip()


def _render_table(ctx: BotContext, tasks: list[Task]) -> str:
    """Render the given tasks as a Markdown table (caller filters archived)."""
    tr = ctx.tr
    state_label = {
        "scheduled": tr.t("task_state_scheduled"),
        "paused": tr.t("task_state_paused"),
        "error": tr.t("task_state_error"),
    }
    head = (
        f"| {tr.t('task_table_head_name')} | {tr.t('task_table_head_schedule')} "
        f"| {tr.t('task_table_head_next')} | {tr.t('task_table_head_status')} |"
    )
    rows = ["| --- | --- | --- | --- |"]
    for t in tasks:
        name = t.name or (t.prompt or t.script or t.id)[:24]
        nxt = t.next_run_at.strftime("%d.%m %H:%M") if t.next_run_at else "—"
        status = state_label.get(t.state, t.state)
        rows.append(
            f"| {_cell(name)} | {_cell(t.schedule.display)} | {nxt} | {status} |"
        )
    return f"{tr.t('task_table_title')}\n\n{head}\n" + "\n".join(rows)


async def tasks_cmd(
    message: Message,
    ctx: BotContext,
    chat_id: int,
    **_: object,
) -> None:
    """`/tasks` — show active (non-archived) tasks as a table."""
    if ctx.task_service is None:
        await send_md(message, ctx.tr.t("task_disabled"))
        return
    tasks = [
        t
        for t in await ctx.task_service.list(chat_id)
        if t.state not in _ARCHIVED_STATES
    ]
    if not tasks:
        await send_md(message, ctx.tr.t("task_list_empty"))
        return
    await send_md(message, _render_table(ctx, tasks))


async def task_cmd(
    message: Message,
    ctx: BotContext,
    command: CommandObject,
    cl: logging.Logger,
    chat_id: int,
    **_: object,
) -> None:
    """Dispatch a `/task <subcommand>` (add/list/show/pause/resume/run/rm)."""
    if ctx.task_service is None:
        await send_md(message, ctx.tr.t("task_disabled"))
        return

    args = (command.args or "").strip()
    sub, _sep, rest = args.partition(" ")
    sub = sub.lower()
    rest = rest.strip()

    if sub in {"", "help"}:
        await send_md(message, ctx.tr.t("task_usage"))
    elif sub == "add":
        await _add(ctx, message, chat_id, rest, cl=cl)
    elif sub == "list":
        await _list(ctx, message, chat_id)
    elif sub in {"show", "pause", "resume", "run", "rm"}:
        await _by_id(ctx, message, chat_id, sub, rest, cl=cl)
    else:
        await send_md(message, ctx.tr.t("task_usage"))


def _parse_add(rest: str) -> dict[str, object]:
    """Parse the `add` argument string into task fields. Raises ValueError."""
    flags: dict[str, object] = {
        "scope": "user",
        "exclusive": False,
        "script": None,
        "name": None,
    }
    tokens = shlex.split(rest)
    positional: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--global":
            flags["scope"] = "global"
        elif tok == "--exclusive":
            flags["exclusive"] = True
        elif tok in {"--script", "--name"}:
            if i + 1 >= len(tokens):
                raise ValueError(f"{tok} needs a value")
            flags[tok[2:]] = tokens[i + 1]
            i += 1
        else:
            positional.append(tok)
        i += 1

    body = " ".join(positional)
    schedule_part, sep, prompt_part = body.partition("|")
    schedule_part = schedule_part.strip()
    prompt_part = prompt_part.strip()
    if not schedule_part:
        raise ValueError("missing schedule")

    flags["schedule_text"] = schedule_part
    flags["prompt"] = prompt_part if sep else ""
    return flags


async def _add(
    ctx: BotContext,
    message: Message,
    chat_id: int,
    rest: str,
    *,
    cl: logging.Logger,
) -> None:
    """Create a task from the `add` argument string and confirm it."""
    assert ctx.task_service is not None
    try:
        parsed = _parse_add(rest)
    except ValueError as e:
        await send_md(message, ctx.tr.t("task_add_error", error=str(e)))
        return

    script = parsed["script"]
    scope: TaskScope = "global" if parsed["scope"] == "global" else "user"
    try:
        task = await ctx.task_service.create(
            chat_id,
            schedule_text=str(parsed["schedule_text"]),
            prompt=str(parsed["prompt"]),
            name=str(parsed["name"] or ""),
            scope=scope,
            script=str(script) if script else None,
            exclusive=bool(parsed["exclusive"]),
        )
    except TaskPermissionError:
        await send_md(message, ctx.tr.t("task_admin_only"))
        return
    except TaskError as e:
        await send_md(message, ctx.tr.t("task_add_error", error=str(e)))
        return

    cl.info("task created %s kind=%s scope=%s", task.id, task.kind, task.scope)
    key = "task_global_created" if task.scope == "global" else "task_created"
    await send_md(
        message, ctx.tr.t(key, id=task.id, schedule=task.schedule.display)
    )


async def _list(ctx: BotContext, message: Message, chat_id: int) -> None:
    """Reply with the caller's visible tasks as a line list."""
    assert ctx.task_service is not None
    tasks = await ctx.task_service.list(chat_id)
    if not tasks:
        await send_md(message, ctx.tr.t("task_list_empty"))
        return
    lines = [ctx.tr.t("task_list_header")]
    lines += [_fmt_line(t) for t in tasks]
    await send_md(message, "\n".join(lines))


async def _by_id(
    ctx: BotContext,
    message: Message,
    chat_id: int,
    action: str,
    task_id: str,
    *,
    cl: logging.Logger,
) -> None:
    """Apply an id-targeted action (show/pause/resume/run/rm) and confirm."""
    assert ctx.task_service is not None
    try:
        task = await ctx.task_service.act(chat_id, action, task_id)
    except TaskNotFoundError:
        await send_md(message, ctx.tr.t("task_not_found", id=task_id.strip() or "?"))
        return

    if action == "show":
        await send_md(message, _fmt_line(task))
    elif action == "rm":
        cl.info("task removed %s", task.id)
        await send_md(message, ctx.tr.t("task_removed", id=task.id))
    elif action == "pause":
        await send_md(message, ctx.tr.t("task_paused", id=task.id))
    elif action == "resume":
        await send_md(message, ctx.tr.t("task_resumed", id=task.id))
    elif action == "run":
        cl.info("task triggered %s", task.id)
        await send_md(message, ctx.tr.t("task_triggered", id=task.id))


def register(dp: Dispatcher) -> None:
    """Register the `/tasks` and `/task` command handlers on ``dp``."""
    dp.message.register(tasks_cmd, Command("tasks"))
    dp.message.register(task_cmd, Command("task"))

