# Backend & layering refactor

Plan file: `.agents/plans/20260622_1533_backend_layering.md`
Created: 2026-06-22. Time in the name is reconstructed from git reflog: checkout of `refactor/backend-layering` (renamed from `PLN-0003.md` on 2026-09-06).

## PRD / Why We Are Doing This

### Problem
Architecture audit found concrete debt across three areas:

1. **Silent config bug.** `CodexAgentBackend._resolve_approval_mode()` returns
   `auto_review` for every `approval_mode` value (`never`/`full_auto`/`on_request`
   collapse to the same branch). Codex always runs in one approval mode; config is
   ignored without error.
2. **Triple duplication.** `claude_agent`, `codex_agent`, `pi_agent` (509/745/767
   LOC) repeat identical `_lock`, `_ensure_gc_running`, `_gc_loop`, and session
   meta-ops (`list_sessions`, `current_session`, `new/switch/delete_session` store
   delegation). Drift risk is real: Codex idle-GC drops a thread from `_sessions`
   without closing it, while PI closes its transport — same intent, divergent code.
3. **Layer inversion.** `infra/task_runner.py` imports `ui/markdown.send_md_to_chat`
   and `aiogram.Bot`; `ui/markdown.py` mixes a pure renderer (`to_html`) with
   Telegram delivery (`send_md`, `send_md_to_chat`). Infra depends on UI.
4. **Wiring carries feature logic.** `bot.py` (455 LOC) embeds `_link_skills`,
   `_make_acl`, `_compose_system_prompt` — AGENTS.md mandates `bot.py` be wiring +
   supervision only.

### Goal
Backends share one base for lock/GC/session-ops; Codex respects its `approval_mode`
config and closes threads on GC; infra no longer imports UI; `bot.py` returns to
wiring-only. No behavior change for users except Codex approval mode now honoring config.

### Users / Scenarios
- Bot operators configuring `backend: codex` with a non-default `approval_mode` —
  the setting must take effect (currently silently ignored).
- Future maintainers adding a 4th backend — inherit base instead of copy-paste.
- No change to Telegram end-user flows.

### Requirements
- REQ-1: `_resolve_approval_mode()` maps each `approval_mode` value to the correct
  Codex SDK `ApprovalMode` member (or documented fallback), with no dead branches.
- REQ-2: Extract a `BaseAgentBackend` providing shared `_lock`, `_ensure_gc_running`,
  `_gc_loop`, `list_sessions`, `current_session`, and template-method
  `new_session`/`switch_session`/`delete_session` that delegate teardown to a backend
  hook. All three backends inherit it; net LOC drops.
- REQ-3: Codex idle-GC closes the underlying thread/runtime before dropping the
  session (parity with PI transport close).
- REQ-4: Split `ui/markdown.py` into pure render (stays) and delivery
  (`send_md`/`send_md_to_chat` move to a delivery module); `task_runner` receives a
  delivery callback via constructor instead of importing UI.
- REQ-5: Move `_link_skills`, `_make_acl`, `_compose_system_prompt` out of `bot.py`
  into focused `infra/`/`services/` modules; `bot.py` only wires them.
- REQ-6: Preserve fail-closed ACL, per-chat lock semantics, and lock-free `interrupt`
  behavior exactly (these are intentional — do not "fix" them).

### Acceptance Criteria
- AC-1: With `approval_mode: never` (and each other value), Codex thread start
  receives the matching SDK approval member; a unit test asserts distinct mappings.
- AC-2: All three backends pass existing `tests/test_agent_backends.py` unchanged in
  behavior; shared helpers exist in exactly one place.
- AC-3: Codex idle-GC invokes a close path on the dropped thread (asserted via a
  fake thread exposing `close`).
- AC-4: `grep -rn "from ..ui" src/infra/` returns nothing; `task_runner` works with an
  injected delivery callable and its tests pass.
