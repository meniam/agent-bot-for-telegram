# Проект собирается uv на Python 3.14, проверки идут через `just`

Decided: 2026-09-06
Plan: `.agents/plans/20260906_1300_tooling_follows_python_and_ruff_rules.md`
Revised: —

## Context

Проект ставился `python3 -m venv` + `pip install -e ".[dev]"`, dev-инструменты
лежали в `optional-dependencies`, `uv.lock` был, но ничем не использовался.
`requires-python >= 3.11`, Docker на `python:3.12-slim`, локальный `.venv` уже
на 3.14.4 и зелёный. В dev-наборе были `pyright`, `bandit`, `pip-audit`.
`~/.agents/rules/python.md` описывает другую схему: uv, группа `dev`, кэши в
`var/cache/`, `just` как точка входа.

## Decision

- `requires-python = ">=3.14"`, `.python-version` = `3.14`,
  `[tool.uv] python-preference = "only-managed"`.
- Dev-инструменты в `[dependency-groups] dev`: `ruff==0.16.6`, `mypy>=2.3`,
  `pytest`, `pytest-asyncio`, `types-PyYAML`, `types-croniter`.
  `pyright` и `bandit` убраны; `pip-audit` не в зависимостях, а в рецепте
  `just audit` через `uvx` по экспорту `uv.lock`.
- Кэши: `var/cache/ruff`, `var/cache/mypy`, `var/cache/pytest`; строки
  `.ruff_cache/` и подобные убраны из `.gitignore`.
- `justfile`: `install`, `lint`, `fix`, `format`, `typecheck`, `test`, `ci`,
  `audit`, `run`. Все рецепты — `uv run --locked`.
- Docker: `python:3.14-slim`, `uv sync --locked --no-dev` в два слоя
  (зависимости, затем проект), `PATH=/app/.venv/bin:$PATH`,
  `UV_PYTHON_PREFERENCE=only-system`.
- `requirements.txt` удалён.

## Why

- Один lock и одна команда установки для локальной машины и образа: раньше
  образ ставил `pip install -e .` без lock и получал другие версии.
- 3.14, а не 3.12: локально уже работает, правила для проектов — 3.14, и на
  3.14 не нужен `from __future__ import annotations`.
- `pyright` дублировал `mypy --strict` на том же коде; `bandit` дублирует
  набор `S` в ruff. Отвергнуто «оставить как второй слой»: два типчекера
  расходятся в мелочах и каждый требует своих подавлений.
- `pip-audit` через `uvx`, а не в `dev`: он нужен по расписанию, а не на
  каждом изменении, и тянет 30+ пакетов в окружение.
- Отвергнуто «оставить `optional-dependencies.dev`»: extras нужно просить
  явно, и проект незаметно линтится чужим ruff из PATH.

## Cost

- На сервере и у контрибьюторов нужен `uv`; `pip`-путь больше не описан.
- Образ пересобирается целиком (новая база).
- `only-managed` в `pyproject.toml` действует и в контейнере: без
  `UV_PYTHON_PREFERENCE=only-system` uv скачал бы второй интерпретатор в
  образ. Переменная окружения перекрывает настройку файла.
