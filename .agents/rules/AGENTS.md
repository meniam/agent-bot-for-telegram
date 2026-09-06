# Project Rules

Shared rules by Eugene Myazin, included into projects via
`.agents/rules/<file>.md` in their `AGENTS.md`. One file, one topic.

| File           | Topic                                                                      |
| -------------- | -------------------------------------------------------------------------- |
| `db.md`        | PostgreSQL naming: schemas, tables, columns, constraints, indexes; migrations |
| `plans.md`     | Task plans in `.agents/plans/`: names, checkboxes, statuses, branch, structure |
| `decisions.md` | Decision log in `.agents/decisions/`: what counts, record format, `_TOC.md` |
| `learning.md`  | Learnings in `.agents/learnings/`: facts found by experience, `_TOC.md`     |
| `changelog.md` | `CHANGELOG.md` in the project root: changes visible outside the code, by date |

## Journals

Four journals, one question each. The reader picks the file by the question.

| Question                | Journal    | Unit                          | Rules          |
| ----------------------- | ---------- | ----------------------------- | -------------- |
| How do we do it?        | Plan       | file per task                 | `plans.md`     |
| Why is it like this?    | Decision   | file per decision + `_TOC.md` | `decisions.md` |
| What did we find out?   | Learning   | file per fact + `_TOC.md`     | `learning.md`  |
| What changed, and when? | Changelog  | section per date              | `changelog.md` |

- Plan, decision, learning share the file name scheme
  `YYYYMMDD_HHMM_short_statement.md`: creation time, latin `snake_case`.
  Name never changes after creation.
- Every journal is live: the record goes in the same commit as the change.
- Plan → decision: a decision that outlives the plan gets a file in
  `.agents/decisions/` before the plan reaches `done`. Plan open questions
  that remain move to `_TOC.md` "Open questions".
- Plan → changelog: every change visible outside from "Definition of done"
  leaves a line under the date of the change.
- Decision vs learning: a choice between options is a decision; a verified
  fact with no alternative is a learning. A learning found on the way to a
  decision lives in that decision's `Trap` section.
- Learning never goes to the changelog.
- A rule for agents lives in the project `AGENTS.md` and links to the
  decision that introduced it. The decision is not retold there.

## How Rules Are Written

- A rule, not a discussion: "table is singular", no "preferably".
- Every rule has an example. Every ban says what to use instead.
- Every rule file ends with a `## Checklist`: numbered, one check per line.
- A rule that needs explaining is explained in one line. Longer means a
  document, not a rule.
