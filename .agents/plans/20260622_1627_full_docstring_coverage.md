# Full docstring coverage + pydocstyle enforcement

Plan file: `.agents/plans/20260622_1627_full_docstring_coverage.md`
Created: 2026-06-22. Time in the name is reconstructed from git reflog: `reset` on `refactor/backend-layering` between the previous and this plan (renamed from `PLN-0005.md` on 2026-09-06).

## PRD / Why We Are Doing This

### Problem
20260622_1558_in_code_docs_audit documented the contracts and the wrong/missing docstrings, but adopted a
"document the important, skip trivial one-liners" policy and explicitly deferred enabling
a docstring linter. The decision now is to flip that policy: **document everything** and
enforce it with ruff's pydocstyle (`D`) rules, so coverage cannot regress and the style
is uniform across the repo.

This is a deliberate policy change. The 20260622_1558_in_code_docs_audit AGENTS.md rule ("skip docstrings on
trivial one-line handlers") now contradicts the goal and must be updated.

### Goal
`ruff check src/` is green with the `D` (pydocstyle) rule set enabled under a single
convention; every public symbol (module, class, function, method, `__init__`, magic
method) has a docstring; private `_` helpers are also documented (manual — the linter
does not gate private symbols); existing docstrings are reformatted to satisfy the
chosen convention. AGENTS.md reflects the new "document everything" policy.

### Users / Scenarios
- Contributors and LLM agents reading any module — every symbol carries intent, and CI
  (`ruff check`) blocks undocumented additions.
- No runtime behavior change. Docstrings, comments, and tooling config only.

### Requirements
- REQ-1: Enable ruff `D` in `pyproject.toml` with one `pydocstyle.convention` (pep257 —
  closest to the existing style), so mutually-exclusive rules (D203/D211, D212/D213) do
  not fight each other.
- REQ-2: Resolve test-file policy explicitly (see Open question) and encode it in
  `per-file-ignores`; keep the existing `i18n/*.json = ["ALL"]` ignore.
- REQ-3: Add docstrings to every **public** symbol the linter flags: modules (D100),
  packages/`__init__.py` (D104), public classes (D101), public methods (D102), public
  functions (D103), magic methods (D105), nested classes (D106), `__init__` (D107).
- REQ-4: Fix every docstring-**format** violation the linter flags (D2xx blank lines,
  D3xx quotes/backslashes, D4xx content: imperative mood D401, ends-with-period
  D400/D415, single summary line, etc.).
- REQ-5: Manually add docstrings to **private** `_` functions/methods (not linter-gated)
  so coverage is genuinely complete, per the "document everything" decision.
- REQ-6: Update AGENTS.md: replace the "skip trivial one-liners" rule with the new
  "document every symbol; ruff `D` enforces it" policy.
- REQ-7: No behavior change; `ruff check`, `mypy --strict`, `pytest` all green.

### Acceptance Criteria
- AC-1: `ruff check src/` passes with `D` in the active select set (no `D` violations).
- AC-2: Every `src/*.py` file starts with a module docstring; every public class/
  function/method has one (verified by AC-1 plus spot grep).
- AC-3: No private `_def` in `src/` lacks a docstring (verified by a grep that finds a
  `def _name(...)` whose next non-blank line is not a docstring → empty result, allowing
  documented exceptions like trivial property getters if explicitly chosen).
- AC-4: Docstrings are accurate — each added/edited docstring matches the code it
  documents (reviewed, not auto-generated boilerplate that restates the signature).
- AC-5: `ruff check src/ tests/`, `mypy src/ tests/ --strict`, `pytest -q` all green.
- AC-6: AGENTS.md documents the "document everything + ruff D" policy.

### Out of Scope
- Renaming/refactoring code to satisfy docstrings — docs and config only.
- Enabling other new ruff rule families (only `D` this round).
- Rewriting `docs/*.md` prose (covered by 20260622_1558_in_code_docs_audit); this plan is in-code docstrings +
  tooling + AGENTS.md.

### Constraints and Dependencies
- AGENTS.md: docstrings in English; code wins over docs.
- ruff 0.15.x is installed; `D` + `convention` is supported.
- Scope baseline (grep): 56 `src/*.py` files (4 missing a module docstring), 50 classes,
  464 `def`s — 255 public, 209 private. The linter gates the public surface + modules +
  classes; the 209 private defs are a manual pass (REQ-5).
- pep257 convention auto-disables the conflicting/over-strict rules; D401 (imperative
  mood) stays active and will require rewording some existing docstrings ("Sends…" →
  "Send…").
- Quality risk: a forced docstring on a trivial getter tends to restate the signature.
  Accept terse one-liners there; the value is uniformity + a regression gate, per the
  explicit decision to document everything.

## Code Context
- Fact: `pyproject.toml` `[tool.ruff.lint] select` lists `E,F,W,I,B,UP,ASYNC,S,RUF,SIM,
  PTH,ANN,RET,ARG,TC` and ignores a small set; no `D` yet; `i18n/*.json = ["ALL"]`.
- Fact: only 4 `src` modules lack a module docstring; most modules already have one →
  D100 churn is small.
- Fact: many existing docstrings are descriptive third-person ("Builds…", "Sends…",
  "Runs one task…") → D401 will flag them.
- Fact: ruff pydocstyle does not flag private (`_`) symbols → REQ-5 is manual and AC-3
  needs its own grep gate, not the linter.
- Conclusion: enforceable work (REQ-3/4) is bounded and mechanical; the larger volume is
  REQ-5 (privates) and D401 rewording.
- Assumption: pep257 convention matches the house style closely enough that D2xx/D3xx
  fixes are minor. Confirm by reading the first full `ruff check --select D` report in
  Phase 1.
- Unknown: exact violation count per D code until the linter runs (allowed in Stage 2,
  not during planning). Phase 1 captures the baseline report and drives the rest.

## Architecture Guidance
- Config lives in `pyproject.toml` under `[tool.ruff.lint]` (`select`) and a new
  `[tool.ruff.lint.pydocstyle]` table (`convention = "pep257"`).
- Drive the work off the real report: `ruff check src/ --select D --statistics` gives
  per-code counts; fix highest-count codes first (likely D103/D102/D101, then D401).
- Use `ruff check --select D --fix` only for the few auto-fixable format codes; docstring
  *content* (D1xx, D401 wording) must be written by hand against the code.
- Keep docstrings terse and accurate (imperative summary line). For genuinely trivial
  symbols (one-line getters, `register(dp)` wrappers) a single imperative line is enough.
- Do private helpers (REQ-5) per-module alongside their public siblings so context is
  fresh; verify with the AC-3 grep, not the linter.
- Update the AGENTS.md "Contributor Rules" bullet added in 20260622_1558_in_code_docs_audit rather than adding a
  competing rule.

## Affected Contracts
- API / Data / Permissions / Events: do not change.
- Configuration: `pyproject.toml` ruff lint config changes (adds `D` + convention +
  per-file-ignores). Tooling-only.
- Public symbol names/signatures: do not change.

## Decision (overrides Open question)
User chose **maximum**: enforce `D` on `tests/` too (no `per-file-ignores` for tests) and
document private/nested symbols everywhere. Only `i18n/*.json = ["ALL"]` stays ignored.

## Phases and Tasks

### Phase 1: Enable D and capture the baseline (done)
- [x] [REQ-1, REQ-2] Added `"D"` to select + `[tool.ruff.lint.pydocstyle] convention =
  "pep257"`; tests NOT exempted (max scope); kept `i18n/*.json = ["ALL"]`.
- [x] [REQ-1] Baseline: 536 D violations — D103×347, D102×100, D101×22, D107×21,
  D205×15, D209×9, D401×8, D100×6, D104×5, D105×1, D301×1, D400×1.

### Phase 2-3: Public coverage + format (done, parallelized)
- [x] [REQ-3, REQ-4, AC-1, AC-2] Fanned out 7 agents over disjoint file sets (tests ×3,
  infra backends, infra data/tasks/interactions, ui, handlers/services/config). Each
  added module/class/public/`__init__`/magic docstrings and fixed D2xx/D3xx/D401 to reach
  `ruff --select D` clean on its files → Verified: `ruff check src/ tests/ --select D`
  and full `ruff check src/ tests/` both "All checks passed!" (536 → 0).

### Phase 4: Private/nested coverage (done, AST-gated)
- [x] [REQ-5, AC-3] ruff leaves 87 private/nested/dunder symbols ungated; a second wave
  of 2 agents documented them (codex/pi/claude privates; config/bot/misc privates +
  closures) → Verified by AST gate: `ast.get_docstring` is non-None for every module,
  class, function, and method in `src/` → **UNDOCUMENTED: 0**.

### Phase 5: Policy + final verification (done)
- [x] [REQ-6, AC-6] AGENTS.md "Contributor Rules" docstring bullet rewritten: "every
  symbol documented; ruff `D` enforces public; private/nested still required" (dropped
  the old "skip trivial one-liners").
