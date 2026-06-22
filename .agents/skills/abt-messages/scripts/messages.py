#!/usr/bin/env python3
"""Read messages from a chat's SQLite message log by CHAT_ID.

Locates ``<messages_dir>/<chat_id>.db`` and prints the matching rows. Reuses
``src.infra.message_db.query_messages`` so the read path stays identical to the
bot's writer (no duplicated SQL).

messages_dir resolution order:
  1. --messages-dir
  2. $ABT_MESSAGES_DIR
  3. <repo_root>/var/brain/messages   (matches config.yaml default)

Examples:
  messages.py 123456789
  messages.py 123456789 --limit 100
  messages.py 123456789 --role user --since 2026-06-01
  messages.py 123456789 --session-id <sid> --json
  messages.py 123456789 --search "payment" --role user
"""

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

# <repo>/.agents/skills/abt-messages/scripts/messages.py → repo root.
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from src.infra.message_db import (  # noqa: E402
    MESSAGES_DEFAULT_LIMIT,
    query_messages,
    search_messages,
)


def _resolve_messages_dir(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()
    env = os.environ.get("ABT_MESSAGES_DIR")
    if env:
        return Path(env).expanduser()
    return ROOT / "var" / "brain" / "messages"


def _parse_when(value: str | None) -> float | None:
    """Accept an ISO date/datetime or a raw epoch-seconds value."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    return _dt.datetime.fromisoformat(value).timestamp()


def main() -> int:
    ap = argparse.ArgumentParser(description="Read chat messages by CHAT_ID.")
    ap.add_argument("chat_id", help="Telegram chat id from the gateway")
    ap.add_argument("--messages-dir", help="override messages dir")
    ap.add_argument("--session-id", help="filter by session id")
    ap.add_argument("--role", help="filter by role: user|bot|tool|system")
    ap.add_argument(
        "-q",
        "--search",
        help="full-text search (FTS5 trigram); ranked by relevance",
    )
    ap.add_argument("--since", help="ISO date/datetime or epoch seconds")
    ap.add_argument("--until", help="ISO date/datetime or epoch seconds")
    ap.add_argument(
        "--limit", type=int, default=MESSAGES_DEFAULT_LIMIT, help="max rows"
    )
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    db_path = _resolve_messages_dir(args.messages_dir) / f"{args.chat_id}.db"
    if not db_path.exists():
        print(f"no message db for chat {args.chat_id}: {db_path}", file=sys.stderr)
        return 1

    if args.search:
        rows = search_messages(
            db_path,
            args.search,
            session_id=args.session_id,
            role=args.role,
            since=_parse_when(args.since),
            until=_parse_when(args.until),
            limit=args.limit,
        )
    else:
        rows = query_messages(
            db_path,
            session_id=args.session_id,
            since=_parse_when(args.since),
            until=_parse_when(args.until),
            limit=args.limit,
            role=args.role,
        )

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("(no messages)")
        return 0
    for r in rows:
        tool = f" [{r['tool']}]" if r.get("tool") else ""
        sess = r.get("session_title") or (r.get("session_id") or "-")
        text = r.get("snippet") or r["message"]
        print(f"{r['created_at']} {r['role']}{tool} ({sess}): {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
