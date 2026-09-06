# AGENTS.md

Operational guide for LLM agents working in this repository. Keep this file
short and action-oriented. Detailed product docs belong in:

- [README.md](README.md) - user-facing overview and install.
- [INSTALLATION.md](INSTALLATION.md) - production install and directory map.
- [CONFIG.md](CONFIG.md) - full `BotConfig` field reference.
- [COMMANDS.md](COMMANDS.md) - custom slash-command format.
- [CLAUDE.md](CLAUDE.md) - tiny pointer back to this file.

If guidance and code disagree, **the code wins**. Re-read the relevant module
before changing behavior.

---

## Rules and Journals

Eugene's shared rules are included one level up, in the root
[`../../AGENTS.md`](../../AGENTS.md) of the projects folder: journals (`plans.md`,
`decisions.md`, `learning.md`, `changelog.md`) and database (`db.md`). This
repository does not reference their location and keeps no copy
([decision](.agents/decisions/20260906_1848_rules_included_from_cluster_copy_removed.md)); only
project-specific notes live here. Read `db.md` before touching a schema.
Python and Ruff conventions (`python.md`, `ruff.md`) are applied through
`pyproject.toml` and `justfile`.

Four journals, one question each. Every directory has its own `AGENTS.md` with
project-specific notes. The record goes in the **same commit** as the change.

| Question                | Where                            | Rules          |
| ----------------------- | -------------------------------- | -------------- |
| How do we do it?        | `.agents/plans/`                 | `plans.md`     |
| Why is it like this?    | `.agents/decisions/` + `_TOC.md` | `decisions.md` |
| What did we find out?   | `.agents/learnings/` + `_TOC.md` | `learning.md`  |
| What changed, and when? | `CHANGELOG.md`                   | `changelog.md` |

- New plan: `.agents/plans/YYYYMMDD_HHMM_short_task_name.md`, branch
  `<type>/YYYYMMDD_HHMM_short_task_name`. Plans created before 2026-09-06 were
  renamed to this scheme; the time in their names is reconstructed from git
  (see `.agents/decisions/`).
- If asked only to write or discuss a plan, do not change code until a
  separate confirmation. Execute an approved plan in its own branch and keep it
  updated as you go; `- [x]` only after the step's check is green.
- Temporary artifacts go to `var/agents/plans/<planName>`, never into
  `.agents/plans`.
- A decision that outlives its plan gets a file in `.agents/decisions/` before
  the plan reaches `done`; a change visible outside the code gets a line in
  `CHANGELOG.md` under the date of the change. `CHANGELOG.md` starts on
  2026-09-06; earlier history is `git log` and the plans.
- Header keys and section names in journal records are English, as in the
  rules; record text is Russian. Code, comments and docstrings stay English.

---

## Project Shape

This is a multi-bot Telegram -> agent-SDK bridge. One Python process can
run several Telegram bots from `src/config/config.yaml`. Each bot has its own
aiogram dispatcher, per-chat agent backend sessions, Telegram permission gate,
draft streaming, logs, translator, and optional voice/upload/custom-command
services.

Important directories:

- `src/bot.py` - entrypoint, dependency wiring, bot supervision. Keep feature
  logic out of this file.
- `src/config/` - `BotConfig` and config loader.
- `src/infra/` - SDK backend adapters, command loader, logging, streaming,
  permission gate.
- `src/services/` - external service clients: Groq transcription and upload
  storage.
- `src/ui/` - Telegram-facing helpers, middleware, markdown, plan routing,
  tool-status mirror.
- `src/handlers/` - aiogram handlers, one feature per module.
- `tests/` - pure unit tests. Integration-heavy Telegram/SDK flows are mostly
  verified manually.

Module-level docstrings should stay accurate. Comments and docstrings are in
English.

---

## Run and Check

The project runs on uv with a managed Python 3.14; `justfile` is the entry
point and `just` lists the recipes.

```bash
uv sync --locked          # .venv with deps + dev tools from uv.lock
just run                  # uv run python -m src.bot
```

The gate, run before every commit:

```bash
just ci                   # = just lint + just test
```

which expands to

```bash
uv run --locked ruff check --no-fix .
uv run --locked ruff format --check .
uv run --locked mypy --no-incremental
uv run --locked pytest -q
```