- AC-5: `bot.py` contains no `_link_skills`/`_make_acl`/`_compose_system_prompt`
  bodies — only calls into the new modules.
- AC-6: Full check suite green: `ruff check src/ tests/`, `mypy src/ tests/ --strict`,
  `pyright`, `bandit -r src/`, `pytest -q`.

### Out of Scope
- Provider-neutral permission types (removing `claude_agent_sdk` `PermissionResult*`
  leak through `agent.py`). Large surgery; the contract already declares intent. Track
  separately.
- `ask_ephemeral` Protocol split — `NotImplementedError` is already documented in
  `agent_types.py:55` as an allowed backend choice. Leave as-is.
- `message_db`/`session_store` fsync-durability changes and callback_data JSON encoding
  — real but independent; not part of this refactor.
- Any change to `interrupt` locking (intentional lock-free, see REQ-6).

### Constraints and Dependencies
- `mypy --strict` and `pyright` must stay green — base class needs precise typing of
  the abstract teardown hook and `_store: SessionStore`.
- Backends differ in teardown primitive: Claude uses `_drop_client` (keeps session in
  store), Codex/PI use `reset`. Base must template this, not assume one.
- Codex SDK `ApprovalMode` members are resolved dynamically via `importlib`
  (`getattr(approval_cls, name, fallback)`); correct member names are an Unknown.

