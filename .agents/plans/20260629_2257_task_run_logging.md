# Рефакторинг логирования исполнения задач

Plan file: `.agents/plans/20260629_2257_task_run_logging.md`
Created: 2026-06-29. Time in the name is reconstructed from git: commit `c441b77` whose state this plan describes as current (renamed from `PLN-0007.md` on 2026-09-06).

## PRD / Why We Are Doing This

### Problem
Scheduled tasks already have two audit surfaces:

- a shared rotating lifecycle log at `<tasks_dir>/tasks.log`, wired from
  `bot._attach_task_log`;
- per-task run history at `<tasks_dir>/history/<task_id>/<timestamp>.json`, with an
  optional copied Claude SDK transcript `<timestamp>.jsonl`.

This is enough for a happy-path run, but not enough for reliable operations and
postmortems. The current design loses or misclassifies several important events:

1. **Task data is already isolated by per-bot `tasks_dir`, but lifecycle log handlers can
   still fan out.** Each bot has its own task directory, so task definitions, run history,
   and copied transcripts are not shared between bots. The extra wrinkle is only the
   lifecycle logger: `_attach_task_log()` attaches each bot's `tasks.log` handler to
   shared module-level loggers (`src.infra.task_scheduler`, `src.infra.task_runner`,
   `src.infra.claude_agent.ephemeral`). In a multi-bot process with tasks enabled for
   more than one bot, a module logger can hold handlers for several different
   `tasks.log` files, and one task event may be emitted to every attached handler unless
   records are bot-scoped or filtered. This does not depend on tasks running in
   parallel; sequential runs can still cross-write if multiple handlers are attached to
   the same shared logger.
2. **LLM provider failures can be marked as successful task runs.** `ClaudeAgentBackend`
   logs `ResultMessage.is_error`, `api_error_status`, `permission_denials`, and
   `errors`, but `EphemeralResult` drops those fields. `TaskRunner` therefore records
   every returned LLM result as `status="ok"` even when the provider reported an error or
   denied tool use.
3. **Delivery failures are not part of the persisted run outcome.** `_deliver()` logs
   per-chat exceptions and keeps going, but `TaskRun.status` remains `ok`. A global task
   that failed delivery to every target can look like a successful execution with only
   `delivered_to=[]` as a clue.
4. **Scheduler decisions that do not execute a task are not in per-task history.** Stale
   one-shot completion, recurring fast-forward, and access revocation are visible only in
   `tasks.log` and mutable task state. After log rotation, there is no per-task audit
   record explaining why a promised run did not happen.
5. **Deleting a task can leave copied transcripts behind.** `_remove_history()` deletes
   `*.json` records but not sibling `*.jsonl` transcript copies.
6. **Recording failures can erase the audit trail of work that already happened.** If
   transcript copy or history append fails after execution and delivery, the scheduler
   logs `task crashed`, but the per-task history may miss the run and final state may not
   reflect what actually happened.
7. **The task log misses important task subsystems.** The attached handler covers
   scheduler, runner, and Claude ephemeral logs, but not `task_store` or `task_tool`,
   where corrupt records, transcript copy misses, and agent-created task actions are
   logged.
8. **Timing diagnostics are too thin.** The logs do not clearly show scheduled time,
   dispatch time, execution start/finish, delivery duration, history-write duration, or
   any wait behind serialized work.
9. **Script execution diagnostics are thin.** History captures final output/error, but
   logs do not show resolved script path, working directory, timeout kill, output byte
   count, or truncation reason.
10. **Prompt and tool-input logging has no redaction layer.** LLM task logs include prompt
    snippets and compact tool inputs. That is useful, but dangerous for common secret
    keys (`token`, `password`, `api_key`, `Authorization`, etc.).
11. **Telegram `/task show` does not expose run audit data.** The MCP task tool can
    return `last_run` and live `log_path`, but the Telegram handler only prints the task
    definition line.

