# Running abt in Docker

The image carries Python + Node + all three agent CLIs (`claude`, `pi`, `codex`)
and runs `python -m src.bot`. Host-specific bits (config, vault, credentials,
state) come in as mounts — nothing secret is baked into the image.

## Files

- [.docker/abt/Dockerfile](../.docker/abt/Dockerfile) — Python 3.12 + Node 22 +
  npm globals (`@anthropic-ai/claude-code`, `@earendil-works/pi-coding-agent`,
  `@openai/codex`), editable install of the project.
- [.docker/docker-entrypoint.sh](../.docker/docker-entrypoint.sh) — pre-creates
  the state dirs (`commands_dir` must exist; the loader does not create it). Runs
  at runtime, not build, because `/app/var` is a bind mount that shadows
  build-time dirs.
- [.docker/graphiti-config.yaml](../.docker/graphiti-config.yaml) — Graphiti MCP
  server config (Neo4j backend), mounted into the graphiti service.
- [docker-compose.yml](../docker-compose.yml) — `abt` + `graphiti` + `neo4j`
  services, mounts, the `./var` bind.
- `src/config/config.docker.example.yaml` — tracked template.
- `src/config/config.docker.yaml` — real config **with secrets inline**,
  gitignored. Bind-mounted over `src/config/config.yaml` at runtime. (Mirrors the
  `config.yaml` / `config.example.yaml` pair.)
- `.env` — root env for compose `${VAR}` interpolation (OpenAI key, Neo4j
  password); gitignored, see `.env.example`. Feeds graphiti/neo4j only, not the bot.

## Setup

```bash
cp src/config/config.docker.example.yaml src/config/config.docker.yaml
# edit it: fill telegram_bot_token, voice.api_key (Groq), access chat ids
```

### Claude auth — the macOS gotcha

`claude login` on macOS stores the OAuth token in the **Keychain**, not in a
file, so bind-mounting `~/.claude` carries config but **not** the token (the
container reports "Not logged in"). The Linux CLI reads
`~/.claude/.credentials.json`. Export the keychain token into that file once:

```bash
security find-generic-password -s "Claude Code-credentials" -w \
  > ~/.claude/.credentials.json
chmod 600 ~/.claude/.credentials.json
```

The bind-mount then carries it in. The container self-refreshes the token via
the refresh token in that file. Re-export if the login is ever revoked.

Alternative: skip the mount and set `ANTHROPIC_API_KEY` as an env var on the
service (compose `environment:`) — uses API billing, not the subscription.

Codex (`~/.codex/auth.json`) and PI (`~/.pi`) are already file-based — their
mounts work as-is.

## Run

```bash
docker compose up -d --build
docker compose logs -f
```

> Only one process may poll a given Telegram token. Stop any host-run bot
> (`pkill -f src.bot`) before starting the container, or Telegram returns 409.

## Mounts

| Host | Container | Purpose |
|------|-----------|---------|
| `src/config/config.docker.yaml` | `/app/src/config/config.yaml` (ro) | config |
| `/Users/eugene/Documents/Obsidian/Brain` | `/vault` (rw) | agent working dir |
| `~/.claude` / `~/.codex` / `~/.pi` | `/root/.*` | agent creds + session history |
| `./var` | `/app/var` | logs, message DBs, tasks, commands, uploads |

## Switching provider

Edit `agent.provider` (`claude` / `codex` / `pi`) in `config.docker.yaml`, then
`docker compose up -d` (no rebuild — config is mounted). All three CLIs are
already in the image.

## Graphiti knowledge-graph memory

Two extra services give the Claude agent a temporal knowledge graph as an MCP
memory tool: `graphiti` (the MCP server, `zepai/knowledge-graph-mcp:standalone`)
backed by `neo4j`. Config: [.docker/graphiti-config.yaml](../.docker/graphiti-config.yaml).
Neo4j data persists under `./var/neo4j`; browse it at <http://localhost:7474>.

Setup:

1. Put a real `OPENAI_API_KEY` in `.env` — Graphiti needs an LLM **and** an
   embedder; without it every graph op fails. `NEO4J_PASSWORD` is also read from
   `.env`.
2. The bot does **not** connect the agent to the HTTP server directly. Instead it
   wraps it in a per-chat in-process MCP server (see "Per-chat isolation" below),
   so the raw `graphiti-memory` server in `/vault/.mcp.json` is **disabled** in
   `/vault/.claude/settings.json` (`disabledMcpjsonServers`). Only the bot's
   proxy reaches Graphiti; it reuses the same tool name `graphiti-memory`.

### Per-chat isolation (multi-user gateway)

Graphiti partitions all data by `group_id`. The static `group_id` in the config
is a server-side default only; left as-is, every chat would share one namespace
and read/write each other's memory behind a shared gateway.

[`src/infra/graphiti_tool.py`](../src/infra/graphiti_tool.py) closes that hole.
At startup the bot discovers the upstream tool list once (`GraphitiProxy.discover`)
and mints a per-chat in-process MCP server (`build_graphiti_server(chat_id, …)`,
wired in `claude_agent.py` next to the `tasks` server). Every tool call has its
`group_id` / `group_ids` **forced to `str(chat_id)`**, overriding whatever the
model passes — so a chat can only ever touch its own namespace.

For the isolation to hold the raw HTTP server must stay out of the agent's tool
set; that is why it is disabled in `settings.json`. Override the endpoint with
`GRAPHITI_MCP_URL` / `GRAPHITI_MCP_HOST`; set `GRAPHITI_MCP_URL=""` to turn the
proxy (and memory) off entirely.

> **Not covered:** the vault's own custom memory pipeline
> (`/vault/.claude/scripts/memory/`, SQLite + hooks) still writes to one shared
> store keyed by `session_id`, not `chat_id`. Isolating that is separate work.

### Two non-obvious gotchas (both about reaching the HTTP server)

- **No trailing slash.** `…/mcp/` answers `307 → /mcp`, and the MCP client does
  not follow it (`✘ Failed to connect`). Use `…/mcp`.
- **Host header.** Graphiti builds its FastMCP instance with the default
  `127.0.0.1` host, which auto-enables DNS-rebinding protection allowing only
  `localhost` / `127.0.0.1`. A request with `Host: graphiti:8000` gets
  `421 Invalid Host header`. The proxy's httpx client sends `Host: localhost:8000`
  to pass the allowlist while still routing to the `graphiti` service.

Verify: bot startup logs `graphiti memory: N tools, per-chat group_id`.
