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

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python -m src.bot
# or
agent-bot
```

Expected green checks:

```bash
ruff check src/ tests/
mypy src/ tests/ --strict
pyright src/ tests/
bandit -r src/ -q
pip-audit --strict
pytest -q
```

After significant Python changes, also run:

```bash
find src -name '*.py' -not -path '*/__pycache__/*' -print0 | xargs -0 python -m py_compile
pytest -q
```

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
- `sessions_dir: null` uses `var/sessions`; set it to relocate per-chat
  session metadata.

Path fields (`working_dir`, `logs_dir`, `sessions_dir`, `uploads_dir`,
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
3. `/plan` and gate callbacks
4. custom commands
5. greedy `F.text`
6. voice/audio
7. uploads

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
  pass absolute paths to Claude via the attachment prompt.

---

## Slash Commands

Built-ins live in `handlers/basic.py`, `handlers/plan.py`,
`handlers/selectors.py`, and `handlers/sessions.py`: `/start`, `/new`,
`/sess`, `/cancel`, `/context`, `/stop`, `/mode`, `/model`, `/plan`, `/mcp`,
`/info`, `/whoami`, `/help`.

Custom commands are `*.md` files in `commands_dir`. Each file is one command.
Frontmatter supports `name:` and `description:`. The body is sent to Claude as
the prompt, with `$ARGUMENTS` replaced by text after the command. Built-in
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
(`infra/session_store.py`): one JSON file per chat at
`var/sessions/<bot_name>/<chat_id>.json` listing sessions (`id`, `title`,
`auto_titled`, timestamps) plus a `current` pointer. `var/` is gitignored.

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

## i18n and User-Facing Text

All user-facing strings must go through `Translator.t(key, **kwargs)` and live
in `src/i18n/<lang>.json`. Do not hardcode Telegram UI strings in Python.

`lang` controls bot-rendered strings. `system_prompt` controls Claude's reply
language.

---

## Tests

Unit tests cover pure modules: config, commands, i18n, uploads, markdown,
reactions, SDK view formatting, plan router, streaming redaction, log LRU, and
bot factories.

Not deeply unit-tested: aiogram handler wiring, live Claude SDK calls, Telegram
Bot API integration, transcriber, album debouncer, permission gate flows, and
tool-status mirror. Validate those manually when touched.

---

## Contributor Rules

- Preserve fail-closed access control.
- Keep `bot.py` as wiring and supervision only.
- Keep feature logic in focused `handlers/`, `ui/`, `infra/`, or `services/`
  modules.
- Add or update focused tests when behavior changes.
- Do not edit generated/runtime files: `src/config/config.yaml`, `logs/`,
  `uploads/`, `commands/`.
- Do not create stray files in the repo root during normal runs.
- Prefer existing project patterns over new abstractions.
- Use [CONFIG.md](CONFIG.md), [COMMANDS.md](COMMANDS.md), and the code itself
  for details rather than expanding this file into full documentation.
