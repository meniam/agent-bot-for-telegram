"""Executes a single scheduled task and delivers its output.

The runner is intentionally stateless about scheduling: `TaskScheduler` decides
*when* a task runs and owns the task-definition bookkeeping (next run, repeat
count, completed/paused state). The runner only *executes* one task — running a
script or an LLM turn — then delivers the result, writes a history record, and
reports the outcome back.

Concurrency: tasks that mutate ``working_dir`` (every LLM task, plus scripts
flagged ``exclusive``) run under a shared per-bot ``workdir_lock`` so they never
overlap; independent scripts run without it.
"""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import BotConfig
from .agent import AgentBackend
from .agent_types import EphemeralResult
from .task_store import TaskStore
from .task_types import RunStatus, Task, TaskRun

log = logging.getLogger(__name__)

# Read-only tools an LLM task may use when `tasks.allowed_tools` is unset.
DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "WebFetch")


def broadcast_targets(cfg: BotConfig) -> list[int]:
    """Recipients for a global task: explicit allow-list minus blacklist.

    With ``allowed_for_all`` there is no enumerable chat list (Telegram polling
    gives none), so a global task can only reach explicitly allowed chats.
    """
    blacklist = set(cfg.blacklist_chat_ids)
    return sorted(c for c in cfg.allowed_chat_ids if c not in blacklist)


@dataclass(slots=True)
class RunOutcome:
    """Result of executing one task: status, output, timing, and delivery."""

    status: RunStatus
    output: str
    error: str | None
    exit_code: int | None
    started_at: datetime
    finished_at: datetime
    delivered_to: list[int] = field(default_factory=list)
    # LLM runs only: SDK session id + path to its jsonl transcript.
    session_id: str | None = None
    transcript_path: str | None = None


