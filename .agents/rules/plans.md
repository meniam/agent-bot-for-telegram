# Plan Rules

How agents write and maintain task plans. Applies to any project.
A plan lets another agent continue the task without guessing: goal, scope,
decisions, order of steps, checks, risks, status, next safe step.

## Location and Naming

- Plans live in `.agents/plans/`. Only plan files plus `AGENTS.md` / `CLAUDE.md`.
- File name: `YYYYMMDD_HHMM_short_task_name.md`: creation date and time, then
  latin `snake_case`. Example: `20260902_1210_person_email_unique`.
  Name describes the result, not the method. Name does not change once created;
  the timestamp is the creation moment, not the last edit.
- Same scheme as migrations, so plans sort by creation in `ls`.
- Temporary files (scripts, dumps, maps, JSON/CSV, intermediate results) go
  only to `var/agents/plans/<planName>/`. Never into `.agents/plans/`.
  No `messenger-map.md`, `routes-before.json` next to plans.
- Size guideline: up to 1500 lines (`wc -l`). At 1500–2000 the agent warns and
  proposes splitting into a main plan plus phase/runbook files. Over 2000
  only with explicit consent.
- One plan, one task. Independent work that needs another release, owner or
  approval goes to "Out of scope" or a separate plan.
- Do not read `.env`, `.env.*`, `secrets/**`, private keys or tokens for planning.
- Need DB schema, tables, columns, indexes or read-only SQL: use the project's
  read-only DB access (MCP or `psql`), do not guess from code.

## Checkboxes

- `- [ ]` not done.
- `- [0]` done, but the check has not run or the result is not accepted yet.
- `- [x]` done and the step's check is green. Never after "code written".
- `- [-]` skipped by joint decision with the developer. Reason and date next to it.
- `- [0]` is temporary. A `done` plan has no `- [0]` left.
- Extra text status is allowed next to a checkbox: `status: implemented`,
  `verification: not_run`, `blocked: ...`, `owner: ...`. It adds to the
  checkbox, never replaces the rules above.
- Mark a step the moment it is finished, together with the change it covers.
  Never collect marks to write them in one batch at the end: the context window
  runs out before the batch is written, and the plan is then left claiming that
  finished work is still open. The same applies to the run log and to status.

## Plan Status

Set and updated by the agent. Values and meaning are strict:

| Status        | Meaning                                                                                         |
| ------------- | ----------------------------------------------------------------------------------------------- |
| `draft`       | Being discussed. No decision to do the task yet, or open decisions / unverified facts remain.  |
| `approved`    | Developer confirmed the plan is to be executed.                                                 |
| `in_progress` | Agent has started changing code.                                                                |
| `blocked`     | A question or external condition makes continuing risky.                                        |
| `done`        | Work finished, final definition of done closed.                                                 |
| `archived`    | No longer needed, not replaced. Reason is written next to it.                                   |
| `superseded`  | Replaced by another plan. Link to the new plan and reason are written next to it.               |

- While a plan is only discussed, status is `draft`.
- A task given as "do / implement / fix" counts as intent to execute. For mass
  or risky changes the agent still fixes goal, scope, invariant, Step 0 and
  checks first, then moves to `in_progress`.
- No `approved` while an open question can change the architecture.
- No `done` if checks did not run and the reason is not recorded.
- No mass changes until goal, scope, main invariant and verification method are
  written down.
- If the user asked for a plan only, the agent does not start implementing
  without a separate confirmation.

## Branch and PR

- Work runs in a separate git branch. Branch and PR are written in the plan
  header once implementation starts.
- Branch name: `<type>/YYYYMMDD_HHMM_short_task_name`. Name after the folder
  equals the plan file name. Example: `feature/20260902_1210_person_email_unique`.
- Types: `feature/`, `bug/`, `refactor/`, `migration/`, `chore/`, `hotfix/`.
- One branch, one plan.
- `draft`: header says `Branch: —`, `PR: —`. Branch is created only after
  `approved`, at the move to `in_progress`.
- PR link is added to the header as soon as the PR exists.
- Multi-PR plans (tracker/phase): main branch in the header, phase branches
  and PRs listed in `## Status` or in phase files.

## Plan Types

Pick by two questions, in order:

1. Several releases or phases with their own transition criteria?
   Yes → **Tracker / phase plan**. Each phase is its own migration or product file.
2. Does observable behavior change (API contract, data model, UI, business logic)?
   - No, mechanical work (move, rename, structural refactor, namespace) →
     **Migration / runbook plan**.
   - Yes → **Product / refactoring plan**.

Mixed task: no compromise. Two plans: migration first with invariant
"behavior unchanged", then a product plan on top.

**Migration / runbook**, required sections: goal, main invariant, Step 0 with
decisions, move map, baseline before changes, iterative steps, legacy cleanup,
verification, deploy / rollback, final DoD.

**Product / refactoring**, required sections: problem and why, new data model
or contract, write / read paths, UI / API / integrations, migration / backfill,
compatibility and release stages, open product questions, checks.

**Tracker / phase**, required sections: context, phases with statuses,
transition criteria, regression matrix, run log, questions to owners, links to
phase files.

## Structure

Default structure. A small task may shorten sections, but status, goal, scope,
invariant, steps and checks are never removed.

