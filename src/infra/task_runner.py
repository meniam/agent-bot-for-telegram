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
from .agent_types import AgentEventStreamTimeout, EphemeralResult
from .task_logging import redact, task_log_context
from .task_store import TaskStore
from .task_types import DeliveryStatus, RunStatus, Task, TaskRun

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
    scheduled_for: datetime | None = None
    dispatched_at: datetime | None = None
    serialized_wait_ms: int | None = None
    execute_ms: int | None = None
    delivery_ms: int | None = None
    record_ms: int | None = None
    delivered_to: list[int] = field(default_factory=list)
    delivery_status: DeliveryStatus = "not_attempted"
    delivery_errors: dict[str, str] = field(default_factory=dict)
    # LLM runs only: SDK session id + path to its jsonl transcript.
    session_id: str | None = None
    transcript_path: str | None = None
    transcript_error: str | None = None
    provider_is_error: bool | None = None
    provider_subtype: str | None = None
    provider_stop_reason: str | None = None
    provider_api_error_status: str | None = None
    provider_permission_denials: list[str] = field(default_factory=list)
    provider_errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DeliveryOutcome:
    """Result of delivering task output to one or more Telegram chats."""

    delivered_to: list[int] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    status: DeliveryStatus = "not_attempted"


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
        running_logs: dict[str, str] | None = None,
    ) -> None:
        """Wire the runner to its delivery callback, config, store, and agent.

        ``running_logs`` (optional) is a shared map of in-flight task id to the
        live provider transcript path; the runner fills it when an LLM run's
        session starts and clears it when the run ends.
        """
        self._deliver_md = deliver
        self._cfg = cfg
        self._store = store
        self._agent = agent
        self._log_for_chat = log_for_chat
        self._workdir_lock = workdir_lock
        self._now = now_fn or (lambda: datetime.now().astimezone())
        self._running_logs = running_logs if running_logs is not None else {}

    async def run(
        self,
        task: Task,
        *,
        scheduled_for: datetime | None = None,
        dispatched_at: datetime | None = None,
    ) -> RunOutcome:
        """Execute a task, deliver its output, and persist a history record.

        Serializes on the workdir lock when the task needs it. Non-empty output
        of a successful run is delivered to the task's targets; the run is then
        recorded to history regardless of outcome.
        """
        with task_log_context(self._cfg.name, task.id):
            log.info(
                "task %s (%s): run start kind=%s needs_lock=%s prompt=%r",
                task.id,
                task.name or "",
                task.kind,
                task.needs_lock,
                str(redact((task.prompt or "")[:120])),
            )
            try:
                wait_started = self._now()
                if task.needs_lock:
                    log.info("task %s: acquiring workdir lock", task.id)
                    async with self._workdir_lock:
                        wait_done = self._now()
                        log.info("task %s: workdir lock acquired", task.id)
                        outcome = await self._execute(task)
                        outcome.serialized_wait_ms = self._duration_ms(
                            wait_started, wait_done
                        )
                else:
                    outcome = await self._execute(task)
                    outcome.serialized_wait_ms = 0

                outcome.scheduled_for = scheduled_for
                outcome.dispatched_at = dispatched_at
                if outcome.status == "ok" and outcome.output.strip():
                    delivery_started = self._now()
                    delivery = await self._deliver(task, outcome.output)
                    outcome.delivery_ms = self._duration_ms(
                        delivery_started, self._now()
                    )
                    outcome.delivered_to = delivery.delivered_to
                    outcome.delivery_errors = delivery.errors
                    outcome.delivery_status = delivery.status
                    if delivery.status in {"partial", "failed"}:
                        outcome.error = outcome.error or f"delivery_{delivery.status}"
                else:
                    outcome.delivery_status = "not_attempted"
                    outcome.delivery_ms = 0

                await self._record(task, outcome)
                log.info(
                    "task %s: run done status=%s duration=%dms session=%s "
                    "delivery=%s delivered=%s err=%s",
                    task.id,
                    outcome.status,
                    self._duration_ms(outcome.started_at, outcome.finished_at),
                    outcome.session_id,
                    outcome.delivery_status,
                    outcome.delivered_to,
                    outcome.error,
                )
                return outcome
            finally:
                # The live log path only applies while the task is running; the
                # finished record carries the copied path instead.
                self._running_logs.pop(task.id, None)

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
                    execute_ms=self._duration_ms(started, self._now()),
                )
            log.info("task %s: starting llm turn", task.id)
            result = await self._run_llm(task)
            log.info(
                "task %s: llm turn returned session=%s chars=%d",
                task.id,
                result.session_id,
                len(result.text),
            )
            status, error = self._classify_llm_result(result)
            finished = self._now()
            return RunOutcome(
                status=status,
                output=result.text,
                error=error,
                exit_code=None,
                started_at=started,
                finished_at=finished,
                execute_ms=self._duration_ms(started, finished),
                session_id=result.session_id,
                transcript_path=result.transcript_path,
                provider_is_error=result.is_error,
                provider_subtype=result.subtype,
                provider_stop_reason=result.stop_reason,
                provider_api_error_status=result.api_error_status,
                provider_permission_denials=result.permission_denials,
                provider_errors=result.errors,
            )
        except Exception as e:  # isolate one task; never crash the loop
            cl.exception("task %s failed: %s", task.id, e)
            log.error("task %s failed: %s", task.id, e, exc_info=True)
            return RunOutcome(
                status="error",
                output="",
                error=str(e),
                exit_code=None,
                started_at=started,
                finished_at=self._now(),
            )

    @staticmethod
    def _duration_ms(started: datetime, finished: datetime) -> int:
        """Return elapsed milliseconds between two timestamps."""
        return int((finished - started).total_seconds() * 1000)

    @staticmethod
    def _classify_llm_result(result: EphemeralResult) -> tuple[RunStatus, str | None]:
        """Map provider terminal metadata to the task execution status."""
        if result.is_error:
            return "error", result.api_error_status or result.subtype or "provider_error"
        if result.api_error_status:
            return "error", result.api_error_status
        if result.permission_denials:
            return "error", "permission_denied"
        if result.errors:
            return "error", "; ".join(result.errors[:3])
        return "ok", None

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
        char_limit = self._cfg.tasks_max_output_chars
        byte_cap = max((char_limit or 0) * 4, 1 << 20)
        log.info(
            "task %s: script start path=%s cwd=%s timeout=%ss byte_cap=%d",
            task.id,
            script,
            cwd,
            self._cfg.tasks_script_timeout_sec,
            byte_cap,
        )
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        # Read with a hard byte cap so a runaway script cannot exhaust memory;
        # StreamReader.read() applies backpressure, so this never buffers more
        # than the pipe + cap. Output is char-truncated again below.
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
            truncated = True
        elif hit_cap:
            text = text + "\n…(truncated)"
            truncated = True
        else:
            truncated = False
        log.info(
            "task %s: script end exit=%s bytes=%d chars=%d hit_cap=%s truncated=%s",
            task.id,
            proc.returncode,
            len(stdout),
            len(text),
            hit_cap,
            truncated,
        )
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
        read-only `DEFAULT_ALLOWED_TOOLS`. Enforces the configured scheduled-LLM
        total and idle timeouts, returning provider-shaped error metadata so the
        caller still records history. Raises ValueError on a missing prompt.
        """
        if not task.prompt:
            raise ValueError("LLM task has no prompt")
        allowed = (
            self._cfg.tasks_allowed_tools
            if self._cfg.tasks_allowed_tools is not None
            else DEFAULT_ALLOWED_TOOLS
        )
        live_transcript_path: str | None = None

        def _on_session_path(path: str) -> None:
            """Publish the live provider transcript path while the run is active."""
            nonlocal live_transcript_path
            live_transcript_path = path
            self._running_logs[task.id] = path

        idle_timeout = (
            self._cfg.tasks_llm_idle_timeout_sec
            if self._cfg.tasks_llm_idle_timeout_sec > 0
            else None
        )
        turn = self._agent.ask_ephemeral(
            task.owner_chat_id,
            task.prompt,
            allowed_tools=allowed,
            on_session_path=_on_session_path,
            idle_timeout_sec=idle_timeout,
        )
        try:
            if self._cfg.tasks_llm_timeout_sec > 0:
                return await asyncio.wait_for(
                    turn,
                    timeout=self._cfg.tasks_llm_timeout_sec,
                )
            return await turn
        except AgentEventStreamTimeout as e:
            reason = f"llm_idle_timeout after {self._cfg.tasks_llm_idle_timeout_sec}s"
            log.warning("task %s: %s: %s", task.id, reason, e)
            return EphemeralResult(
                text="",
                transcript_path=live_transcript_path,
                is_error=True,
                subtype=reason,
                errors=[str(e)],
            )
        except TimeoutError:
            reason = f"llm_timeout after {self._cfg.tasks_llm_timeout_sec}s"
            log.warning("task %s: %s", task.id, reason)
            return EphemeralResult(
                text="",
                transcript_path=live_transcript_path,
                is_error=True,
                subtype=reason,
            )

    # ----- delivery + history ----------------------------------------------

    async def _deliver(self, task: Task, output: str) -> DeliveryOutcome:
        """Send ``output`` to the task's targets; return delivery details.

        Global tasks broadcast to `broadcast_targets`; user tasks go to the
        owner. A failure to one chat is logged and does not stop the rest.
        """
        targets = (
            broadcast_targets(self._cfg)
            if task.scope == "global"
            else [task.owner_chat_id]
        )
        delivered: list[int] = []
        errors: dict[str, str] = {}
        for chat_id in targets:
            try:
                await self._deliver_md(chat_id, output)
                delivered.append(chat_id)
            except Exception as e:  # one bad chat must not stop the rest
                errors[str(chat_id)] = str(redact(str(e)))[:500]
                self._log_for_chat(chat_id).exception(
                    "task %s: delivery to chat %s failed", task.id, chat_id
                )
        if not targets or not delivered:
            log.warning("task %s: no delivery targets", task.id)
        if not errors:
            status: DeliveryStatus = "all_delivered" if delivered else "not_attempted"
        elif delivered:
            status = "partial"
        else:
            status = "failed"
        return DeliveryOutcome(delivered_to=delivered, errors=errors, status=status)

    async def _record(self, task: Task, outcome: RunOutcome) -> None:
        """Copy the run's transcript, then build and append its `TaskRun` record."""
        record_started = self._now()
        # For LLM runs, copy the SDK jsonl transcript next to the run record so
        # the full session (tools, inputs, outputs) is inspectable alongside it;
        # the copied path is what we persist as ``log_path``.
        log_path: str | None = None
        if outcome.transcript_path:
            try:
                dst = await self._store.copy_transcript(
                    task.id, outcome.started_at, Path(outcome.transcript_path)
                )
            except Exception as e:
                outcome.transcript_error = str(e)
                log.warning(
                    "task %s: transcript copy failed: %s", task.id, outcome.transcript_error
                )
            else:
                if dst is not None:
                    log_path = str(dst)
                else:
                    outcome.transcript_error = "transcript_not_found"
        outcome.record_ms = self._duration_ms(record_started, self._now())
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
            delivery_status=outcome.delivery_status,
            delivery_errors=outcome.delivery_errors,
            scheduled_for=outcome.scheduled_for,
            dispatched_at=outcome.dispatched_at,
            serialized_wait_ms=outcome.serialized_wait_ms,
            execute_ms=outcome.execute_ms,
            delivery_ms=outcome.delivery_ms,
            record_ms=outcome.record_ms,
            session_id=outcome.session_id,
            log_path=log_path,
            transcript_error=outcome.transcript_error,
            provider_is_error=outcome.provider_is_error,
            provider_subtype=outcome.provider_subtype,
            provider_stop_reason=outcome.provider_stop_reason,
            provider_api_error_status=outcome.provider_api_error_status,
            provider_permission_denials=outcome.provider_permission_denials,
            provider_errors=outcome.provider_errors,
        )
        try:
            await self._store.append_history(run)
        except Exception:
            log.critical(
                "task %s: history append failed status=%s duration=%d output_chars=%d "
                "error=%r",
                task.id,
                run.status,
                run.duration_ms,
                len(run.output),
                run.error,
                exc_info=True,
            )
            raise