`just fix` applies safe ruff fixes and formats (read the diff); `just audit`
runs `pip-audit` over the locked dependency export via `uvx`. Never run
`ruff --unsafe-fixes` as an agent. Tool caches live under `var/cache/`.

Production is not deployed from this repo. The sibling `brain-abt` repository
holds the overlay (compose, `.env`, real config, commands) and `just deploy` /
`just deploy-build`, which pull `origin/main` of this repo on the server and
run the Docker stack. Push here first; a change to `pyproject.toml`, `uv.lock`
or the Dockerfile needs `deploy-build`.

For behavior that unit tests do not cover, run the bot and inspect
`logs/<internal_name>/bot.log` plus the relevant per-chat `<chat_id>.log`.

---

## Configuration Invariants

`src/config/config.yaml` is normally a map of `<internal_name>: BotConfig`.
The loader also accepts the legacy flat single-bot format and wraps it under
`default`.

`internal_name` is a technical key for logs, console prefixes, and env-var
overrides. It is not the Telegram username.

Access control is fail-closed:

1. `blacklist_chat_ids` denies first.
2. `allowed_for_all=true` allows any non-blacklisted chat.
3. otherwise, `allowed_chat_ids` must contain the sender.

Missing, `null`, or empty `allowed_chat_ids` with `allowed_for_all=false`
means nobody is allowed. Any code path that lets a message reach the agent
without this check is a bug.

Important optional features:

- `groq_api_key: null` disables voice/audio transcription.
- `uploads_dir: null` disables photo/document/sticker ingestion.
- `commands_dir: null` disables custom slash commands.
- configured `uploads_dir` is also passed to Claude SDK `add_dirs`.
- `system_prompt: null` falls back to i18n key `default_system_prompt`.
- `agent_dangerously_skip_permissions: true` (Claude only) bypasses the
  permission gate for the live session and for scheduled LLM tasks.
- `agent_event_timeout_sec` (default 120) interrupts a turn when the backend
  emits no event for that long; it is separate from `agent_timeout_sec`.

Path fields (`working_dir`, `logs_dir`, `uploads_dir`,
`commands_dir`) expand `~` and resolve **relative to the config file's
directory** (`src/config/`), not the process CWD. Absolute paths pass
through unchanged.

Env overrides:

- `TELEGRAM_BOT_TOKEN_<INTERNAL_NAME>`
- `GROQ_API_KEY_<INTERNAL_NAME>`
- `GROQ_API_KEY`

Use [CONFIG.md](CONFIG.md) for the full field reference.

---

## Handler and Flow Rules

`register_all` order in `src/handlers/__init__.py` matters:

1. selectors: `/mode`, `/model`
2. basic built-ins
3. `/sess` (sessions)
4. `/task`, `/tasks` (tasks)
5. `/plan` and gate callbacks
6. questionnaire (`AskUserQuestion` poll callbacks)
7. custom commands
8. greedy `F.text`
9. voice/audio
10. uploads

Custom commands must be registered before `F.text`.

Every normal input handler should rely on `AclMiddleware` injection:

- `ctx: BotContext`
- `cl: logging.Logger`
- `chat_id: int`

Gate-managed callback prefixes (`perm:`, `aq:`, `plan:`) bypass ACL
middleware; the gate validates ownership itself.

Input flow essentials:

- Text first checks pending `ExitPlanMode` feedback, then armed `/plan`, then
  normal agent turn.
- Voice/audio cancels active `AskUserQuestion`, transcribes via Groq, echoes
  the transcript, then either fires armed `/plan` or a normal agent turn.
- Uploads cancel active `AskUserQuestion`, save files, debounce albums, then
  pass absolute paths to the agent via the attachment prompt.

---

## Slash Commands

Built-ins live in `handlers/basic.py`, `handlers/plan.py`,
`handlers/selectors.py`, `handlers/sessions.py`, and `handlers/tasks.py`:
`/start`, `/new`, `/sess`, `/task`, `/tasks`, `/cancel`, `/context`, `/stop`,
`/mode`, `/model`, `/plan`, `/mcp`, `/info`, `/whoami`, `/help`.

