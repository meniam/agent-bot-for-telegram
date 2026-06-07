# Installing agent-bot

Python Telegram bot that talks to Claude through the Claude Agent SDK + aiogram.

## Requirements

- **macOS / Linux** (Windows is not tested).
- **Python 3.11+** (matches `pyproject.toml`; 3.11 / 3.12 / 3.14 recommended).
- **Node.js 18+** — needed once to install the Claude Code CLI and run `claude login`. The SDK ships a bundled `claude` for runtime use.
- A **Telegram** account and a bot token from [@BotFather](https://t.me/BotFather).
- A **Claude / Max / Team** subscription ([claude login](https://docs.anthropic.com/en/docs/claude-code/setup)) **or** an API key from [console.anthropic.com](https://console.anthropic.com/).
- *(optional)* A **Groq API key** from [console.groq.com/keys](https://console.groq.com/keys) — required only if you want voice/audio messages to be transcribed.

## 1. Install the Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

Authenticate:

```bash
claude login
```

Credentials are stored in `~/.claude/`. The bundled SDK copy picks them up automatically. Alternative — export `ANTHROPIC_API_KEY` before launching the bot.

## 2. Get a Telegram bot token

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. `/newbot` → set a name and `@username`.
3. Copy the issued token (format: `123456789:AA...`).

Keep the Telegram bot token secret — it lives in `src/config/config.json`, which is in `.gitignore`.

## 3. Clone the project and install dependencies

```bash
git clone <repo-url> agent-bot
cd agent-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"   # pyproject.toml is the source of truth; [dev] adds ruff/mypy/pytest/bandit/pip-audit
```

## 4. Create the config

```bash
cp src/config/config.example.json src/config/config.json
```

Open `src/config/config.json` and fill in at least one section:

```json
{
  "brain": {
    "telegram_bot_token": "123456:ABC...",
    "system_prompt": null,
    "lang": "en",
    "allowed_chat_ids": [],
    "working_dir": "/Users/me/Projects/some-project",
    "logs_dir": "/Users/me/Projects/agent-bot/logs",
    "draft_interval_sec": 0.2,
    "approval_timeout_sec": 300
  }
}
```

| Field | What it sets |
|---|---|
| `telegram_bot_token` | Token from @BotFather. **Required.** |
| `system_prompt` | System prompt for Claude. `null` → fallback to translation `default_system_prompt`. |
| `lang` | UI language for bot-facing strings. Bundled (top-20 world languages): `en`, `zh`, `hi`, `es`, `ar`, `fr`, `bn`, `pt`, `ru`, `ur`, `id`, `de`, `ja`, `sw`, `mr`, `te`, `tr`, `ta`, `vi`, `ko`. Default: `ru`. |
| `allowed_for_all` | Boolean. Default `false`. Set to `true` only if you intend the bot to be public — the access gate then accepts every non-blacklisted chat. Logged as a warning at startup. |
| `allowed_chat_ids` | Telegram chat IDs allowed to talk to the bot. `null` / missing / `[]` → **fail-closed**, nobody is allowed (bot replies with the refusal containing the sender's chat_id). `[id, id, ...]` → whitelist. Ignored when `allowed_for_all: true`. |
| `blacklist_chat_ids` | Telegram chat IDs that are always denied. Wins over `allowed_for_all` and the whitelist. `null` / missing / `[]` → no blacklist. |
| `working_dir` | Claude Code working directory (`null` → process cwd). A non-existent path causes a startup error. |
| `logs_dir` | Root log directory. `null` → console only. Otherwise `<logs_dir>/<internal_name>/bot.log` + `<chat_id>.log` are written. |
| `draft_interval_sec` | Minimum seconds between draft-message updates while streaming. Default `0.2`. |
| `approval_timeout_sec` | Seconds to wait for a permission reply before auto-deny. Default `300`. |
| `agent_timeout_sec` | Hard timeout per Claude turn (seconds). Default `600`. The wrap also covers `can_use_tool` interceptor time, so set this above the slowest combination of generation + user reaction time. |
| `session_idle_ttl_sec` | Idle TTL for a per-chat `ClaudeSDKClient`; closed by background GC after this many seconds without traffic. Default `86400` (24 h). Set `0` to disable. |
| `chat_logger_capacity` | Max number of per-chat file loggers kept in memory (LRU). Default `256`. |
| `groq_api_key` | Groq API key for voice/audio transcription. Override via env `GROQ_API_KEY_<INTERNAL_NAME>` or fallback `GROQ_API_KEY`. `null` / missing → voice handler is disabled. |
| `groq_model` | Whisper model on Groq. Default `whisper-large-v3-turbo`. |
| `groq_timeout_sec` | HTTP timeout for the transcription call. Default `60.0`. |
| `voice_max_duration_sec` | Reject voice/audio longer than this (seconds). Default `600`. `0` disables the cap. |
| `uploads_dir` | Directory for incoming `photo` / `document` / `sticker` files. `null` / missing → uploads are disabled. The path is also forwarded to `ClaudeAgentOptions(add_dirs=[...])` so Claude's `Read` works without a permission prompt. |
| `upload_max_bytes` | Reject uploads larger than this (bytes). Default `20971520` (20 MB). `0` disables the local check. |
| `commands_dir` | Directory with `*.md` files defining user slash commands. Missing field / `null` → no extra commands. A non-existent directory raises a startup error. See section 10 and [COMMANDS.md](COMMANDS.md). |

`internal_name` is the top-level key (`brain` in the example). The log subdirectory is named after it.

### Multiple bots

You can declare several sections — each one runs concurrently inside the same process:

```json
{
  "brain": { "telegram_bot_token": "...", "working_dir": "/path/A" },
  "research": { "telegram_bot_token": "...", "working_dir": "/path/B" }
}
```

## 5. Run

```bash
source .venv/bin/activate
python -m src.bot
```

You should see in the console:

```
INFO root: loaded 1 bot(s): brain
INFO bot.brain: [brain] starting as @YourBot
INFO aiogram.dispatcher: Run polling for bot @YourBot ...
```

Open the bot in Telegram → `/start` → ask a question. The bot will:

1. Set an emoji reaction on your message.
2. Stream Claude's reply through `sendMessageDraft` (typing animation).
3. Send the final response as a separate Telegram HTML message.

Commands (also visible in the Telegram `/` menu):

- `/start` — greeting.
- `/new` — start a fresh Claude Code session (context dropped, armed `/plan` cleared, active quiz cancelled).
- `/context` — show context-window usage (percentage, used / max tokens, model, top categories).
- `/plan <task>` — engage `permission_mode="plan"` for the chat and send the task. Claude builds a plan and calls `ExitPlanMode`; the bot renders it with Approve / Reject buttons. Freeform text while the buttons are on screen → Reject + feedback.
- `/plan` (no args) — arm plan mode, the next text or voice message becomes the plan prompt.
- `/cancel` — drop an armed `/plan` wait. Does **not** reset the session, does **not** cancel quizzes.
- `/stop` — interrupt the running turn (`ClaudeSDKClient.interrupt`). Session stays open; next message starts a new turn with the same context.
- `/mode [default|acceptEdits|plan]` — switch permission mode without resetting the session. No argument → inline keyboard.
- `/model [<id>|default]` — switch model. No argument → inline keyboard with Opus 4.7 / Sonnet 4.6 / Haiku 4.5 / default.
- `/mcp` — list MCP servers attached to the session, grouped by status.
- `/info` — show server info: active output style, available styles, slash commands exposed by the SDK.
- `/whoami` — show your chat_id, access type, current mode, whether the session is live.
- `/help` — list every command with its description and a usage example for the more involved ones.
- Any `/<name>` defined in `commands_dir` (see [COMMANDS.md](COMMANDS.md)).

Voice / audio messages are transcribed via Groq when `groq_api_key` is set — the bot echoes the transcript as a blockquote and runs the same agent flow on the recognized text.

Photos, documents and stickers are saved under `<uploads_dir>/<chat_id>/` when `uploads_dir` is set. The agent runs right after the upload — caption (if any) acts as the user prompt; otherwise Claude gets just the file paths and is told to inspect them. Albums are debounced ~1.5 s so a multi-photo upload becomes one agent turn. Claude reads the saved files via the `Read` tool; `uploads_dir` is passed to the SDK as `add_dirs`, so reads are not blocked even when the path is outside `working_dir`.

## 6. Restricting who can talk to the bot

The bot is **fail-closed by default**: a missing or empty whitelist means nobody is allowed.

Set `allowed_chat_ids` to a list of Telegram chat IDs that may use the bot:

```json
"allowed_chat_ids": [123456789, 987654321]
```

Full semantics — gate evaluates in this order: `blacklist_chat_ids` → `allowed_for_all` → `allowed_chat_ids`.

| `allowed_for_all` | `allowed_chat_ids` | `blacklist_chat_ids` | Effect for sender X |
|---|---|---|---|
| `false` (default) | `null` / missing / `[]` | any | Closed to everyone. |
| `false` (default) | `[id, ...]` | `[]` | Whitelist only. Outsiders get the refusal. |
| `false` (default) | `[id, ...]` | `[X, ...]` | X is denied even if whitelisted. |
| `true`            | anything             | `[]` | Open to everyone. Startup logs a warning. |
| `true`            | anything             | `[X, ...]` | Open to everyone *except* X (and other blacklisted IDs). |

When someone outside the list sends a message, the bot replies with their `chat_id` and instructions to forward it to the administrator. The admin adds the ID to `config.json` and restarts the process.

To find your own chat_id quickly: leave `allowed_chat_ids` empty (`[]`), send a message, copy the `chat_id` from the refusal, then add it.

To kick a misbehaving user from a public bot: add their ID to `blacklist_chat_ids` and restart. The blacklist beats `allowed_for_all`.

> **Public bots**: only set `"allowed_for_all": true` if you really want anyone on Telegram to drive the agent on `working_dir`. Combined with permission rules in `.claude/settings.local.json`, this is how you'd run a read-only public assistant. For everything else, use the whitelist.

## 7. Voice transcription (optional)

Voice notes and `audio` attachments are routed to Groq's OpenAI-compatible `audio/transcriptions` endpoint. Setup:

1. Create a key at [console.groq.com/keys](https://console.groq.com/keys).
2. Either drop it into `config.json` as `groq_api_key`, or export `GROQ_API_KEY=...` (per-bot override: `GROQ_API_KEY_<INTERNAL_NAME>`).
3. Restart the process — startup logs print `groq transcription enabled (model=...)` when the feature is active.

Tunables:

- `groq_model` — default `whisper-large-v3-turbo`. Switch to `whisper-large-v3` for higher accuracy.
- `groq_timeout_sec` — HTTP timeout. Default `60.0`.
- `voice_max_duration_sec` — drop audio longer than this. Default `600`. `0` disables the cap.

Behaviour without a key: any voice/audio message gets a one-line refusal (`voice_disabled`) and is not forwarded to the agent.

## 8. File uploads (optional)

Photos, documents and stickers are saved under `<uploads_dir>/<chat_id>/<timestamp>_<file_id>_<original_name>`. Setup:

1. Add `"uploads_dir": "/var/lib/telegram-agent-bot/uploads"` (or any writable absolute path) to `config.json`.
2. Make sure the user running the bot can write to that directory and the user running Claude Code can read from it (in the simple case both are the same user).
3. Restart — startup logs print `uploads enabled at <path>` when the feature is active.

Flow:

- Single file → agent fires immediately. The caption (if any) becomes the user prompt; otherwise Claude gets only the file paths and is asked to use `Read`.
- Album (multiple photos in one Telegram message) → debounced ~1.5 s. The agent runs once after the last item lands. Caption from any item in the album is preserved.
- Subsequent text/voice messages also drain anything still queued, so files attached at different times can be combined into one prompt.

Filename layout per file kind:

| Telegram type | Saved as | `kind` shown to Claude |
| --- | --- | --- |
| `photo` (compressed) | `<ts>_<file_id>_photo.jpg` | `image` |
| `document` | `<ts>_<file_id>_<original file name>` | `document` |
| `sticker`, static (`.webp`) | `<ts>_<file_id>_sticker_<set>.webp` | `image` |
| `sticker`, animated (`.tgs`) | `<ts>_<file_id>_sticker_<set>.tgs` | `binary (animated sticker, Lottie JSON)` |
| `sticker`, video (`.webm`) | `<ts>_<file_id>_sticker_<set>.webm` | `binary (video sticker)` |

Permissions: `uploads_dir` is forwarded to `ClaudeAgentOptions(add_dirs=[...])`. Files saved there can be read by Claude's `Read` tool without triggering the inline-button permission prompt — even when `uploads_dir` is outside `working_dir`. Other tools acting on those paths (`Edit`, `Bash`) still go through the gate as usual.

Tunables:

- `upload_max_bytes` — reject uploads larger than this. Default `20971520` (20 MB). `0` disables the local check.

Behaviour without `uploads_dir`: every `photo` / `document` / `sticker` gets `upload_disabled` and the file is **not** saved.

## 9. Permission prompts

When Claude wants to use an `ask`-level tool (Bash, Write, Edit, etc.) the bot sends inline buttons:

- **✅ Allow** — once.
- **🚫 Deny** — once.
- **♾️ Always allow this session** — adds an `addRules / behavior=allow / destination=session` rule. Reset on `/new` or restart.

To allow a tool persistently (across restarts), add a rule manually to `<working_dir>/.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": ["Read", "Edit", "Bash(ls:*)"]
  }
}
```

The SDK loads `user/project/local` settings — those rules never reach the permission gate.

The prompt message is **deleted** as soon as you click any button (or on timeout). The verdict shows up as a Telegram callback toast, so the chat does not pile up with stale button rows.

### `AskUserQuestion` (interactive quiz)

Claude's built-in `AskUserQuestion` tool is intercepted before it would otherwise hit the Allow/Deny/Always gate. The bot walks the `questions` array sequentially:

- Single-select: each option is a button — first tap fires the answer.
- Multi-select: option buttons toggle `▫️ ↔ ☑️`. The bottom row carries `✅ Done` to submit.
- Every question has a `⏭ Skip` button.

Result handed back to Claude (as the tool response):

```
User responded to AskUserQuestion via Telegram inline buttons:

1. <question text>
   → 'option label A'

2. <question text>
   → (skipped)
```

Per-question timeout reuses `approval_timeout_sec` (default 300 s). On expiry the bot sends a short notice and tells Claude no answer was given.

Sending any new message (text / voice / photo / document / sticker) while a quiz is mid-flight auto-skips the rest of the questions — the old turn finishes immediately and the new message is processed. This avoids a deadlock when you start typing a follow-up instead of clicking a stale button.

### `/plan` and `ExitPlanMode` (plan approval)

`/plan` is the user-facing entry point for Claude Code's plan mode:

- `/plan <task>` — calls `ClaudeSDKClient.set_permission_mode("plan")` on the per-chat client (no session reset; context is preserved) and sends the task as the prompt.
- `/plan` with no arguments — arms plan mode and waits for the next text or voice message; that message becomes the plan prompt. `/cancel` clears the wait. `/new` also clears it as part of the session reset.

When Claude requests to leave plan mode (calls `ExitPlanMode`), the bot:

- Renders the `plan` markdown to the chat using the same Telegram HTML sender as regular replies. Long plans are split into ≤ 4000-char messages; if Telegram rejects an HTML chunk, the original body is sent as plain text instead.
- Posts a separate compact message holding two buttons: `✅ Approve` and `🚫 Reject`.
- Resolves on whichever happens first:
  - **Tap Approve** → `PermissionResultAllow()` — the SDK exits plan mode and lets Claude execute. The chat sees `▶️ Plan approved — agent started.`.
  - **Tap Reject** → `PermissionResultDeny(message="User rejected the plan.")`. The chat sees `🚫 Plan rejected.`.
  - **Type a freeform reply** while the buttons are on screen — counts as Reject *with feedback*. The text becomes the deny message and Claude reads it as the tool's failure reason, so it can revise the plan. The chat sees `🚫 Plan rejected — forwarding feedback to the agent.`.
- On `approval_timeout_sec` expiry the prompt is dropped and Claude sees a generic rejection.

Auto-cancel of armed `AskUserQuestion` quizzes is **not** wired here; freeform text *is* the response, so we let it through. All approve/reject decisions and the rendered plan length are written into `<chat_id>.log` as INFO entries.

### `PushNotification` (model-driven notifications)

`PushNotification` calls are intercepted: the `message` field is forwarded to the chat as `🔔 …` and Claude is told the notification was delivered. No buttons, no waiting.

### `Monitor` / `TaskOutput` (status mirroring)

These tools must keep running so the SDK can do its thing. The bot configures `PreToolUse` and `PostToolUse` hooks for the matcher `Monitor|TaskOutput`:

- **Pre**: chat sees `🔧 Monitor: <description>` (or `🔧 Monitor` if no description). The tool then runs as usual.
- **Post**: chat sees `✅ Monitor done` plus a preview of up to 6 lines / 600 chars of the tool's response if available.

i18n keys: `tool_status_pre`, `tool_status_pre_no_desc`, `tool_status_post`, `tool_status_post_with_preview`.

## 10. Custom slash commands (optional)

Drop a directory of `*.md` files anywhere readable, point `commands_dir` at it, and every file becomes a Telegram bot command whose body is sent to Claude as the user prompt. Full reference: [COMMANDS.md](COMMANDS.md).

Example `commands/recall.md`:

```markdown
---
name: recall
description: Search project memory for relevant entries
---
Search handoff / decisions / archive for anything related to: $ARGUMENTS
Quote at most 3 lines per match and link the source.
```

When the user types `/recall vector store decision`, the body's `$ARGUMENTS` is replaced with `vector store decision` and the resulting prompt is sent to Claude. Without arguments `$ARGUMENTS` is replaced with an empty string.

Frontmatter rules:

- `name` (optional, defaults to file stem). Must be 1–32 chars, lowercase letters / digits / `_`, starting with a letter. Names that clash with built-ins (`start`, `new`) are skipped with a warning.
- `description` (optional, defaults to `name`). Trimmed to 256 chars (the Telegram limit).
- Only the leading `--- ... ---` block is parsed. Body is everything after the closing `---`.

Loading:

- Discovery is `*.md` flat (no recursion). Run with `commands_dir` writable to whoever edits files; Claude does not need access to it.
- Files are loaded **once at startup**. Restart the bot to pick up edits.
- Bad files (invalid name, empty body, parse error) are skipped with a `WARNING` in `bot.log`. The rest still load.
- Commands appear in the Telegram menu (`/`), alongside `/start` and `/new`.

## 11. Directory layout

```
agent-bot/
├── src/
│   ├── __init__.py
│   ├── bot.py                  # entry: run_bot + _supervise + main + _make_* factories
│   ├── config/
│   │   ├── __init__.py         # BotConfig (pydantic), load()
│   │   ├── config.json         # real config, .gitignore'd
│   │   └── config.example.json # template
│   ├── i18n/
│   │   ├── __init__.py         # Translator
│   │   └── <lang>.json         # ar, bn, de, en, es, fr, hi, id, ja, ko, mr, pt, ru, sw, ta, te, tr, ur, vi, zh
│   ├── infra/                  # state managers
│   │   ├── agent.py            # AgentSessionManager (per-chat ClaudeSDKClient + idle GC + mode/model mirrors)
│   │   ├── commands.py         # *.md → CommandDef loader
│   │   ├── logs.py             # BotLogs (bot.log + per-chat files, LRU-bounded)
│   │   ├── streaming.py        # DraftStreamer (sendMessageDraft animation, token-redacting logs)
│   │   └── interactions/       # TelegramInteractionGate package
│   │       ├── __init__.py     # re-exports TelegramInteractionGate
│   │       ├── gate.py         # class + shared helpers + dispatch
│   │       ├── permission_prompt.py   # Allow / Deny / Always flow
│   │       ├── ask_user_question.py   # AskUserQuestion flow
│   │       ├── plan_mode.py    # ExitPlanMode flow
│   │       └── push_notification.py   # PushNotification flow
│   ├── services/
│   │   ├── transcribe.py       # GroqTranscriber (voice/audio → text; accepts bytes or file-like)
│   │   └── upload_store.py     # UploadStore + PendingFile + format_attachment_prompt
│   ├── ui/                     # bot-side UX helpers (no aiogram handlers)
│   │   ├── agent_reply.py      # react_to + reply_with_agent
│   │   ├── album.py            # AlbumDebouncer (media_group_id coalescing)
│   │   ├── markdown.py         # to_html, send_md, audio_filename, format_quote, TG_LIMIT
│   │   ├── middleware.py       # AclMiddleware + deny_access
│   │   ├── plan_router.py      # PlanRouter (per-chat /plan arm state + fire helper)
│   │   ├── reactions.py        # ReactionPicker (keyword → emoji)
│   │   ├── sdk_views.py        # format_context_usage / format_mcp_status / format_server_info
│   │   └── tool_status.py      # ToolStatusMirror (PreToolUse / PostToolUse hooks)
│   └── handlers/               # aiogram handlers (top-level fns + register(dp))
│       ├── __init__.py         # register_all(dp, custom_commands)
│       ├── context.py          # BotContext (frozen dataclass holding all wiring)
│       ├── basic.py            # /start /new /cancel /context /stop /mcp /info /whoami /help
│       ├── custom.py           # user-defined slash commands from commands_dir
│       ├── plan.py             # /plan + perm/aq/plan callbacks
│       ├── selectors.py        # /mode /model + their callbacks
│       ├── text.py             # F.text catch-all
│       ├── voice.py            # F.voice | F.audio
│       └── uploads.py          # F.photo, F.document, F.sticker
├── tests/                      # 76 pytest unit tests (config, commands, i18n, uploads, markdown, reactions, sdk_views, plan_router, streaming, logs, bot factories)
├── commands/                   # example .md custom commands (gitignored)
├── logs/                       # auto-created when logs_dir is set
├── uploads/                    # auto-created when uploads_dir is set
├── pyproject.toml              # build, deps, ruff/mypy/pytest config; [project.scripts] agent-bot
├── requirements.txt            # legacy mirror of pyproject runtime deps
├── AGENTS.md                   # full project guide for LLMs
├── CONFIG.md                   # per-field config reference
├── COMMANDS.md                 # custom slash-command reference
├── CLAUDE.md                   # Claude Code orientation
├── INSTALLATION.md
├── README.md
└── .gitignore
```

## 12. Updating

```bash
git pull
source .venv/bin/activate
pip install -e ".[dev]"
```

Restart the process so changes take effect.

## Troubleshooting

| Symptom | Check |
|---|---|
| `config.json not found` | Did you copy `config.example.json` → `config.json`? Is it inside `src/config/`? |
| `telegram_bot_token is missing` | Placeholder `put-...` left in place or field is empty. |
| `working_dir does not exist` | `working_dir` is not a directory. Fix it or set to `null`. |
| `claude login` complains | Is `@anthropic-ai/claude-code` installed? Does `which claude` resolve? |
| Bot is silent | Check logs at `logs/<internal_name>/bot.log`. Confirm `Run polling` appeared. |
| Permission prompt every time | No rules in `.claude/settings.local.json` inside `working_dir`. Or use the "Always" button in chat. |
| No streaming animation | `sendMessageDraft` is a recent Bot API method. Make sure your Telegram client is up to date. |
| Voice message gets `voice_disabled` reply | `groq_api_key` not set in `config.json` and no `GROQ_API_KEY` / `GROQ_API_KEY_<NAME>` env var. |
| Transcription returns `voice_error` | Check `bot.log` — usually a bad API key, exhausted quota, or unsupported audio format. |
| Voice longer than expected gets rejected | Bump `voice_max_duration_sec` (default `600`s) or set it to `0`. |
| Photo/document gets `upload_disabled` | `uploads_dir` not set in `config.json`. Add it and restart. |
| `upload_too_large` reply | Raise `upload_max_bytes` or set `0` to disable the local check. The Telegram Bot API hard cap is 20 MB without a self-hosted Bot API server. |
| Claude says it cannot find the file | Confirm Claude Code's user can read `uploads_dir`. The path printed in logs (`upload saved: ... path=...`) must be reachable from `working_dir` user context. |
| `Read` permission prompt for an uploaded file | The bot wires `uploads_dir` into `ClaudeAgentOptions(add_dirs=[...])` automatically. If you still see prompts, you either restarted before this fix or `uploads_dir` is unset — re-check `config.json` and the startup log line `uploads enabled at <path>`. |
| Quiz buttons appear but nothing happens after click | Make sure the chat's `chat_id` is in `allowed_chat_ids` (the callback authz check rejects out-of-chat clicks). Restart if you upgraded across the AskUserQuestion patch. |
| New message ignored while a quiz is on screen | Old behaviour. After the auto-skip patch, any new message cancels the running quiz and is processed in the same turn. Restart the bot if you are running the pre-patch build. |