### Goal
Make scheduled-task execution auditable per bot and per task. A maintainer should be
able to answer:

- Which bot and task produced this log line?
- Was the task actually executed, skipped, revoked, delivered, or partially delivered?
- If an LLM provider reported an error or permission denial, why was the run classified
  that way?
- Which transcript/log file belongs to a run?
- When was the task scheduled, dispatched, started, delivered, and persisted?
- Did deletion remove both history records and copied transcripts?

### Users / Scenarios
- Maintainer tailing `<tasks_dir>/tasks.log` during a live task run.
- User asking why a scheduled reminder did not arrive.
- Admin investigating a global task that reached only some chats.
- Developer debugging Claude scheduled-task permission denials or provider API errors.
- Developer running multiple Telegram bots in one process and expecting log isolation.

### Requirements
- REQ-1: Preserve the existing per-bot `tasks_dir` isolation. Task definitions, history,
  transcripts, and `tasks.log` already live under each bot's own directory; the
  lifecycle `tasks.log` handler must only accept records for that bot, even though task
  code uses shared Python module loggers in the same process.
- REQ-2: Include stable correlation fields in every task lifecycle log line:
  `bot_name`, `task_id`, `owner_chat_id`, `scope`, `kind`, and a per-run id or timestamp.
- REQ-3: Extend LLM ephemeral results so provider terminal metadata reaches the runner:
  `is_error`, `subtype`, `stop_reason`, `api_error_status`, `permission_denials`,
  provider `errors`, and `session_id`.
- REQ-4: Classify LLM task runs as `error` when the provider reports a terminal error,
  API error, or permission denial that prevents the intended work from completing.
- REQ-5: Persist delivery outcome details, including partial delivery failures, in
  history. A run with successful execution but failed delivery must not be silently `ok`
  without an explicit delivery status.
- REQ-6: Persist non-execution scheduler decisions in per-task audit history: stale
  one-shot missed/completed, recurring fast-forward/skipped, access revoked/paused, and
  duplicate/dedup suppression if that state becomes user-visible.
- REQ-7: Make history writes resilient. If transcript copy fails, still append the
  `TaskRun` with an explicit transcript-copy error. If history append fails, log a
  high-severity structured event with enough data to reconstruct the run.
- REQ-8: Delete all per-task history artifacts on task removal, including copied
  transcripts and future sidecar files.
- REQ-9: Attach the task file handler to every task subsystem that emits relevant events,
  or route all task subsystem logs through a bot-scoped task logger.
- REQ-10: Add timing fields for scheduling and execution: `scheduled_for`,
  `dispatched_at`, `started_at`, `finished_at`, `serialized_wait_ms`, `execute_ms`,
  `delivery_ms`, and `record_ms`.
- REQ-11: Improve script diagnostics without leaking excessive output: log resolved path,
  cwd, argv shape, timeout, byte/char counts, truncation flag, and exit code.
- REQ-12: Redact common secret-looking fields from task prompt/tool-input/error logs.
- REQ-13: Expose recent run audit in Telegram `/task show`, including last status,
  error, delivery summary, and `log_path` when present.
- REQ-14: Preserve existing JSON compatibility where practical. Existing `TaskRun`
  records must continue to parse.
- REQ-15: Add focused tests for all changed classification, cleanup, and log-isolation
  behavior.

### Acceptance Criteria
- AC-1: In a test with two bots and two distinct `tasks_dir` values, task definitions and
  history stay in their own directories, a run for bot A appears only in bot A's
  `tasks.log`, and a run for bot B appears only in bot B's `tasks.log`, proving shared
  module logger handlers do not cross-write.
- AC-2: A Claude ephemeral `ResultMessage(is_error=True)` produces a `TaskRun` with
  `status="error"` and persisted provider error metadata.
- AC-3: A Claude ephemeral result with permission denials records the denials and does
  not look like a clean `ok` run.