Custom commands are `*.md` files in `commands_dir`. Each file is one command.
Frontmatter supports `name:` and `description:`. The body is sent to the agent
as the prompt, with `$ARGUMENTS` replaced by text after the command. Built-in
names cannot be overridden. Commands load once at startup.

Full custom command reference: [COMMANDS.md](COMMANDS.md).

---

## Permission Gate and Plan Mode

`TelegramInteractionGate` is the boundary between Claude SDK tool permission
checks and Telegram UX.

Ordinary tools get Allow / Deny / Always allow this session buttons. Prompt
messages are deleted after click or timeout. Session-scoped allow rules die on
`/new` or process restart. Persistent rules belong in
`<working_dir>/.claude/settings.local.json`.

SDK settings from user, project, and local sources are honored by the SDK; tools
already allowed there never reach the gate.

Special tool handling:

- `AskUserQuestion` renders Telegram inline keyboards and returns a text
  summary to Claude. Any new user message auto-skips an active question flow.
- `ExitPlanMode` sends the plan plus Approve/Reject buttons. Text typed while
  approval is pending becomes rejection feedback.
- `PushNotification` forwards the message to Telegram and returns success-like
  feedback to Claude.
- `Monitor` and `TaskOutput` remain on the standard tool path; status is
  mirrored through SDK pre/post hooks.

`/plan <task>` immediately enters SDK `permission_mode="plan"`. Bare `/plan`
arms the next text or transcribed voice message as the plan prompt. `/cancel`
and `/new` disarm it.

---

## Sessions, Streaming, Logs

The active `AgentBackend` keeps one live SDK session/thread per chat,
serializes turns with per-chat locks, mirrors selected mode/model state, and
closes or drops idle sessions when `session_idle_ttl_sec > 0`.

### Multi-session per chat

Each chat owns several **named** sessions. The meta layer is `SessionStore`
(`infra/session_store.py`): it owns the `sessions` table (`id`, `title`,
`auto_titled`, timestamps) plus a `chat_meta` `current` pointer in the **same**
per-chat SQLite file as the message log (`<messages_dir>/<chat_id>.db`; falls
back to `var/sessions/<bot_name>/<chat_id>.db` when no logs/messages dir). Ops
open a short-lived connection per call — no cache. `var/` is gitignored.

The Claude SDK already persists conversation history on disk keyed by
`session_id` (UUID); the bot reuses that: a new session is created with
`options.session_id=<uuid>`, an existing one is reopened with
`options.resume=<uuid>`. So switching sessions and surviving a restart need
only swap which UUID the next `_get_client` uses — no history copying.

- `/new` starts a fresh session; the previous one stays in the list (it is no
  longer destroyed).
- `/sess` lists open sessions (current marked); `/sess <n>` switches to the
  nth listed session (1-based ordinal, ordered by creation).
- "Pick up last session" is **lazy**: Telegram polling gives no chat list, so
  the first message from a chat after a restart resumes that chat's `current`
  session via `resume`.
- After the first message in an unnamed session, a cheap one-shot Haiku call
  (`generate_title`) names it in the background (`auto_titled` then `true`).
- **Codex/PI limitation:** these backends have no resume primitive wired here,
  so `new_session`/`switch_session` reset the live session and update the
  store, but do not replay history; `generate_title` falls back to a truncated
  prompt.

`/stop` interrupts a running turn without taking the per-chat lock.

`DraftStreamer` uses Telegram `sendMessageDraft` while SDK partial messages
stream. Final replies go through MarkdownV2 conversion, chunking, and plain-text
fallback.

`BotLogs` writes a general bot log plus per-chat logs when `logs_dir` is set.
Per-chat logs are the best audit trail for user messages, bot replies,
permission decisions, tool hooks, uploads, plan decisions, and errors.

---

## Scheduled Tasks

Opt-in per bot via the `tasks` config section (default off). When enabled,
`run_bot` builds a `TaskStore` (passed into `BotContext.tasks`), a `TaskRunner`,
and a `TaskScheduler`, starts the scheduler before polling, and stops it in the
`finally`. All logic lives in `src/infra/task_{types,store,runner,scheduler}.py`;
`bot.py` stays wiring-only.