- [x] [REQ-7, AC-5] Full suite: `ruff check src/ tests/` clean, `mypy --strict` clean (85
  files), `pytest -q` → 309 passed. Spot-read codex/pi private docstrings for accuracy
  (AC-4) — accurate, not signature-restating filler.

## Done When
- [x] `ruff check src/ tests/` green with `D` enabled (no docstring violations).
- [x] Every symbol in `src/` (public + private + nested + dunder) has a docstring
  (AST gate = 0); tests fully `D`-clean.
- [x] AGENTS.md documents the "document everything + ruff D" policy.
- [x] `mypy --strict` and `pytest` green; no runtime behavior changed.

## Risks / Unknowns
- Risk: Forced docstrings degrade into signature-restating noise → Keep them terse and
  imperative; for trivial symbols one honest line is acceptable (the gate + uniformity is
  the point here).
- Risk: D401 (imperative mood) has false positives → Reword where sensible; per-line
  `# noqa: D401` only as a last resort, documented.
- Risk: A reworded docstring introduces a new inaccuracy → Write each against the code it
  documents; spot-review (AC-4).
- Risk: Large diff is hard to review → Do it phase by phase (D-code by D-code), each
  phase independently `ruff`-verifiable.
- Open question: Enforce `D` on `tests/` too? Recommendation: **ignore `D` in `tests/`**
  (`per-file-ignores: "tests/*" = ["D"]`) — pytest test names are self-documenting and
  forcing docstrings on every `test_*` is pure noise. Confirm before Phase 1; if "yes,
  tests too", drop that ignore and extend Phases 2-4 to `tests/`.
