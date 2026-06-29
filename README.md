<h1 align="center">Agent Bot for Telegram (ABT)</h1>

<p align="center">
  Run Claude Agent SDK or Codex SDK on Telegram.
  Named multi-sessions per chat, streaming replies with thinking tokens, voice input, file uploads, scheduled tasks, plan mode, MCP — all in your favourite messenger.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue.svg">
  <img alt="aiogram" src="https://img.shields.io/badge/aiogram-3.29%2B-2CA5E0.svg">
  <img alt="Claude" src="https://img.shields.io/badge/Claude-Agent%20SDK-D77757.svg">
  <img alt="Codex" src="https://img.shields.io/badge/Codex-SDK-111111.svg">
</p>

---

## ✨ Highlights

- 🪄 **Custom slash commands — killer feature.** Drop a `*.md` file into `commands_dir`, get a Telegram bot command. Body is the prompt; `$ARGUMENTS` substitutes whatever the user typed. Ship `/recall`, `/today`, `/standup`, `/capture` to your team **without touching code**. See [COMMANDS.md](COMMANDS.md).
- ⏰ **Scheduled & recurring tasks.** `/task add <once|interval|cron> | <prompt>` schedules an LLM turn; admins can schedule shell/Python **script** tasks and **global** ones. The agent can even schedule its own follow-ups via a per-chat MCP task tool. Background scheduler fires due tasks, no double-firing, with run history. See [CONFIG.md](CONFIG.md) and [docs/tasks.md](docs/tasks.md).
- 🤖 **Claude, Codex, or PI.dev backend** — choose `agent_provider` per configured Telegram bot.
- 💬 **Named multi-sessions per chat** — each chat owns several named sessions; `/sess` lists / switches them, `/new` starts a fresh one. Metadata lives in the per-chat SQLite; legacy session JSON migrates in on first start.
- ✨ **Rich messages (Bot API 10.1)** — `sendRichMessage` rendering with headings, tables, spoilers, `mark`/`sub`/`sup`/`details`.
- ⚡ **Streaming with thinking tokens** — animated draft via `sendRichMessageDraft`; reasoning streamed inside `<tg-thinking>`.
- 🎙️ **Voice & audio in** — transcribed by Groq Whisper, fed into the agent.
- 📎 **Photo / document / sticker uploads** — the agent reads saved files; albums coalesced into one turn.
- 🗂️ **SQLite message log + full-text search** — every per-chat event mirrored to `<chat_id>.db` with role + session; FTS5 trigram index for substring search across the history.
- 🛡️ **Permission gate** — Allow / Deny / Always-allow-this-session inline buttons for every tool call.
- 🧠 **Plan mode** — `/plan <task>` engages `permission_mode="plan"`; Approve / Reject keyboard for `ExitPlanMode`.
- ❓ **AskUserQuestion → keyboards** — single- and multi-select inline polls returned to Claude as plain text.
- 🌍 **20 languages out of the box** — `ar bn de en es fr hi id ja ko mr pt ru sw ta te tr ur vi zh`.
- 🔒 **Fail-closed access control** — per-bot whitelist / blacklist / open mode, plus `admin_chat_ids` gating global & script tasks.
- 🤖 **Run many bots in one process** — `asyncio.gather` over a `<name>: BotConfig` map.

## 🚀 Quick start

```bash
git clone <repo-url> abt && cd abt
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

claude login                                       # for agent_provider="claude"
# Codex backend uses openai-codex / Codex auth when agent_provider="codex"
# PI backend uses `pi --mode rpc` when agent_provider="pi"
cp src/config/config.example.yaml src/config/config.yaml
# edit: gateway.telegram_bot_token + gateway.access.allowed_chat_ids + agent.provider

python -m src.bot                                  # or: abt
```

Don't know your `chat_id`? Leave `allowed_chat_ids: []`, message the bot — the refusal contains it. Add it back, restart.

## 🧭 Bot commands

`/start` `/new` `/sess` `/task` `/tasks` `/context` `/plan` `/cancel` `/stop` `/mode` `/model` `/mcp` `/info` `/whoami` `/help` + any user-defined slash commands from `commands_dir`.

`/sess` lists and switches named sessions; `/task` schedules one-shot / recurring work and `/tasks` lists active ones (shown only when the `tasks` section is enabled).

Drop `*.md` files into `commands_dir` to expose reusable workflows (`/recall`, `/today`, `/standup`, …) with `$ARGUMENTS` substitution. See [COMMANDS.md](COMMANDS.md).

## 📚 Documentation

| File | What's inside |
|---|---|
| [INSTALLATION.md](INSTALLATION.md) | Step-by-step setup, multi-bot config, troubleshooting. |
| [CONFIG.md](CONFIG.md) | Every `BotConfig` field — type, default, validation, env override. |
| [COMMANDS.md](COMMANDS.md) | Custom slash commands: frontmatter, `$ARGUMENTS`, examples. |
| [docs/tasks.md](docs/tasks.md) | Scheduled task storage, scheduler/runner lifecycle, timeouts, and recovery. |
| [AGENTS.md](AGENTS.md) | Architecture reference for LLM agents working in this repo. |
| [CLAUDE.md](CLAUDE.md) | Short orientation pinned to the repo for Claude Code. |

## 🛠 Tech

[aiogram 3](https://docs.aiogram.dev/) · [claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python) · [openai-codex](https://developers.openai.com/codex/sdk) · [pydantic 2](https://docs.pydantic.dev/) · [markdown-it-py](https://pypi.org/project/markdown-it-py/) · Groq Whisper.

Full deps + dev tooling (`ruff`, `mypy`, `pyright`, `bandit`, `pip-audit`, `pytest`) declared in [pyproject.toml](pyproject.toml).

## 📄 License

[MIT](LICENSE) — © 2026 Eugene Myazin.
