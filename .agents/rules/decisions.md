# Decision Rules

How agents record project decisions. A record explains why the code is the
way it is: what was chosen, what was rejected, what it costs. It lets another
agent keep the choice or revise it knowingly.

## Location and Naming

- Decisions live in `.agents/decisions/`: decision files, `_TOC.md`,
  `AGENTS.md` / `CLAUDE.md`. Nothing else.
- File name as for plans: `YYYYMMDD_HHMM_short_statement.md`, latin
  `snake_case`. Example: `20260831_1554_time_is_moscow_without_zone`.
  Name is a statement, not a topic. Never changes after creation.
- One decision, one file. No ordinals; records reference each other by file
  link with the statement.
- Size guideline: 20–80 lines. Longer means two decisions or a document.

## What Is Recorded

A decision that had an alternative: choice of technology, schema, storage,
module boundary, convention, data format; rejection of the obvious option;
deviation from a project rule with its reason.

Not recorded: implementation with no choice, text edits, reformatting,
one-off debugging, an open question (it belongs in "Open questions"), a rule
for agents by itself (it goes to `AGENTS.md` and links here), a fact found by
experience with no choice involved (that is a learning, see `learning.md`).

Test: "we chose X over Y because Z" completes without guessing.

## When

- The journal is live: a decision is written in the same commit as the code.
- With a plan: the plan's `Decision log` is the working table. A decision
  that outlives the plan gets a file before the plan reaches `done`; the plan
  row links to the file, the file links to the plan.
- Without a plan (debugging, review, operations): recorded the same day.

## Structure

`Context`, `Cost`, `Trap` are omitted when empty. `Decision` and `Why` are
required.

```markdown
# <Statement in one line>

Decided: YYYY-MM-DD
Plan: `.agents/plans/YYYYMMDD_HHMM_task_name.md` or —
Revised: —

## Context

What forced the choice. Measured facts: "Seq Scan, 1750 of 1816 rows
discarded by filter", not "the query was slow".

## Decision

What was chosen, enough to repeat without reading the code: table names,
paths, config keys, exact SQL, values.

## Why

Why this and why not the alternatives. Every rejected option is named with
the reason it lost.

## Cost

What we pay: manual sync, duplication, broken clients, extra query on start.
Condition for revisiting, if known. A decision with no cost is suspicious.

## Trap

A learning found on the way to this decision: behaviour that contradicts
the docs or the name. A standalone learning goes to `learning.md`.
```

- `Decided` never changes. `Revised` is the only line edited later:
  `revised by [<statement>](file) YYYY-MM-DD` or
  `superseded by [<statement>](file) YYYY-MM-DD`.

## Revisions

- Records are never rewritten. A changed or wrong decision gets a new record;
  its `Context` links the old one and says what changed.
- The old record changes only in the `Revised` line and the `_TOC.md` mark.
- `revised`: the decision still stands with a change. `superseded`: it no
  longer applies. Files are not deleted.

## `_TOC.md`

The only place where the whole list is visible. Every file has a line.

```markdown
# Decision Log

Index of `.agents/decisions/`. Rules: `decisions.md`.

- 2026-08-31 — [Time is stored as Moscow wall time, without zone](20260831_1554_time_is_moscow_without_zone.md)
- 2026-08-31 — [Table is called `meetings`](20260831_1748_meetings_table.md),
  superseded by [Tables are singular](20260831_1804_tables_are_singular.md)

## Open questions

- One line per question, no answer options.
```

- Line = date, statement verbatim as in the file, link. Order by file name.
  New lines go before "Open questions".
- Mark only on revised records: `revised by` / `superseded by` on the old
  line, `revises` / `supersedes` on the new one. Date and statement of the
  old line never change.
- "Open questions" is always the last section. A decided question is removed
  from the list and becomes a record. Plan open questions that remain after
  `done` move here.

## Checklist

1. Title is a statement, file name follows the plan scheme.
2. `Decision` reproduces without reading the code, `Why` names alternatives.
3. `Cost` is written or its absence is deliberate.
4. Header has `Decided`, `Plan`, `Revised`.
5. References are links with statements, not ordinals.
6. `_TOC.md` has the line; revised records changed only in `Revised` and the mark.
7. A rule for agents lives in `AGENTS.md` and links here.
