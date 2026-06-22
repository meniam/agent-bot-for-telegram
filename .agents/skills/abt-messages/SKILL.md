---
name: abt-messages
description: Reads the message history of a specific chat from the bot's SQLite database by CHAT_ID. Filters by session, role (user/bot/tool/system), time, and limit; full-text search over message text (FTS5); output as text or JSON. Use when you need to view/analyze a chat conversation, search it for a word/phrase, restore session context, or dump messages.
---

# abt-messages

Reads messages from the per-chat database `<messages_dir>/<CHAT_ID>.db` by
**CHAT_ID** — the user's current chat id from the GATEWAY.

## Argument

CHAT_ID is passed **externally as an argument** when invoking the skill. It is the
Telegram chat id from the gateway. Without it, the database cannot be selected.

`$ARGUMENTS` = `<CHAT_ID> [extra filters]`.

## How to read

Run the helper `scripts/messages.py` from the skill directory. It locates the
database by CHAT_ID and reuses `query_messages` from `src/infra/message_db.py` (the
same SQL the bot's writer uses).

```bash
python3 .claude/skills/abt-messages/scripts/messages.py <CHAT_ID>
```

> The skill's source is `.agents/skills/abt-messages`; at startup the bot symlinks it
> into the current provider's skills directory (`.claude/skills` for Claude). The path
> above is for the `claude` provider; for another, replace the prefix (`.codex/skills`,
> etc.).

Database: `<messages_dir>/<CHAT_ID>.db`. messages_dir is resolved in this order:

1. `--messages-dir`
2. `$ABT_MESSAGES_DIR`
3. `<repo>/var/brain/messages` (default from `config.yaml`)

## Filters

| Flag                  | Purpose                                  |
| --------------------- | ---------------------------------------- |
| `--limit N`           | max rows (default 50, cap 500)           |
| `--role R`            | `user` \| `bot` \| `tool` \| `system`    |
| `--session-id ID`     | a specific session                       |
| `--since` / `--until` | ISO date/time or epoch seconds           |
| `-q` / `--search Q`   | full-text search over message text       |
| `--json`              | JSON output                              |
| `--messages-dir DIR`  | override the database directory          |

## Search

`--search Q` runs full-text search over the message text via SQLite FTS5 with
the **trigram** tokenizer: substring matching (≥3 chars), case-insensitive and
language-agnostic — `paym` matches `payment`, `payments`, `prepayment`. This
tolerates inflected forms (endings/cases), useful for non-English text. Results
are ordered by relevance (`bm25`), not time, and each text row shows a
highlighted `[…]` snippet. Combine with `--role`, `--session-id`,
`--since`/`--until` to narrow the search.

## Examples

```bash
# last 50 messages of a chat
python3 .claude/skills/abt-messages/scripts/messages.py 123456789

# last 100 user messages
python3 .claude/skills/abt-messages/scripts/messages.py 123456789 --role user --limit 100

# messages in a time range, JSON
python3 .claude/skills/abt-messages/scripts/messages.py 123456789 \
  --since 2026-06-01 --until 2026-06-22 --json

# a specific session
python3 .claude/skills/abt-messages/scripts/messages.py 123456789 --session-id <SID>

# full-text search, user messages only
python3 .claude/skills/abt-messages/scripts/messages.py 123456789 --search "payment" --role user
```

## Behavior

- No database for CHAT_ID → stderr + exit 1.
- No range → last N in chronological order.
- With `--since`/`--until` → matches in ascending order.
- With `--search` → matches ranked by relevance; other filters still apply.
