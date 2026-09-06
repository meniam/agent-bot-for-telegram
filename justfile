# Entry point for project checks. `just` lists the recipes.

set shell := ["zsh", "-cu"]

default:
    @just --list

# Install the project and dev tools from the committed lock.
install:
    uv sync --locked

# Lint, format check and types: read-only, the CI gate.
lint:
    uv run --locked ruff check --no-fix .
    uv run --locked ruff format --check .
    uv run --locked mypy --no-incremental

# Apply safe ruff fixes, then format. Read the diff.
fix:
    uv run --locked ruff check --fix --no-unsafe-fixes .
    uv run --locked ruff format .

# Format only.
format:
    uv run --locked ruff format .

# Types only, incremental for speed.
typecheck:
    uv run --locked mypy

# Unit tests. Extra args go to pytest: `just test -k config`.
test *args:
    uv run --locked pytest -q {{args}}

# Lint + tests.
ci: lint test

# Dependency CVE audit of the locked runtime + dev set via pip-audit.
audit:
    #!/usr/bin/env zsh
    set -eu
    audit_requirements=$(mktemp)
    trap 'rm -f "$audit_requirements"' EXIT
    uv export --locked --format requirements-txt --all-groups --no-emit-project --output-file "$audit_requirements"
    uvx pip-audit -r "$audit_requirements"

# Run the bot from the project environment.
run:
    uv run --locked python -m src.bot
