# In-code documentation audit & cleanup

Plan file: `.agents/plans/20260622_1558_in_code_docs_audit.md`
Created: 2026-06-22. Time in the name is reconstructed from git reflog: `reset` on `refactor/backend-layering` between the previous and this plan (renamed from `PLN-0004.md` on 2026-09-06).

## PRD / Why We Are Doing This

### Problem
A docstring/comment audit (and the 20260622_1533_backend_layering refactor) surfaced three classes of
documentation debt:

1. **Actively wrong docs** — docstrings/notes that contradict the code and mislead
   readers. These are the worst: a reader trusts them.
   - `claude_agent.generate_title` docstring says *"One-shot **Haiku** call"*, but the
     code runs `self._initial_model` (the bot's configured model) and a comment
     elsewhere explicitly says *"never assume Haiku is available."*
   - `ui/markdown.py` module docstring says *"Pure helpers — no I/O state"*, yet the
     module ships `send_md`/`send_md_to_chat` (aiogram I/O). 20260622_1533_backend_layering deliberately left
     these here, so the docstring must be corrected, not the code.
   - `docs/ARCHITECTURE.md:168,209` reference `_make_acl(cfg, glog)` / `bot._make_acl` —
     20260622_1533_backend_layering moved this to `infra/access_control.py::make_acl`. The doc now names a
     symbol that no longer exists in `bot.py`.
2. **Undocumented contracts** — the spine of the system has no contract docs.
   `AgentBackend` Protocol (`agent_types.py`) has no per-method docstrings: `ask` vs
   `ask_stream` semantics, thread-safety, what each returns, which raise. `BotContext`
   (the aggregate every handler consumes) has no class docstring.
3. **Undocumented complex public surfaces** — non-trivial entry points with subtle
   control flow and no docstring: the permission gate dispatcher (`gate.can_use_tool`),
   the interaction `handle()` methods (`permission_prompt`, `plan_mode`,
   `ask_user_question`, `push_notification`), `agent_reply.reply_with_agent`,
   `handlers/selectors._dispatch_choice_cb`, and `infra/streaming.py` (no module
   docstring at all).

### Goal
No docstring or project-doc statement contradicts the code; the `AgentBackend` contract
and `BotContext` are documented; the handful of genuinely complex public entry points
get focused docstrings explaining *what/returns/raises/why*. A short docstring
convention is added to AGENTS.md so this does not regress.

### Users / Scenarios
- Contributors and LLM agents reading the code to make changes — today they hit
  docstrings that lie (Haiku, "pure helpers") or contracts with zero documentation.
- No runtime behavior change. This is documentation only.

### Requirements
- REQ-1: Every docstring/comment/project-doc statement identified as contradicting the
  code is corrected to match the code (code wins, per AGENTS.md).
- REQ-2: The `AgentBackend` Protocol has per-method docstrings covering return value,
  side effects, concurrency, and which methods may raise / return `NotImplementedError`.
- REQ-3: `BotContext` has a class docstring; optional fields document when they are
  `None`.
- REQ-4: The complex public entry points listed above get focused docstrings.
- REQ-5: AGENTS.md gains a brief docstring convention (module docstring required;
  public/contract symbols documented; document *why* for non-obvious control flow;
  do not docstring trivial one-line handlers — avoid noise).
- REQ-6: No behavior change; the full check suite stays green.

### Acceptance Criteria
- AC-1: `grep` for the wrong terms confirms removal: `generate_title` docstring no longer
  says "Haiku"; `markdown.py` docstring no longer claims "no I/O state"; ARCHITECTURE.md
  no longer references `_make_acl`/`bot._make_acl`.
- AC-2: Every method on the `AgentBackend` Protocol has a docstring; a reviewer can tell
  `ask` from `ask_stream` and knows `ask_ephemeral` may raise `NotImplementedError`.
- AC-3: `BotContext` and the entry points in REQ-4 have docstrings that match their
  actual code paths (verified by re-reading each against its implementation).
- AC-4: `ruff check`, `mypy --strict`, `pytest -q` all still green (docs-only change).
- AC-5: AGENTS.md documents the docstring convention.

### Out of Scope
- Mass docstring-filling of trivial handlers/helpers (`start`, `cancel`, `_mode_label`,
  one-line callbacks). AGENTS.md does not require it and it adds noise.
- Adding a docstring linter (ruff `D`/pydocstyle) — not currently enabled; enabling it
  would flood the repo with findings. Could be a separate future task.
- Any code change beyond docstrings/comments and the AGENTS.md/ARCHITECTURE.md edits.
- **FALSE flags from the audit — explicitly do NOT touch:**
  - `agent_base.py` module docstring is accurate (written in 20260622_1533_backend_layering); leave it.
  - `task_scheduler._grace_decision` docstring is accurate — it summarizes the policy;
    the `120s`/`period/2` constants correctly live in `compute_grace_seconds`. Leave it.

### Constraints and Dependencies
- AGENTS.md: docstrings/comments in English; "module-level docstrings should stay
  accurate"; "if guidance and code disagree, the code wins."
- ruff has no `D`/pydocstyle rules, so docstrings are unenforced — correctness is by
  review, not tooling. Each task verifies by re-reading against the code.

## Code Context
- Fact: [claude_agent.py:385](../../src/infra/claude_agent.py#L385) docstring "One-shot
  Haiku call"; [claude_agent.py:251-253](../../src/infra/claude_agent.py#L251-L253) and
  the `generate_title` body use `self._initial_model`, not Haiku.
- Fact: [markdown.py:1-5](../../src/ui/markdown.py#L1-L5) "Pure helpers — no I/O state";
  `send_md`/`send_md_to_chat` (aiogram I/O) live in the same module (left there by
  20260622_1533_backend_layering).
- Fact: [ARCHITECTURE.md:168](../../docs/ARCHITECTURE.md) and
  [:209](../../docs/ARCHITECTURE.md) name `_make_acl`/`bot._make_acl`; the symbol is now
  `infra/access_control.py::make_acl`.
- Fact: [agent_types.py:36-96](../../src/infra/agent_types.py#L36-L96) — `AgentBackend`
  Protocol methods have no docstrings; only `ask_ephemeral` has one.
- Fact: `infra/streaming.py` starts with `import` (no module docstring).
- Fact: `handlers/context.py` `BotContext` dataclass has no class docstring.
- Conclusion: changes are docstring/comment/markdown edits only — zero runtime risk; the
  suite is a regression guard, not a behavior check.
- Assumption: No significant assumptions; each fix is verifiable by reading the code.
- Unknown: ARCHITECTURE.md may carry *other* 20260622_1533_backend_layering staleness beyond ACL (e.g. backend
  base class, task_runner→ui). Resolve by re-reading ARCHITECTURE.md end-to-end in
  Phase 1, not just the two known lines.

## Architecture Guidance
- Fix-in-place: correct the wrong docstrings where they are; do not move code to satisfy
  a docstring (20260622_1533_backend_layering already decided `send_md` stays in `markdown.py`).
- For `markdown.py`: reword the module docstring to "Markdown → Telegram HTML conversion
  plus chunked Rich-Message senders" — describe what's actually there (pure converters +
  a thin send layer), rather than claiming purity.
- For the Protocol: keep per-method docstrings terse (1-3 lines). Document the
  `ask`/`ask_stream` split, that turns serialize per-chat, that `interrupt` is
  intentionally lock-free, and that `ask_ephemeral` may raise `NotImplementedError`
  (Codex/PI). These facts are established in AGENTS.md "Sessions, Streaming, Logs".
- For interaction `handle()`/`on_callback()` methods: document the return contract
  (e.g. `PushNotification.handle` returns a *Deny-shaped* result by design) and where
  feedback/state is stored — the subtle parts, not line-by-line narration.
- Prefer extending AGENTS.md's existing "Contributor Rules" with the docstring
  convention rather than a new doc file.

## Affected Contracts
- API / Data / Permissions / Events / Config: do not change (documentation only).
- Public symbol names: do not change (`make_acl` etc. already renamed in 20260622_1533_backend_layering; this
  plan only updates docs that still reference old names).

## Phases and Tasks

### Phase 1: Correct docs that contradict the code (done)
- [x] [REQ-1, AC-1] `generate_title` docstring now says it runs on the bot's configured
  model (not "Haiku call") → Verified: docstring matches body; remaining "Haiku" hits
  are the `CLAUDE_MODELS` label and the "never assume Haiku" notes.
- [x] [REQ-1, AC-1] `ui/markdown.py` module docstring rewritten (pure converters + thin
  send layer); "no I/O state" removed → Verified: `grep "no I/O state"` empty.
- [x] [REQ-1, AC-1, Unknown] Re-read `docs/ARCHITECTURE.md`; fixed `_make_acl` /
  `bot._make_acl` → `infra/access_control.py::make_acl`, added the `BaseAgentBackend`
  note to §3/§11, referenced `system_prompt_builder`, and fixed stale `sendMessageDraft`
  → `sendRichMessageDraft` (§12) → Verified: `grep -nE "_make_acl|bot\._" ARCHITECTURE.md`
  empty.
- [x] [REQ-1] `agent_types.py` module docstring now lists Claude / Codex / PI.

### Phase 2: Document the AgentBackend contract (done)
- [x] [REQ-2, AC-2] Added a class docstring (concurrency/lock-free-interrupt note) plus a
  per-method docstring to every `AgentBackend` Protocol method → Verified: re-read
  against claude/codex/pi; `mypy --strict` green.

### Phase 3: Document key public surfaces (done)
- [x] [REQ-3, AC-3] `BotContext` class docstring added with the None-conditions for
  `transcriber`/`uploads`/`tasks`/`task_service`.
- [x] [REQ-4, AC-3] `infra/streaming.py` module docstring added (DraftStreamer already
  had a class docstring).
- [x] [REQ-4, AC-3] Documented `gate` (class + `can_use_tool`) and the flow entry points:
  `permission_prompt.handle/on_callback`, `plan_mode.handle/on_callback`,
  `ask_user_question.handle/_ask_one/on_callback`, `push_notification.handle` (Deny-shaped
  return explained).
- [x] [REQ-4, AC-3] Documented `agent_reply.reply_with_agent` (pipeline, failure modes,
  background titling) and `selectors._dispatch_choice_cb` (anti-forgery ownership check).

### Phase 4: Lock in the convention + verify (done)
- [x] [REQ-5, AC-5] Added a docstring-convention bullet to AGENTS.md "Contributor Rules".
- [x] [REQ-6, AC-4] Full suite green: `ruff` clean, `mypy --strict` clean (85 files),
  `pytest -q` → 309 passed. Docs-only; no behavior change.

## Done When
- [x] No docstring/comment/project-doc statement contradicts the code (Haiku, "no I/O
  state", `_make_acl`, `sendMessageDraft` all fixed; ARCHITECTURE.md re-read).
- [x] `AgentBackend` Protocol fully documented; `BotContext` documented.
- [x] The listed complex entry points and `streaming.py` have accurate docstrings.
- [x] AGENTS.md documents the docstring convention.
- [x] `ruff`, `mypy --strict`, `pytest` green; no runtime behavior changed.

## Risks / Unknowns
- Risk: A "fix" docstring introduces a *new* inaccuracy → Verify each by re-reading the
  implementation it documents, not from memory.
- Risk: Scope creep into mass docstring-spam → Stick to the Out-of-Scope boundary;
  trivial handlers stay undocumented on purpose.
- Risk: ARCHITECTURE.md has staleness beyond what the audit caught → Phase 1 re-reads the
  whole file rather than patching only the two known lines.
- Open question: Enable ruff `D`/pydocstyle later to enforce this? Deferred — would flag
  the whole repo; out of scope here.