class TaskRunner:
    """Runs one task end-to-end: execute → deliver → record history."""

    def __init__(
        self,
        *,
        deliver: Callable[[int, str], Awaitable[None]],
        cfg: BotConfig,
        store: TaskStore,
        agent: AgentBackend,
        log_for_chat: Callable[[int], logging.Logger],
        workdir_lock: asyncio.Lock,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        """Wire the runner to its delivery callback, config, store, and agent."""
        self._deliver_md = deliver
        self._cfg = cfg
        self._store = store
        self._agent = agent
        self._log_for_chat = log_for_chat
        self._workdir_lock = workdir_lock
        self._now = now_fn or (lambda: datetime.now().astimezone())

    async def run(self, task: Task) -> RunOutcome:
        """Execute a task, deliver its output, and persist a history record.

        Serializes on the workdir lock when the task needs it. Non-empty output
        of a successful run is delivered to the task's targets; the run is then
        recorded to history regardless of outcome.
        """
        if task.needs_lock:
            async with self._workdir_lock:
                outcome = await self._execute(task)
        else:
            outcome = await self._execute(task)

        if outcome.status == "ok" and outcome.output.strip():
            outcome.delivered_to = await self._deliver(task, outcome.output)

        await self._record(task, outcome)
        return outcome

    async def _execute(self, task: Task) -> RunOutcome:
        """Run the task's script or LLM turn, catching failures into an outcome."""
        started = self._now()
        cl = self._log_for_chat(task.owner_chat_id)
        try:
            if task.kind == "script":
                exit_code, output = await self._run_script(task)
                status: RunStatus = "ok" if exit_code == 0 else "error"
                error = None if status == "ok" else f"script exited with {exit_code}"
                return RunOutcome(
                    status=status,
                    output=output,
                    error=error,
                    exit_code=exit_code,
                    started_at=started,
                    finished_at=self._now(),
                )
            result = await self._run_llm(task)
            return RunOutcome(
                status="ok",
                output=result.text,
                error=None,
                exit_code=None,
                started_at=started,
                finished_at=self._now(),
                session_id=result.session_id,
                transcript_path=result.transcript_path,
            )
        except Exception as e:  # isolate one task; never crash the loop
            cl.exception("task %s failed: %s", task.id, e)
            return RunOutcome(
                status="error",
                output="",
                error=str(e),
                exit_code=None,
                started_at=started,
                finished_at=self._now(),
            )

    # ----- script execution ------------------------------------------------

    def _resolve_script(self, task: Task) -> Path:
        """Resolve a task's script to an existing path inside ``scripts_dir``.

        Raises ValueError if no script/dir is configured, the path escapes the
        scripts directory, or the file does not exist.
        """
        if not task.script:
            raise ValueError("script task has no script path")
        if not self._cfg.tasks_scripts_dir:
            raise ValueError("tasks.scripts_dir is not configured")
        scripts_dir = Path(self._cfg.tasks_scripts_dir).resolve()
        candidate = (scripts_dir / task.script).resolve()
        if not candidate.is_relative_to(scripts_dir):
            raise ValueError(f"script path escapes scripts_dir: {task.script!r}")
        if not candidate.is_file():
            raise ValueError(f"script not found: {task.script!r}")
        return candidate

    async def _run_script(self, task: Task) -> tuple[int, str]:
        """Run the task's script with a timeout; return (exit code, output).

        Shell scripts run under bash, others under the current interpreter.
        Output is decoded, truncated to the configured cap, and stripped; a
        timeout kills the process and raises ValueError.
        """
        script = self._resolve_script(task)
        if script.suffix in {".sh", ".bash"}:
            argv = ["/bin/bash", str(script)]
        else:
            argv = [sys.executable, str(script)]

        cwd = self._cfg.working_dir or str(script.parent)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        # Read with a hard byte cap so a runaway script cannot exhaust memory;
        # StreamReader.read() applies backpressure, so this never buffers more
        # than the pipe + cap. Output is char-truncated again below.
        char_limit = self._cfg.tasks_max_output_chars
        byte_cap = max((char_limit or 0) * 4, 1 << 20)
        try:
            stdout, hit_cap = await asyncio.wait_for(
                self._read_capped(proc.stdout, byte_cap),
                timeout=self._cfg.tasks_script_timeout_sec,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ValueError(
                f"script timed out after {self._cfg.tasks_script_timeout_sec}s"
            ) from None
        if hit_cap:
            proc.kill()
        await proc.wait()

        text = stdout.decode("utf-8", errors="replace")
        if char_limit and len(text) > char_limit:
            text = text[:char_limit] + "\n…(truncated)"
        elif hit_cap:
            text = text + "\n…(truncated)"
        return (proc.returncode or 0, text.strip())

    @staticmethod
    async def _read_capped(
        stream: asyncio.StreamReader | None, cap: int
    ) -> tuple[bytes, bool]:
        """Read up to ``cap`` bytes from ``stream``; return (data, hit_cap)."""
        if stream is None:
            return (b"", False)
        chunks: list[bytes] = []
        total = 0
        while total < cap:
            chunk = await stream.read(min(65536, cap - total))
            if not chunk:
                return (b"".join(chunks), False)
            chunks.append(chunk)
            total += len(chunk)
        return (b"".join(chunks), True)

    # ----- LLM execution (filled in Phase 4) -------------------------------

    async def _run_llm(self, task: Task) -> EphemeralResult:
        """Run the task's prompt as an ephemeral agent turn and return its result.

        Uses the configured ``tasks.allowed_tools`` or, when unset, the
        read-only `DEFAULT_ALLOWED_TOOLS`. Raises ValueError on a missing prompt.
        """
        if not task.prompt:
            raise ValueError("LLM task has no prompt")
        allowed = (
            self._cfg.tasks_allowed_tools
            if self._cfg.tasks_allowed_tools is not None
            else DEFAULT_ALLOWED_TOOLS
        )
        return await self._agent.ask_ephemeral(
            task.owner_chat_id, task.prompt, allowed_tools=allowed
        )

    # ----- delivery + history ----------------------------------------------

    async def _deliver(self, task: Task, output: str) -> list[int]:
        """Send ``output`` to the task's targets; return the chats reached.

        Global tasks broadcast to `broadcast_targets`; user tasks go to the
        owner. A failure to one chat is logged and does not stop the rest.
        """
        targets = (
            broadcast_targets(self._cfg)
            if task.scope == "global"
            else [task.owner_chat_id]
        )
        delivered: list[int] = []
        for chat_id in targets:
            try:
                await self._deliver_md(chat_id, output)
                delivered.append(chat_id)
            except Exception:  # one bad chat must not stop the rest
                self._log_for_chat(chat_id).exception(
                    "task %s: delivery to chat %s failed", task.id, chat_id
                )
        if not delivered:
            log.warning("task %s: no delivery targets", task.id)
        return delivered

    async def _record(self, task: Task, outcome: RunOutcome) -> None:
        """Copy the run's transcript, then build and append its `TaskRun` record."""
        # For LLM runs, copy the SDK jsonl transcript next to the run record so
        # the full session (tools, inputs, outputs) is inspectable alongside it;
        # the copied path is what we persist as ``log_path``.
        log_path: str | None = None
        if outcome.transcript_path:
            dst = await self._store.copy_transcript(
                task.id, outcome.started_at, Path(outcome.transcript_path)
            )
            if dst is not None:
                log_path = str(dst)
        run = TaskRun(
            task_id=task.id,
            scope=task.scope,
            kind=task.kind,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
            duration_ms=int(
                (outcome.finished_at - outcome.started_at).total_seconds() * 1000
            ),
            status=outcome.status,
            exit_code=outcome.exit_code,
            output=outcome.output,
            error=outcome.error,
            delivered_to=outcome.delivered_to,
            session_id=outcome.session_id,
            log_path=log_path,
        )
        await self._store.append_history(run)
