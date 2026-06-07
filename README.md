<h1 align="center">Telegram Agent Bot</h1>

<p align="center">
  Run Claude Agent SDK or Codex SDK on Telegram.
  Multi-turn chat per user, streaming replies, voice input, file uploads, plan mode, MCP — all in your favourite messenger.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue.svg">
  <img alt="aiogram" src="https://img.shields.io/badge/aiogram-3.13%2B-2CA5E0.svg">
  <img alt="Claude" src="https://img.shields.io/badge/Claude-Agent%20SDK-D77757.svg">
  <img alt="Codex" src="https://img.shields.io/badge/Codex-SDK-111111.svg">
</p>

---

## ✨ Highlights

- 🪄 **Custom slash commands — killer feature.** Drop a `*.md` file into `commands_dir`, get a Telegram bot command. Body is the prompt; `$ARGUMENTS` substitutes whatever the user typed. Ship `/recall`, `/today`, `/standup`, `/capture` to your team **without touching code**. See [COMMANDS.md](COMMANDS.md).
- 🤖 **Claude, Codex, or PI.dev backend** — choose `agent_provider` per configured Telegram bot.
- 💬 **Per-chat session memory** — every chat owns a live agent session; `/new` starts fresh.
- ⚡ **Token-by-token streaming** — animated draft via Bot API `sendMessageDraft`.
- 🎙️ **Voice & audio in** — transcribed by Groq Whisper, fed into the agent.
- 📎 **Photo / document / sticker uploads** — the agent reads saved files; albums coalesced into one turn.
- 🛡️ **Permission gate** — Allow / Deny / Always-allow-this-session inline buttons for every tool call.
- 🧠 **Plan mode** — `/plan <task>` engages `permission_mode="plan"`; Approve / Reject keyboard for `ExitPlanMode`.
- ❓ **AskUserQuestion → keyboards** — single- and multi-select inline polls returned to Claude as plain text.
- 🌍 **20 languages out of the box** — `ar bn de en es fr hi id ja ko mr pt ru sw ta te tr ur vi zh`.
- 🔒 **Fail-closed access control** — per-bot whitelist / blacklist / open mode.
- 🤖 **Run many bots in one process** — `asyncio.gather` over a `<name>: BotConfig` map.

## 🚀 Quick start

```bash
git clone <repo-url> agent-bot && cd agent-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

claude login                                       # for agent_provider="claude"
# Codex backend uses openai-codex / Codex auth when agent_provider="codex"
# PI backend uses `pi --mode rpc` when agent_provider="pi"
cp src/config/config.example.yaml src/config/config.yaml
# edit: gateway.telegram_bot_token + gateway.access.allowed_chat_ids + agent.provider

python -m src.bot                                  # or: agent-bot
```

Don't know your `chat_id`? Leave `allowed_chat_ids: []`, message the bot — the refusal contains it. Add it back, restart.

## 🧭 Bot commands

`/start` `/new` `/context` `/plan` `/cancel` `/stop` `/mode` `/model` `/mcp` `/info` `/whoami` `/help` + any user-defined slash commands from `commands_dir`.

Drop `*.md` files into `commands_dir` to expose reusable workflows (`/recall`, `/today`, `/standup`, …) with `$ARGUMENTS` substitution. See [COMMANDS.md](COMMANDS.md).

## 📚 Documentation

| File | What's inside |
|---|---|
| [INSTALLATION.md](INSTALLATION.md) | Step-by-step setup, multi-bot config, troubleshooting. |
| [CONFIG.md](CONFIG.md) | Every `BotConfig` field — type, default, validation, env override. |
| [COMMANDS.md](COMMANDS.md) | Custom slash commands: frontmatter, `$ARGUMENTS`, examples. |
| [AGENTS.md](AGENTS.md) | Architecture reference for LLM agents working in this repo. |
| [CLAUDE.md](CLAUDE.md) | Short orientation pinned to the repo for Claude Code. |

## 🛠 Tech

[aiogram 3](https://docs.aiogram.dev/) · [claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python) · [openai-codex](https://developers.openai.com/codex/sdk) · [pydantic 2](https://docs.pydantic.dev/) · [markdown-it-py](https://pypi.org/project/markdown-it-py/) · Groq Whisper.

Full deps + dev tooling (`ruff`, `mypy`, `pyright`, `bandit`, `pip-audit`, `pytest`) declared in [pyproject.toml](pyproject.toml).

## 📄 License

[MIT](LICENSE) — © 2026 Eugene Myazin.
