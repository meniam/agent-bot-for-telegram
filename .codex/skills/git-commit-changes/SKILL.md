---
name: git-commit-changes
description: Safely review, stage, and commit repository changes with Git. Use when the user asks Codex to commit changes, make a git commit, save current work in Git, create a checkpoint commit, or prepare a commit message after code/documentation edits.
---

# Git Commit Changes

Commit only intentional repository changes, preserving unrelated user work.

## Workflow

1. Inspect repository state:
   - Run `git status --short`.
   - Review relevant diffs with `git diff` and, if staged changes exist, `git diff --cached`.
   - Identify untracked files before staging them.

2. Decide commit scope:
   - Include only changes that belong to the requested work.
   - Do not stage unrelated files, generated/runtime files, secrets, local config, logs, uploads, or user changes outside the task.
   - If unrelated changes are mixed into a file you need to commit, use partial staging (`git add -p`) only when it is safe and non-interactive alternatives are available; otherwise ask the user.

3. Validate before commit when practical:
   - Run focused tests, linters, or format checks relevant to the changed files.
   - If validation is expensive or unavailable, state what was skipped and why.

4. Stage intentionally:
   - Prefer explicit paths: `git add path/to/file`.
   - Avoid `git add .` unless the diff is small, fully inspected, and entirely in scope.
   - Re-run `git status --short` and `git diff --cached --stat`.

5. Commit:
   - Use a concise imperative subject that summarizes the whole commit in one line.
   - Add a body where each meaningful change is listed on its own bullet line.
   - Prefer this format:
     ```text
     Short overall summary

     - Change one
     - Change two
     - Change three
     ```
   - Run `git commit -m "Short overall summary" -m "- Change one
     - Change two
     - Change three"` when committing from the shell.

6. Report result:
   - Provide commit hash and subject.
   - Summarize validation run.
   - Mention any uncommitted changes left behind.

## Safety Rules

- Never use destructive commands such as `git reset --hard`, `git checkout --`, or broad cleanup unless the user explicitly asks.
- Never commit secrets. If a token, password, private key, or local credential appears in the staged diff, stop and tell the user.
- Never amend, rebase, squash, or force-push unless explicitly requested.
- Do not change author identity unless explicitly requested.
- If there is nothing to commit, say so and include the clean/dirty status.