- A `Task` is one-shot or recurring (`schedule.kind` = `once`/`interval`/`cron`)
  and LLM or script (`kind`). Stored as JSON per chat under `tasks_dir`
  (`<chat_id>.json`, `global.json`), with append-only run history in
  `history/<task_id>/`. Writes are atomic + `fsync` (a lost task means a missed
  run); unparsable files are quarantined to `_corrupt/`.
- The scheduler ticks every `tick_interval_sec`, runs due tasks fire-and-forget
  (deduped by id), and advances `next_run_at` **before** running so a slow run
  never double-fires. There is no missed-run backlog; restart catch-up uses a
  grace window (one-shot 120s; recurring period/2, clamped) — past it, one-shots
  complete without running and recurring tasks fast-forward.
- LLM tasks run via `AgentBackend.ask_ephemeral` — a throwaway SDK session that
  never touches the chat's live session or `current` pointer. Permissions are
  non-interactive: only `tasks.allowed_tools` are allowed (default read-only),
  everything else is denied (no Telegram gate; nobody is watching).
- Concurrency: a per-bot `workdir_lock` serializes everything that mutates
  `working_dir` (all LLM tasks + `exclusive` scripts); independent scripts run
  in parallel. Live user turns are **not** under this lock (out of scope).
- Access: `/task` is a normal ACL handler. Users manage only their own
  `scope=user` tasks; `admin_chat_ids` may create/manage `scope=global` and
  `kind=script` tasks. Before each run the scheduler re-checks owner access and
  pauses tasks whose owner lost it (`last_error=access_revoked`). Global task
  output broadcasts to `allowed_chat_ids` minus `blacklist_chat_ids`.

---

## i18n and User-Facing Text

All user-facing strings must go through `Translator.t(key, **kwargs)` and live
in `src/i18n/<lang>.json`. Do not hardcode Telegram UI strings in Python.

`lang` controls bot-rendered strings. `system_prompt` controls Claude's reply
language.

---

## Tests

Unit tests cover pure modules: config, commands, i18n, uploads and file
delivery, markdown, reactions, SDK view formatting, plan router, streaming
redaction, log LRU, bot factories, agent backends, the Graphiti proxy, the
healthcheck, the session store, the SQLite message log + FTS search,
questionnaire and AskUserQuestion rendering, and the task subsystem (schedule
math, store, service, MCP task tool, runner script execution, scheduler
dispatch/grace).

Not deeply unit-tested: aiogram handler wiring, live agent SDK calls, Telegram
Bot API integration, transcriber, album debouncer, and permission gate flows.
Validate those manually when touched.

---

## Contributor Rules

- Preserve fail-closed access control.
- Keep `bot.py` as wiring and supervision only.
- Docstrings (English): **every** symbol is documented — module, class,
  function, method, `__init__`, magic method, and private `_` / nested helpers.
  ruff's `D` (pydocstyle, `google` convention) enforces this for public symbols
  and modules; private/nested ones are not linter-gated but are still required
  (keep them to one honest imperative line). Style: imperative summary ("Build…"
  not "Builds…"), a blank line after a multi-line summary, ends with a period.
  A multi-line docstring documents its contract in Google sections: `Returns:`,
  `Raises:` (only exceptions the function raises itself; propagated ones go in
  prose), `Yields:`. The `DOC*` rules check them against the body.
- Lint suppressions carry the rule code and a reason:
  `# noqa: S603  # cmd is a literal list`. Bare `# noqa` fails `PGH004`.
  `# type: ignore` always names the error code.
  Explain *why* for non-obvious control flow (locks, GC, grace windows,
  Deny-shaped tool results); for trivial symbols a single accurate line is
  enough — never restate the signature as filler. If a docstring and the code
  disagree, fix the docstring (the code wins).
- Keep feature logic in focused `handlers/`, `ui/`, `infra/`, or `services/`
  modules.
- Add or update focused tests when behavior changes.
- Do not edit generated/runtime files: `src/config/config.yaml`, `logs/`,
  `uploads/`, `commands/`.
- Do not create stray files in the repo root during normal runs.
- Prefer existing project patterns over new abstractions.
- Use [CONFIG.md](CONFIG.md), [COMMANDS.md](COMMANDS.md), and the code itself
  for details rather than expanding this file into full documentation.