- AC-4: Delivery failure to one target records partial delivery; delivery failure to all
  targets is visible in persisted history and in task state.
- AC-5: Stale one-shot completion, recurring fast-forward, and access revocation each
  leave a per-task audit record.
- AC-6: Removing a task deletes `*.json`, `*.jsonl`, and any sidecar audit files under
  `history/<task_id>/`.
- AC-7: If transcript copy fails because the provider path is missing, history still
  contains the run and a transcript-copy warning/error field.
- AC-8: `/task show <id>` shows the latest run summary and live or copied log path when
  available.
- AC-9: Logs for tool inputs and prompts redact common secret fields.
- AC-10: `ruff check src/ tests/`, `mypy src/ tests/ --strict`, `pyright src/ tests/`,
  and `pytest -q` are green.

### Out of Scope
- Changing scheduling semantics, grace windows, repeat behavior, or ACL decisions.
- Replaying missed recurring runs as a backlog.
- Changing Telegram delivery formatting beyond `/task show` audit visibility.
- Adding an external observability system, metrics daemon, database, or new dependency.
- Making Codex/PI support ephemeral LLM tasks. This plan should improve the logged error
  when those backends raise `NotImplementedError`, not implement new backend primitives.

### Constraints and Dependencies
- `AGENTS.md`: keep access control fail-closed; keep `bot.py` as wiring and supervision
  only; user-facing strings must go through `Translator`.
- `TaskStore` is JSON-per-chat with atomic durable writes. Preserve parse compatibility
  for existing task and history files.
- `TaskRun.status` is currently `Literal["ok", "error"]`. Adding new run statuses has a
  compatibility cost. Prefer a separate event/audit model for scheduler non-execution
  decisions unless the status enum is intentionally expanded.
- `ClaudeAgentBackend.ask_ephemeral` is provider-specific; Codex and PI currently raise
  `NotImplementedError`.
- The bot is multi-bot by design. Directory-level isolation already exists by
  configuration for both normal logs and task storage; the task logging implementation
  must not undermine it with unfiltered handlers on shared module loggers.
- Task runs are treated as sequentially launched for this plan. The log-isolation work
  must be correct without relying on parallel execution or race conditions.

