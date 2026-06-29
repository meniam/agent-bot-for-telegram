# Scheduled Tasks

ABT has an opt-in scheduled-task subsystem. It lets users schedule one-shot or
recurring LLM prompts, and lets admins schedule script tasks and global tasks.
The subsystem is deliberately small: task definitions are JSON files, run
history is append-only, and the scheduler never replays a backlog after
downtime.

## Components

- `TaskService` owns user-facing CRUD rules shared by `/task` and the agent MCP
  `task` tool: visibility, admin checks, schedule parsing, prompt scanning, and
  task creation/update.
- `TaskStore` persists task definitions and run history under one bot-specific
  `tasks_dir`. Every operation opens and parses files fresh; writes are atomic
  and durable.
- `TaskScheduler` is the per-bot background loop. It finds due tasks, applies
  access and grace-window rules, persists `running` before execution, and settles
  final task state after `TaskRunner` returns.
- `TaskRunner` executes exactly one task. It serializes workdir-mutating tasks,
  runs scripts or ephemeral LLM turns, delivers successful output, and appends
  one `TaskRun` history record.
- `AgentBackend.ask_ephemeral` is the provider-neutral one-shot LLM interface
  used by scheduled LLM tasks. Claude implements it with a throwaway SDK query;
  Codex and PI currently raise `NotImplementedError`.
- `bot.py` wires the subsystem: it builds `TaskStore`, `TaskService`,
  per-chat MCP task server factory, shared `running_task_ids`, shared
  `running_logs`, `TaskRunner`, and `TaskScheduler`.

## Storage Layout

`tasks_dir` is already bot-specific; no extra bot-name suffix is added.

```text
<tasks_dir>/<chat_id>.json
<tasks_dir>/global.json
<tasks_dir>/history/<task_id>/<started_at>.json
<tasks_dir>/history/<task_id>/<started_at>.jsonl
<tasks_dir>/history/<task_id>/events/<occurred_at>.json
<tasks_dir>/_corrupt/<stem>.<timestamp>.json
```

Definition files are shaped as:

```json
{
  "updated_at": "2026-06-29T23:01:26+03:00",
  "tasks": []
}
```

History JSON files store `TaskRun`: status, output/error, delivery outcome,
timings, provider metadata, session id, and copied transcript path. The paired
`.jsonl` file is a copy of the provider transcript when the backend exposes one.
Audit events record non-execution decisions such as stale skips and access
revocation.

## Lifecycle

Tasks normally move through:

```text
create -> scheduled -> running -> completed | error | paused
```

- `scheduled`: enabled and waiting for `next_run_at`.
- `running`: persisted immediately before `TaskRunner` starts. This makes
  in-flight tasks visible and recoverable.
- `completed`: terminal one-shot state. One-shots become completed even when the
  run result is `error`; inspect `last_status` and history for the outcome.
- `error`: recurring task whose last run failed, or a stale `running` task
  recovered after restart.
- `paused`: user/admin paused it, or owner access was revoked.

The in-memory `running_task_ids` set is only a live-process hint for UI. The
persisted `running` state is the source of truth across restarts.

## Scheduler Rules

- The scheduler ticks every `tasks.tick_interval_sec`.
- It lists tasks with `enabled=true` and `next_run_at <= now`.
- It skips ids already present in `running_task_ids`, so one process never
  double-fires the same task.
- It re-checks owner access before every run. Lost access pauses the task with
  `last_error=access_revoked`.
- It applies catch-up grace instead of replaying missed runs:
  - one-shot overdue by more than 120 seconds becomes `completed` without
    running;
  - recurring overdue beyond its computed grace window is fast-forwarded.
- It advances `next_run_at` before execution, then persists `state=running`.
  This prevents slow runs from being picked up again on the next tick.
- On scheduler startup, any persisted `running` task not in the current
  `running_task_ids` set is treated as interrupted by a previous process:
  - one-shot: `state=error`, `enabled=false`, `next_run_at=null`,
    `last_status=error`, `last_error=interrupted_by_restart`;
  - recurring: `state=error`, `last_status=error`,
    `last_error=interrupted_by_restart`, and `enabled=true` only when it already
    has a future `next_run_at`.

## Runner Rules

- LLM tasks always take the per-bot `workdir_lock`; script tasks take it only
  when `exclusive=true`.
- Script tasks run under `tasks.script_timeout_sec`; stdout/stderr are combined,
  byte-capped, decoded, char-truncated, and recorded.
- LLM tasks use `AgentBackend.ask_ephemeral` and do not touch the chat's live
  session or current-session pointer.
- LLM task permissions are non-interactive. If Claude is configured with
  bypass permissions, the background run mirrors that posture; otherwise only
  `tasks.allowed_tools` are allowed.
- LLM task watchdogs:
  - `tasks.llm_idle_timeout_sec` bounds silence between SDK events;
  - `tasks.llm_timeout_sec` bounds total wall-clock execution time;
  - `0` disables the corresponding watchdog.
- Idle or total LLM timeout is converted into a normal `RunOutcome` with
  `status=error`, so history is still written and scheduler state is settled.
- Successful runs with non-empty output are delivered to the owner chat or, for
  global tasks, to allowed chats minus blacklisted chats. Delivery failures are
  recorded in history and do not crash the scheduler.
- `_record` appends history for every outcome. If transcript copying fails, the
  run is still recorded with `transcript_error`.

## Access Model

- `/task` is a normal ACL-protected handler.
- Users can list/manage only their own `scope=user` tasks.
- `admin_chat_ids` can create/manage `scope=global` tasks and script tasks.
- Global task output broadcasts only to enumerable `allowed_chat_ids` minus
  `blacklist_chat_ids`. `allowed_for_all=true` does not provide a chat list.
- The agent MCP `task` tool is per chat and does not expose chat ids; it reuses
  `TaskService` permissions and prompt scanning.

## Testing Map

- `tests/test_task_types.py`: schedule parsing, interval/cron math, grace math.
- `tests/test_task_store.py`: task JSON persistence, due/running listing,
  history, audit events, transcript copies, pruning, corruption quarantine.
- `tests/test_task_scheduler.py`: due dispatch, running state, repeat settling,
  stale grace handling, access revocation, heartbeat, loop-death alerts,
  restart recovery.
- `tests/test_task_runner.py`: script execution, delivery outcomes, LLM provider
  metadata, transcript copying, live log publication, LLM idle/total timeouts.
- `tests/test_task_tool.py`: MCP task tool payloads, permissions, live and
  persisted running visibility, last-run metadata.
- `tests/test_config.py`: task config loading, path resolution, default values,
  LLM timeout configuration.
- `tests/test_agent_backends.py`: provider behavior for ephemeral turns,
  including Claude idle watchdog behavior.

For significant task changes, run:

```bash
ruff check src/ tests/
mypy src/ tests/ --strict
pyright src/ tests/
pytest -q
find src -name '*.py' -not -path '*/__pycache__/*' -print0 | xargs -0 python -m py_compile
```
