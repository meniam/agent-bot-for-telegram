# Learning Rules

How agents record facts found by experience. A learning is not a choice but
an observation: expected one thing, got another, verified it. It has no
alternatives and no cost, so it is not a decision (see `decisions.md`).

## Location and Naming

- Learnings live in `.agents/learnings/`: record files, `_TOC.md`,
  `AGENTS.md` / `CLAUDE.md`. Nothing else.
- File name as for plans and decisions: `YYYYMMDD_HHMM_short_statement.md`,
  latin `snake_case`. Example: `20260901_1130_env_override_before_template_check`.
  Name is a statement, not a topic. Never changes after creation.
- One fact, one file. No ordinals; records reference each other by file link
  with the statement.
- Size guideline: 10–40 lines.
- A learning found on the way to a decision lives in the `Trap` section of
  that decision, not here.

## What Is Recorded

A fact that contradicts the docs, the name or the expectation, and was
verified: behaviour of a library, platform, tool, external API; an
environment limit; the cause of a non-obvious failure.

Not recorded: a choice between options (that is a decision), a fact from the
docs that simply was not read, a one-off error without reproduction.

Test: "we thought X, it is Y, verified by Z" completes without guessing.

## When

- Same commit as the code or config the fact forced to change.
- A fact with no code change (an environment limit, say) is recorded the
  same day it is verified.

## Structure

```markdown
# <Statement in one line>

Found: YYYY-MM-DD
Plan: `.agents/plans/YYYYMMDD_HHMM_task_name.md` or —
Current: yes

## Expected

What followed from the docs, the name or common sense.

## Actual

What really happens. With conditions: version, platform, mode.

## Verified

Command, path, log, reproduction.

## Do

What to do or not do from now on. One line.
```

- Title is a statement: "Env override is applied before the template check",
  not "About env".
- `Verified` is required. Without reproduction it is a hypothesis, not a
  learning.
- `Found` never changes. `Current` is the only line edited later:
  `no since YYYY-MM-DD, reason`.
- A learning that becomes a rule for agents is duplicated in `AGENTS.md`;
  the record stays as the explanation.

## Expiry

- Records are never rewritten or deleted. A learning that stopped being true
  (version upgrade, platform change) gets `Current: no since YYYY-MM-DD,
  reason` in the header and a mark in `_TOC.md`.
- If a new fact replaces it, the new record links the old one in `Expected`.

## `_TOC.md`

The only place where the whole list is visible. Every file has a line.

```markdown
# Learnings

Index of `.agents/learnings/`. Rules: `learning.md`.

- 2026-09-01 — [Env override is applied before the template check](20260901_1130_env_override_before_template_check.md)
- 2026-09-01 — [Sandbox on x86_64 needs a `/lib64` symlink](20260901_1500_sandbox_x86_64_needs_lib64.md),
  outdated since 2026-09-10

## Hypotheses

- One line per unverified observation.
```

- Line = date, statement verbatim as in the file, link. Order by file name.
  New lines go before "Hypotheses".
- Mark `outdated since YYYY-MM-DD` only on expired records. Date and
  statement of the line never change.
- "Hypotheses" is always the last section: an observation without
  reproduction. After verification the line is removed and becomes a record,
  or is deleted if it did not hold.

## Checklist

1. Title is a statement, file name follows the plan scheme.
2. All four sections present, `Verified` has a command or path.
3. It is not a choice: the record has no alternative. Otherwise it goes to
   `.agents/decisions/`.
4. Header has `Found`, `Plan`, `Current`.
5. `_TOC.md` has the line; expired records changed only in `Current` and the mark.
6. A rule for agents is duplicated in `AGENTS.md`.