## Code Context
- `bot._attach_task_log()` attaches one `RotatingFileHandler` per task folder to global
  module loggers and does not install a bot filter:
  [bot.py](../../src/bot.py#L136).
- `TaskScheduler.tick()` decides due, revoke, stale complete, stale skip, and dispatch:
  [task_scheduler.py](../../src/infra/task_scheduler.py#L187).
- `TaskScheduler._run_tracked()` advances schedule before running, calls the runner, then
  finalizes task state:
  [task_scheduler.py](../../src/infra/task_scheduler.py#L264).
- `TaskRunner.run()` executes, optionally delivers only on `ok`, records history, then
  logs completion:
  [task_runner.py](../../src/infra/task_runner.py#L90).
- `TaskRunner._deliver()` logs delivery exceptions but does not persist failure details:
  [task_runner.py](../../src/infra/task_runner.py#L297).
- `TaskRunner._record()` copies the transcript before appending `TaskRun`; copy failure
  can prevent the history append:
  [task_runner.py](../../src/infra/task_runner.py#L321).
- `TaskRun` currently stores status, output, error, delivered chats, session id, and
  copied log path:
  [task_types.py](../../src/infra/task_types.py#L100).
- `TaskStore._remove_history()` only deletes `*.json`, leaving copied `*.jsonl` files:
  [task_store.py](../../src/infra/task_store.py#L344).
- `ClaudeAgentBackend.ask_ephemeral()` logs provider terminal metadata but returns only
  text/session/transcript:
  [claude_agent.py](../../src/infra/claude_agent.py#L591).
- `TaskService.last_run()` and `TaskService.live_log_path()` already provide the service
  layer needed to surface audit data:
  [task_service.py](../../src/services/task_service.py#L161).
- The MCP task tool already exposes `last_run` and live log path:
  [task_tool.py](../../src/infra/task_tool.py#L113).
- The Telegram `/task show` handler currently prints only `_fmt_line(task)`:
  [handlers/tasks.py](../../src/handlers/tasks.py#L245).

## Architecture Guidance

### Logging topology
Keep the configured per-bot `tasks_dir` as the storage boundary for definitions, history,
transcripts, and `tasks.log`. The refactor is not about moving task files; it is about
ensuring lifecycle log records do not fan out through shared module loggers. Introduce a
bot-scoped task logging component, for example:

- `TaskLogManager` in `src/infra/task_logging.py`, created by `bot.py` wiring;
- a `logging.Filter` or `LoggerAdapter` that stamps `bot_name` and optionally drops
  records not belonging to that bot;
- a close method invoked from `run_bot()` shutdown so task file handlers are flushed and
  closed.

Two viable approaches:

1. **Preferred: explicit injected task logger.** Pass a `TaskLogger` or
   `logging.LoggerAdapter` into `TaskScheduler`, `TaskRunner`, and task-related services
   that need lifecycle logging. This makes bot scoping explicit and testable.
2. **Fallback: contextual module logger filter.** Keep module loggers but add a filter
   that requires a `bot_name` extra field. This is less invasive, but every log call must
   reliably provide that field.

Prefer option 1 unless the diff becomes too broad. Avoid adding feature logic to
`bot.py`; it should only construct and close the logging component.

### Persistent audit model
Keep `TaskRun` as the final execution record for actual runs. Add a separate lightweight
audit/event model for decisions that are not executions, for example:

```text
TaskAuditEvent:
  task_id
  scope
  kind
  event: dispatched | skipped_stale | completed_stale | access_revoked |
         delivery_failed | history_write_failed
  occurred_at
  scheduled_for
  details
```

This avoids overloading `RunStatus` with non-run concepts while still giving each task a
durable event trail. Store events next to history records under the same task directory,
for example `events/<timestamp>.json` or `<timestamp>.event.json`. If a new file layout
is added, update prune/remove logic to handle it.

### Run outcome model
Extend `RunOutcome` and `TaskRun` with explicit provider and delivery details:

- provider terminal metadata for LLM runs;
- delivery status: `not_attempted`, `all_delivered`, `partial`, `failed`;
- delivery errors by chat id, redacted and bounded;
- transcript copy status/error;
- timing fields.

Keep fields optional so old history records parse. For `TaskRun`, use Pydantic defaults
and `extra="ignore"` if needed.

### Error classification
Use a single classification function in the runner or agent adapter, not ad hoc checks:

- script exit code 0 -> execution ok;
- script non-zero/timeout/path validation -> execution error;
- Claude `ResultMessage.is_error` -> execution error;
- Claude `api_error_status` present -> execution error;
- permission denial count > 0 -> error unless a future explicit policy says denials can
  be non-fatal;
- delivery failure affects delivery status and may update task state/last_error even if
  execution succeeded.

Document the final policy in code comments near the classifier.

### Redaction
Centralize redaction in a small helper, not inline string replacements. It should:

- redact dict keys matching `token`, `api_key`, `apikey`, `password`, `secret`,
  `authorization`, `cookie`, `set-cookie`, `credential`, `private_key`;
- recurse through lists/dicts with a depth and size cap;
- leave non-secret values intact but still truncate long strings;
- be used by `_short_json`, `_short_text`, prompt snippets, delivery errors, and script
  diagnostic logs.

### Telegram UX
Update `/task show <id>` to include a compact latest-run block. All text must use i18n
keys in `src/i18n/*.json`.

Example information shape:

```text
<existing task line>

Last run: error, 2026-06-29 20:15, 5321 ms
Delivery: 1/2 chats
Error: permission_denied
Log: <copied or live path>
```

Keep paths visible because this is an operator-facing bot and the existing MCP tool
already exposes them.

## Affected Contracts
- **Task history JSON:** adds optional fields; old records still parse.
- **Task audit files:** likely new file type under `tasks_dir/history/<task_id>/`.
- **Telegram text:** `/task show` output changes and requires i18n updates.
- **Tests:** task runner/scheduler/store/tool/handler tests expand.
- **Config:** no new required config. Optional future knobs for log redaction or max
  retained audit events are out of scope unless implementation reveals a strong need.

## Phases and Tasks

### Phase 1: Lock down the desired audit contracts
- [ ] [REQ-3, REQ-4, REQ-5, REQ-6] Decide exact data model changes:
  optional `TaskRun` fields vs. new `TaskAuditEvent` files.
- [ ] [REQ-14] Add model tests proving old minimal `TaskRun` JSON still parses.
- [ ] [REQ-6] Add tests for serializing/listing audit events if a new event model is
  introduced.
- [ ] [REQ-5] Define delivery status enum and how it maps to `Task.last_status` and
  `Task.last_error`.

### Phase 2: Isolate and structure task lifecycle logging
- [ ] [REQ-1, REQ-2, REQ-9, AC-1] Replace `_attach_task_log()` with a bot-scoped task
  logging component that owns its handler lifecycle.
- [ ] [REQ-1] Add a two-bot unit test that proves task definitions/history remain in
  their own `tasks_dir` values and lifecycle task log lines do not cross-write between
  the two `tasks.log` files.
- [ ] [REQ-2] Update scheduler/runner/task-store/task-tool log calls to include stable
  correlation fields.
- [ ] [REQ-9] Ensure `task_store` and `task_tool` events reach the task log when they are
  part of task operations.
- [ ] [REQ-1] Close task log handlers during `run_bot()` shutdown and test idempotent
  attach/close behavior.

### Phase 3: Propagate provider terminal metadata
- [ ] [REQ-3] Extend `EphemeralResult` with Claude terminal metadata while keeping
  provider-neutral defaults.
- [ ] [REQ-3] Update `ClaudeAgentBackend.ask_ephemeral()` to populate terminal metadata
  from `ResultMessage`.
- [ ] [REQ-4] Update `TaskRunner._run_llm()` or a classifier helper so provider errors
  produce an error `RunOutcome`.
- [ ] [AC-2, AC-3] Add tests with fake Claude result messages for `is_error`,
  `api_error_status`, and permission denials.
- [ ] [REQ-4] Ensure Codex/PI `NotImplementedError` is recorded as a clear backend
  unsupported error, not an opaque failure.

### Phase 4: Persist delivery and scheduler non-execution outcomes
- [ ] [REQ-5] Extend `_deliver()` to collect per-target errors and delivery status.
- [ ] [REQ-5] Persist delivery status/errors in `TaskRun`.
- [ ] [REQ-5] Decide whether all-delivery-failed sets `Task.state="error"` or only
  `last_error`; implement the chosen policy consistently.
- [ ] [REQ-6] Record audit events for stale one-shot complete, recurring fast-forward,
  and access revocation.
- [ ] [REQ-6] Add scheduler tests proving those decisions leave durable per-task audit
  records.
- [ ] [AC-4, AC-5] Add runner tests for partial and full delivery failure.

### Phase 5: Make history recording failure-tolerant
- [ ] [REQ-7] Change `_record()` so transcript copy failure does not prevent appending
  the main run record.
- [ ] [REQ-7] Store transcript copy status/error in `TaskRun`.
- [ ] [REQ-7] On history append failure, log a structured critical/error event with
  task id, run timing, status, and bounded output/error snippets.
- [ ] [REQ-7] Add tests for missing transcript path and append failure behavior.

### Phase 6: Cleanup and retention correctness
- [ ] [REQ-8, AC-6] Update `_remove_history()` to remove every artifact under the task's
  history directory, including `*.jsonl` and event sidecars.
- [ ] [REQ-8] Update `_prune_history()` so pruning a run removes its paired transcript
  and any paired sidecars.
- [ ] [REQ-8] Add tests for remove/prune cleanup with `.json`, `.jsonl`, and event files.

### Phase 7: Timing and script diagnostics
- [ ] [REQ-10] Capture `scheduled_for`, `dispatched_at`, serialized wait if any,
  execution duration, delivery duration, and record duration in `RunOutcome`/`TaskRun`
  or audit events.
- [ ] [REQ-10] Update logs to include those fields in machine-searchable key/value
  style.
- [ ] [REQ-11] Add script diagnostics: resolved path, cwd, timeout, output byte count,
  output char count, truncation flag, exit code.
- [ ] [REQ-11] Add tests for truncation/timeout diagnostics where feasible.

### Phase 8: Redaction
- [ ] [REQ-12] Add a central redaction helper for task logs.
- [ ] [REQ-12] Use it for prompt snippets, tool-use inputs, tool errors, delivery
  errors, provider errors, and script diagnostic snippets.
- [ ] [AC-9] Add tests proving common secret keys are redacted and ordinary fields remain
  visible.

### Phase 9: Telegram and MCP audit visibility
- [ ] [REQ-13, AC-8] Extend `/task show` to include latest run summary and live/copied
  log path.
- [ ] [REQ-13] Add i18n keys in all supported languages for the new `/task show` labels.
- [ ] [REQ-13] Update MCP task tool output if new run fields are useful to agents, while
  keeping existing `last_run` keys.
- [ ] [REQ-13] Add focused handler/service tests for latest-run rendering.

### Phase 10: Final verification and docs
- [ ] [AC-10] Run:
  `ruff check src/ tests/`,
  `mypy src/ tests/ --strict`,
  `pyright src/ tests/`,
  `bandit -r src/ -q`,
  and `pytest -q`.
- [ ] [AC-10] Run `find src -name '*.py' -not -path '*/__pycache__/*' -print0 |
  xargs -0 python -m py_compile`.
- [ ] Update `CONFIG.md` or `docs/ARCHITECTURE.md` only if the new audit files or task
  log topology need operator-facing documentation.

## Done When
- [ ] Task lifecycle logs are bot-isolated and closed cleanly on shutdown.
- [ ] LLM provider terminal errors and permission denials are correctly classified in
  persistent run history.
- [ ] Delivery failures and scheduler non-execution decisions are durably auditable per
  task.
- [ ] History cleanup removes JSON records, transcripts, and sidecars.
- [ ] Task logs contain enough correlation/timing data to debug scheduling, serialized
  wait if any, execution, delivery, and recording.
- [ ] Sensitive-looking fields are redacted in task logs.
- [ ] Telegram `/task show` exposes latest-run audit data.
- [ ] Full verification suite is green.

## Risks / Unknowns
- Risk: Expanding `RunStatus` beyond `ok|error` will ripple through UI, tests, and stored
  history. Prefer a separate audit event model for non-run decisions.
- Risk: Bot-scoped logging via adapters can be bypassed if future code calls module
  loggers directly. Mitigate with tests and a small helper API.
- Risk: Over-classifying permission denials as errors may mark runs error even when the
  agent recovered. Initial policy should be conservative and explicit; revisit with real
  task logs if needed.
- Risk: Redaction can hide useful debugging data. Use key-based redaction plus truncation
  rather than blanket removal of all tool input.
- Risk: `/task show` could become noisy. Keep the Telegram summary compact and leave full
  detail in JSON history/transcripts.
- Open question: Should all-delivery-failed runs be `status="error"` or
  `status="ok"` with `delivery_status="failed"`? Recommendation: execution status stays
  about execution, but `Task.last_status`/UI should surface delivery failure prominently.
- Open question: Should skipped/revoked audit events count against
  `tasks_history_limit`? Recommendation: keep one shared per-task retention limit across
  run records and audit events unless operators need separate knobs later.
