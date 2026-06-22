# Custom Telegram slash commands

Drop `*.md` files into a directory and the bot exposes each one as a Telegram bot command. Body of the file is used as the user prompt sent to Claude.

## Setup

1. Pick a directory, e.g. `/etc/abt/commands` or `./commands`.
2. Add it to `src/config/config.yaml`:
   ```yaml
   brain:
     gateway:
       commands_dir: /absolute/path/to/commands
   ```
3. Drop one or more `*.md` files inside (see format below).
4. Restart the bot. The startup log prints `loaded N custom command(s) from <path>` and the commands appear in the Telegram menu alongside the built-in ones (`/start`, `/new`, `/context`, `/plan`, `/cancel`, `/stop`, `/mode`, `/model`, `/mcp`, `/info`, `/whoami`, `/help`).

Omitting `commands_dir` disables the feature. A non-existent directory raises a startup error.

## File format

```markdown
---
name: recall
description: Search project memory for relevant entries
---
Search handoff / decisions / archive for anything related to: $ARGUMENTS

Quote at most 3 lines per match and link the source.
```

- `name` (optional) — defaults to the file stem (`recall.md` → `recall`). Must be 1–32 chars, lowercase letters, digits and underscores, starting with a letter. Names colliding with built-ins (`start`, `new`, `context`, `plan`, `cancel`, `stop`, `mode`, `model`, `mcp`, `info`, `whoami`, `help`) or with previously-loaded commands are skipped with a warning.
- `description` (optional) — defaults to `name`. Trimmed to 256 chars (the Telegram limit).
- Frontmatter is a small `key: value` subset of YAML — no nested structures, no lists. Quotes around values are stripped. Anything outside the leading `--- ... ---` block is treated as the body.
- Body is everything after the closing `---`. Empty bodies are rejected.

The frontmatter parser pulls in no extra dependencies. See [src/infra/commands.py](src/infra/commands.py) for the exact rules.

## `$ARGUMENTS` substitution

Whatever the user types after the command becomes `$ARGUMENTS` in the body:

```
User in Telegram:    /summarize the latest changelog and group by area
Body template:       Summarize $ARGUMENTS. Keep it under 5 bullet points.
Prompt sent to LLM:  Summarize the latest changelog and group by area. Keep it under 5 bullet points.
```

If the user runs the command with no arguments, `$ARGUMENTS` is replaced with an empty string.

Substitution is a plain text replace — `$ARGUMENTS` may appear multiple times, and the body can mix it with literal markdown. There is currently no escaping for `$ARGUMENTS` itself; if you need a literal token, pick a different placeholder.

## Examples

`commands/recall.md`:

```markdown
---
name: recall
description: Search project memory
---
Search handoff / decisions / archive for: $ARGUMENTS
Return at most 5 hits, quote 1–3 lines each, link the source path.
```

Usage in Telegram: `/recall vector store decision`.

`commands/today.md`:

```markdown
---
name: today
description: What's on my calendar today
---
List today's calendar events. Group by morning / afternoon / evening.
Add a one-line action item for each. No fluff.
```

Usage: `/today` (no `$ARGUMENTS` needed — the body has none).

`commands/standup.md`:

```markdown
---
name: standup
description: Generate stand-up update
---
Build my stand-up update for the team based on yesterday's commits and
today's plan. Tone: $ARGUMENTS.
```

Usage: `/standup terse and technical`.

## Behaviour notes

- Commands are loaded **once at startup**. Restart the bot to pick up edits or new files.
- Discovery is `*.md` flat — no recursion into subdirectories.
- Bad files (invalid name, empty body, parse error) are skipped with a `WARNING` in `bot.log`. The rest still load.
- Handlers are registered **before** the generic `F.text` handler, so `/<name>` is routed to the custom prompt instead of being treated as plain text.
- Each invocation goes through the same agent flow as a regular text message: same access checks (`allowed_for_all` / `allowed_chat_ids` / `blacklist_chat_ids`), same auto-cancel of pending `AskUserQuestion` quizzes, same drain of pending file uploads.
- Commands are listed via `set_my_commands`, so the Telegram client shows them in the `/` menu autocomplete with the description you provided.

## Layout

```
commands/
├── recall.md
├── today.md
└── standup.md
```

`commands/` is in `.gitignore` by default — keep your prompts out of the public repo or move them to a server-only path.
