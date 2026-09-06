# Changelog

Что изменилось и когда, для того, кто обновляет бота или ищет день, когда
что-то сломалось. Правила — `.agents/rules/changelog.md`. Проект не
версионируется, поэтому единица — день изменения. Изменения до 2026-09-06
здесь не описаны: см. `git log` и планы в `.agents/plans/`.

## 6 сентября 2026, Воскресенье

- Безопасность: обновлены aiohttp 3.14.1 → 3.14.3 (PYSEC-2026-3546, PYSEC-2026-3547),
  cryptography 49.0.0 → 50.0.1 (PYSEC-2026-3552), mcp 1.28.0 → 1.29.1
  (PYSEC-2026-3483); `just audit` чист. Пересоберите Docker-образ.
- Изменено: установка проекта — `uv sync --locked` на Python 3.14 вместо
  `python3 -m venv` + `pip install -e ".[dev]"`; старое окружение удалить
  (`rm -rf .venv`) ([решение](.agents/decisions/20260906_1323_project_runs_on_uv_with_python_314.md)).
- Изменено: Docker-образ на `python:3.14-slim`, зависимости ставятся uv из
  `uv.lock`; пересоберите образ командой `docker compose build`
  ([план](.agents/plans/20260906_1300_tooling_follows_python_and_ruff_rules.md)).
- Добавлено: `justfile` с рецептами `lint`, `fix`, `format`, `typecheck`,
  `test`, `ci`, `audit`, `run`; `just ci` — обязательная проверка перед коммитом.
- Удалено: `requirements.txt`; `pyright` и `bandit` из dev-инструментов,
  аудит зависимостей — `just audit`
  ([решение](.agents/decisions/20260906_1324_ruff_full_tier_with_project_adaptations.md)).
