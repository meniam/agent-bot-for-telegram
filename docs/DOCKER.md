# Running abt in Docker

The image carries Python + Node + all three agent CLIs (`claude`, `pi`, `codex`)
and runs `python -m src.bot`. Host-specific bits (config, vault, credentials,
state) come in as mounts — nothing secret is baked into the image.

## Files

- [Dockerfile](../Dockerfile) — Python 3.12 + Node 22 + npm globals
  (`@anthropic-ai/claude-code`, `@earendil-works/pi-coding-agent`,
  `@openai/codex`), editable install of the project.
- [docker-entrypoint.sh](../docker-entrypoint.sh) — pre-creates the state dirs
  (`commands_dir` must exist; the loader does not create it). Runs at runtime,
  not build, because `/app/var` is a bind mount that shadows build-time dirs.
- [docker-compose.yml](../docker-compose.yml) — mounts + the `./var` bind.
- `src/config/config.docker.example.yaml` — tracked template.
- `src/config/config.docker.yaml` — real config **with secrets inline**,
  gitignored. Bind-mounted over `src/config/config.yaml` at runtime. (Mirrors the
  `config.yaml` / `config.example.yaml` pair.)

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