```markdown
# Step-by-step plan: <what we do>

Created: YYYY-MM-DD
Owner: <if known>
Branch: — until in_progress
PR: — until PR exists

## Status

- Current status: draft | approved | in_progress | blocked | done | archived | superseded
- Last update: YYYY-MM-DD
- Next safe step: ...

## Context and sources

- Read: `path/to/file`, `path/to/dir/**`.
- Verified by commands: `rg ...`, `...`.
- Not verified: ... Reason: ...

## Goal

Final result, short.

## Scope

- In scope.
- Out of scope.
- Files / modules / contours touched.

## Main invariant

What must not change. Example: no SQL diff, routes identical, public API
compatible, queue / DSN unchanged, no legacy assets in Network.

## Risks and strategy

- Risk: ...
- Decision: ...

## Decision log

| Date       | Decision | Reason | Confirmed by |
| ---------- | -------- | ------ | ------------ |
| YYYY-MM-DD | ...      | ...    | ...          |

## Step 0. Fix decisions before changes

- [ ] Decision 1.
- [ ] Decision 2.

## Step 1. Audit and baseline

- [ ] Build map / registry.
- [ ] Capture baseline before changes.

## Implementation steps

- [ ] Small verifiable step.
- [ ] Next small verifiable step.

## Verification

| Check | Command | Expected result | Status  |
| ----- | ------- | --------------- | ------- |
| ...   | `...`   | ...             | not_run |

## Deploy / rollback

- [ ] How to roll out.
- [ ] How to roll back.
- [ ] Cache, queues, sessions, CDN, cron, workers.

## Cleanup

- [ ] Temporary files live in `var/agents/plans/<planName>`.
- [ ] Temporary files are not in git.
- [ ] Artifacts needed later are moved to docs or into the plan.

## Definition of done

- [ ] Code changed only in scope.
- [ ] Main invariant holds.
- [ ] Checks green or deviations recorded explicitly.
- [ ] No `- [0]` steps left.
- [ ] No temporary files in `.agents/plans`.

## Known facts

Snapshot at planning time: file counts, commands, env, current implementation details.

## Open questions

| Question | Why it blocks | Options | Recommendation | Status |
| -------- | ------------- | ------- | -------------- | ------ |
| ...      | ...           | ...     | ...            | open   |
```

## Writing a Good Plan

**Start with the invariant.** It answers "how do we know nothing important
broke". Examples: ORM move changes only path / namespace / use, a new schema
diff is an error; router dump before and after matches by name, path, method;
queue names, DSN, retry, serializer, broker topology unchanged; runtime has no
requests to the removed legacy asset path, no 404, no JS errors. If the
invariant cannot be stated, research more first.

**Step 0 before any mass edit.** Step 0 fixes decisions that can change the
whole plan: target namespaces / paths, branch and commit strategy,
compatibility format, legacy / shim handling, deploy and rollback, what is out
of scope. Step 0 changes no code.

**Map first, then move.** For mass tasks build a registry before touching code:

```text
old path | old FQCN | new path | new FQCN | reason | check
file | legacy dependency found | new place | asset type | check
scenario | old behavior | new behavior | files touched | check
```

Map lives in `var/agents/plans/<planName>/`. If needed after the task, its
content moves into the plan or project docs.

**Facts apart from assumptions.** Write explicitly: "Context and sources" (what
was actually read and run), "Known facts" (verified by code, commands, grep,
docs), "Assumptions" (not proven yet), "Verified, not a problem" (looked risky,
ruled out), "Open questions" (need a human or external context). Never present
an assumption as a fact.

**Small verifiable steps.** Bad: "Move all controllers". Good: "Move `Api/Mobile`
to `src/Controller/Api/MobileApi`", "Update route resource for `mapi`",
"Compare router dump with baseline". Every big stage has a completion criterion.

**Explicit check commands.** Commands run from the repository root. Backend
commands go through the project's container / runner as its `AGENTS.md`
prescribes. Read-only code search may run on the host (`rg`). A check needs an
expected result; "run the command" alone is incomplete. Temporary files of a
check go to `var/agents/plans/<planName>/`.

**Runtime risks.** If the task touches namespaces, queues, cache, routing,
assets, migrations, sessions, CDN, workers, cron or external API, the plan has
`Deploy / rollback`: rollout order, whether workers stop, queue drain,
compatibility shim, which cache / proxy / opcache / CDN to clear, how to roll
back and what data may block rollback.

**No hidden blockers.** A question that may change the implementation goes to
`Open questions`, not into step text. If continuing without an answer is
dangerous, status is `blocked`.

**Keep the plan alive.** The plan must not drift from the code. Update
checkboxes by the rules above. New facts go to "Known facts", decisions to the
"Decision log". A strategy change is written explicitly: was, became, why. A
check that did not run says "not run" and why.

## Quality Checklist

1. The expected result is clear.
2. Out of scope is clear.
3. Header has branch `<type>/YYYYMMDD_HHMM_short_task_name` (PR once it exists).
4. Context and sources: what was read, verified, not verified.
5. Main invariant is stated.
6. Baseline exists, or why it is not needed.
7. Map / registry for mass changes.
8. Verification commands with expected result for each.
9. Deploy / rollback for runtime risks.
10. Open questions listed, or explicitly none.
11. Temporary files point to `var/agents/plans/<planName>`.
12. No temporary map / json / csv / txt in `.agents/plans`.
13. Another agent can continue without verbal explanations.