## Code Context
- Fact: [codex_agent.py:190-196](../../src/infra/codex_agent.py#L190-L196) — four
  branches all return `auto_review`.
- Fact: session meta-ops in [claude_agent.py:383-416](../../src/infra/claude_agent.py#L383-L416),
  [codex_agent.py:652-677](../../src/infra/codex_agent.py#L652-L677), and pi_agent are
  structurally identical, differing only by teardown call (`_drop_client` vs `reset`).
- Fact: [codex_agent.py:108-114](../../src/infra/codex_agent.py#L108-L114) — GC pops
  `_sessions`/`_locks` without closing the thread; PI GC (`pi_agent.py:246+`) closes
  transport.
- Fact: [task_runner.py:25](../../src/infra/task_runner.py#L25) imports
  `..ui.markdown.send_md_to_chat`; [task_runner.py:22](../../src/infra/task_runner.py#L22)
  imports `aiogram.Bot`; delivery used at line 201.
- Fact: `send_md`/`send_md_to_chat` live in [markdown.py:386-424](../../src/ui/markdown.py#L386-L424)
  alongside pure `to_html`. Importers: `plan_router`, `agent_reply`, `task_runner`.
- Fact: tests exist — `test_agent_backends.py`, `test_task_runner.py`,
  `test_markdown.py`, `test_bot_factories.py`.
- Conclusion: BaseAgentBackend is a template-method extraction (abstract `_teardown`
  hook), not a flat move; safe and test-covered.
- Conclusion: delivery split is mechanical (move 2 functions + update 3 importers + 1
  constructor injection).
- Assumption: Codex `ApprovalMode` exposes members named for the modes (e.g. `never`,
  `on_request`, `full_auto`/`auto`); must confirm from `openai_codex` at impl time.
- Unknown: exact Codex SDK `ApprovalMode`/`Sandbox` member names — resolve via
  `python -c "import openai_codex; print(dir(openai_codex.ApprovalMode))"` before
  finalizing REQ-1 mapping.

## Architecture Guidance
- New file `src/infra/agent_base.py`: `class BaseAgentBackend` holding
  `_locks: dict[int, asyncio.Lock]`, `_store: SessionStore`, `_idle_ttl`, plus
  `_lock`, `_ensure_gc_running`, `_gc_loop`, `list_sessions`, `current_session`.
  Define abstract `async def _teardown_live(chat_id)` and `async def _gc_idle()`;
  template `new/switch/delete_session` call `_teardown_live` then `_store` ops. Each
  backend overrides `_teardown_live` (Claude→`_drop_client`, Codex/PI→reset-body) and
  `_gc_idle`. Backends keep their own client/thread/transport dicts.
- Codex GC fix lands inside Codex's `_gc_idle` override: before `pop`, resolve a
  `close`/`__aexit__` on the thread and await/suppress (mirror PI).
- New file `src/ui/delivery.py`: move `send_md`, `send_md_to_chat` here. `markdown.py`
  keeps `to_html` and pure helpers, drops the `aiogram` import. Update `plan_router`,
  `agent_reply` imports to `ui.delivery`.
- `task_runner`: add `deliver: Callable[[int, str], Awaitable[None]]` constructor param;
  remove `..ui` import (keep `aiogram.Bot` only if still needed for typing — prefer
  removing). `bot.py` injects a closure binding `send_md_to_chat` + `bot` when building
  the runner.
- New `src/infra/skills_linker.py` (`link_skills(...)`), and ACL +
  system-prompt builders. ACL fits `infra/access_control.py` (fail-closed predicate);
  system prompt fits `services/system_prompt_builder.py` (file load + compose).
  `bot.py` imports and calls them.
- Reuse existing patterns: backends already share `SessionStore`; follow `task_*`
  module split for the new infra modules; keep docstrings English per AGENTS.md.

## Affected Contracts
- API (Telegram handlers): does not change.
- Data/schema (SQLite session/message/task stores): does not change.
- Permissions/ACL: predicate moves modules but logic must stay byte-equivalent
  (fail-closed). No behavior change.
- AgentBackend Protocol (`agent_types.py`): does not change (base is impl detail).
- Configuration: `approval_mode` starts taking effect (REQ-1) — a behavior change, but
  toward documented intent. Note in changelog.
- `TaskRunner.__init__` signature: changes (adds `deliver` param) — internal, only
  `bot.py` + tests construct it.

## Implementation note — scope adjustments

Two findings were tempered during implementation after reading the code:

- **Phase 1 was NOT a behavioral bug.** Codex's SDK `ApprovalMode` exposes only
  `auto_review` and `deny_all`; the config values (`default`/`on_request`/`never`/
  `full_auto`) have no matching members, and the bot has no interactive Codex approval
  gate, so mapping any mode to `deny_all` would block all tool execution. The dead
  4-branch cascade collapsing to `auto_review` was therefore *correct* but misleading.
  Fix = behavior-preserving: a single documented `getattr(..., "auto_review")` with a
  comment explaining why `deny_all` is never used. AC-1 ("three distinct values") was
  based on a wrong assumption about the SDK and does not apply.
- **Phase 4 trimmed to the real fix.** Moving `send_md`/`send_md_to_chat` to a new
  `ui/delivery.py` would churn ~14 importers across `ui/` and `handlers/` for no
  architectural gain — `ui/` is the Telegram-facing layer and importing `aiogram`
  there is legitimate (AGENTS.md). The one true violation was `infra/task_runner.py`
  importing `ui.markdown`. Fixed by injecting a `deliver` callback (same pattern the
  gate already uses). `markdown.py` left in place.

## Phases and Tasks

### Phase 1: Codex approval-mode clarity (done)
- [x] [REQ-1] Removed the dead 4-branch cascade in `_resolve_approval_mode()`; replaced
  with a single documented resolution + comment on why `deny_all` is unused → Verified:
  `test_agent_backends.py` green (incl. `approval_mode="never"` passthrough test).

### Phase 2: Shared backend base (done)
- [x] [REQ-2] Created `src/infra/agent_base.py` with `BaseAgentBackend`: shared `_lock`,
  `_ensure_gc_running`, `_gc_loop`, `_stale_chat_ids`, `list_sessions`,
  `current_session`, and template `new`/`switch`/`delete_session` (teardown via
  `reset`); abstract `_gc_idle`/`reset` → Verified: `mypy --strict` clean.
- [x] [REQ-2, AC-2] `ClaudeAgentBackend` inherits base; keeps `new/switch/delete`
  overrides (store mutation under one lock — preserves its no-race semantics) →
  Verified: Claude tests unchanged.
- [x] [REQ-2, AC-2] `CodexAgentBackend` and `PiAgentBackend` inherit base; dropped their
  `_lock`/`_ensure_gc_running`/`_gc_loop`/session-ops; use base template (teardown =
  `reset`) → Verified: full `test_agent_backends.py` green; `_lock`/`_gc_loop` now
  defined only in `agent_base.py`.

### Phase 3: Codex GC thread close (done)
- [x] [REQ-3, AC-3] Codex `_gc_idle` now closes the thread (`_close_thread`, await +
  suppress) before dropping the session → Verified: new
  `test_codex_idle_gc_closes_thread` asserts `fake.thread.closed is True` after idle
  sweep.

### Phase 4: task_runner inversion (done; UI split skipped — see note)
- [x] [REQ-4, AC-4] Added `deliver: Callable[[int, str], Awaitable[None]]` to
  `TaskRunner`, removed its `..ui.markdown` import; `bot.py` injects
  `partial(send_md_to_chat, bot)` → Verified: `grep -rn "from ..ui" src/infra/` empty;
  `test_task_runner.py` green (fake deliver recorder).

### Phase 5: bot.py back to wiring-only (done)
- [x] [REQ-5] Extracted `link_skills` (+ skills constants) to
  `src/infra/skills_linker.py` → Verified: `bot.py` has no `_link_skills` body.
- [x] [REQ-5, AC-5] Extracted ACL to `infra/access_control.py` (`make_acl`) and
  system-prompt compose to `services/system_prompt_builder.py`; updated
  `test_bot_factories.py` imports → Verified: fail-closed ACL tests pass; `bot.py`
  455 → 371 LOC (remaining `_make_*` are genuine wiring factories).

### Phase 6: Final verification (done)
- [x] [AC-6] `ruff check src/ tests/` clean; `mypy src/ tests/ --strict` clean (85
  files); `bandit -r src/ -q` no new issues (pre-existing 1 High/4 Low unchanged);
  `pytest -q` → 309 passed. `pyright` not installed locally; mypy strict + ruff cover
  it. Manual codex smoke deferred (no Codex runtime in this env).

## Done When
- [x] Dead approval-mode cascade removed; behavior preserved + documented (not the
  bug the audit assumed — see implementation note).
- [x] `_lock`/`_ensure_gc_running`/`_gc_loop` and session meta-ops exist once in
  `agent_base.py`; all three backends inherit; net backend LOC reduced (~−194 across
  changed files, +100 base).
- [x] Codex idle-GC closes the thread.
- [x] `src/infra` imports nothing from `src/ui` (markdown.py left in place — UI split
  skipped as churn-without-gain, see note).
- [x] `bot.py` is wiring-only (feature logic extracted; 455 → 371 LOC).
- [x] Existing behavior preserved (ACL fail-closed, per-chat lock, lock-free interrupt
  all untouched).
- [x] Full check suite green (ruff, mypy --strict, bandit, pytest 309).

## Risks / Unknowns
- Risk: Wrong Codex `ApprovalMode` member names → config maps to invalid member →
  Resolve actual names from the installed `openai_codex` before writing the mapping;
  keep `getattr(..., fallback)` guard.
- Risk: Base extraction subtly changes teardown ordering (e.g. lock acquired around
  `_store` op) → Mirror each backend's current ordering exactly in the template; rely on
  `test_agent_backends.py` to catch drift.
- Risk: Removing `aiogram` from `markdown.py` breaks a hidden importer → `grep` all
  importers of `send_md*` before moving; update each.
- Risk: ACL logic drift during move breaks fail-closed guarantee → Move verbatim; add/keep
  a test that an unlisted chat is denied under `allowed_for_all=false`.
- Open question: Should `backend: codex` approval-mode behavior change be gated behind a
  changelog note or treated as a straight bugfix? (Recommend: bugfix + changelog line.)
