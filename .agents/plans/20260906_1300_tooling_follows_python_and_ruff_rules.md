# Step-by-step plan: тулинг проекта по правилам `python.md` и `ruff.md`

Created: 2026-09-06
Owner: Eugene Myazin
Branch: `chore/20260906_1300_tooling_follows_python_and_ruff_rules`
PR: — until PR exists

Тип: migration / runbook. Поведение бота не меняется; меняются сборка,
зависимости, линт, типизация, документация и то, что нужно поправить в коде,
чтобы новый линт был зелёным.

## Status

- Current status: in_progress
- Last update: 2026-09-06
- Next safe step: Step 1, baseline.

## Context and sources

- Read: `pyproject.toml`, `requirements.txt`, `uv.lock` (шапка), `.gitignore`,
  `.docker/abt/Dockerfile`, `.docker/docker-entrypoint.sh`, `docker-compose.yml`,
  `README.md`, `INSTALLATION.md`, `AGENTS.md`, `~/.agents/rules/python.md`,
  `~/.agents/rules/ruff.md`, `src/infra/interactions/*.py`, `src/infra/healthcheck.py`.
- Verified by commands (2026-09-06, из корня, `.venv` на Python 3.14.4,
  ruff 0.15.18, mypy 2.1.0, uv 0.11.8):
  - текущие проверки: `ruff check src/ tests/` чисто, `mypy src/ tests/ --strict`
    чисто, `pytest -q` → 374 passed; `ruff format --check src tests` →
    61 файл из 91 не отформатирован (формат никогда не применялся).
  - сухой прогон `uvx ruff@0.16.6` с конфигом full из `ruff.md`
    (`var/agents/plans/<plan>/ruff_full.toml`): 298 находок, 265 в `src`,
    33 в `tests`; 32 чинятся safe-фиксами. Разбивка — в «Known facts».
  - тот же прогон с `target-version = "py314"`: +148 `TC001`–`TC003`,
    +20 `UP037`, +3 `UP043`; `TC00x` чинятся только `--unsafe-fixes`.
  - `mypy --strict` без проектного конфига: 0 ошибок в коде, только два
    `import-untyped`: `yaml`, `croniter`. `disallow_untyped_defs = false` больше
    ничего не прикрывает.
  - `uv lock --check` — lock актуален; `uv sync --dry-run` снимет 38 пакетов
    (bandit, pip-audit, pyright и их транзитивные) и ничего не доставит.
  - `uvx mypy@latest --version` → 2.3.1; `uvx ruff@latest --version` → 0.16.6.
  - `which just` → `/opt/homebrew/bin/just`.
- Not verified: сборка Docker-образа на `python:3.14-slim` и наличие linux-колёс
  всех зависимостей под 3.14. Причина: не запускал `docker build`; проверяется
  в шаге 7.
- Not verified: как проект развёрнут на сервере (bare `.venv` по
  `INSTALLATION.md` или только Docker). В `INSTALLATION.md` нет systemd/docker;
  `DEPLOY.md` в gitignore и не читался (личный файл).

## Goal

`pyproject.toml`, окружение, Docker, проверки и документация соответствуют
`~/.agents/rules/python.md` и `~/.agents/rules/ruff.md`; `uv run --locked ruff check`,
`ruff format --check`, `mypy`, `pytest` зелёные из одного `just ci`; отклонения
от правил записаны с причиной в `pyproject.toml` и в `.agents/decisions/`.

## Scope

- In scope:
  - `pyproject.toml`: uv, dependency group `dev`, кэши в `var/cache/`, версия
    Python, конфиг ruff (full) и mypy (strict по правилам), pytest.
  - `.python-version`, `uv.lock`, удаление `requirements.txt`.
  - Точечные правки кода и тестов, чтобы новый линт был зелёным (см. карту).
  - `justfile` с рецептами `lint`, `format`, `fix`, `typecheck`, `test`, `ci`,
    `audit`, `run`.
  - `Dockerfile`: установка через uv из `uv.lock`, версия Python.
  - Документация: `README.md`, `INSTALLATION.md`, `AGENTS.md` («Run and
    Check», «Contributor Rules»), `docs/DOCKER.md` там, где упоминается pip.
  - `.gitignore`: убрать `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`
    (кэши уезжают в `var/`), убрать дубль `.vscode/`.
  - Журналы: решения, `CHANGELOG.md`.
- Out of scope:
  - Переименование пакета `src` в нормальный пакет (`abt`): затрагивает импорты
    во всём коде, `[project.scripts]`, Dockerfile, bind-mount конфига в
    `/app/src/config/`. Отдельный план.
  - Смена политики докстрингов проекта (каждый символ, включая приватные,
    документирован): она строже правил, остаётся.
  - Любые изменения поведения бота, i18n, конфигов.
  - CI-пайплайн (GitHub Actions): в репозитории его нет и он не заказан.
    `just ci` — локальный эквивалент.
- Files touched: `pyproject.toml`, `uv.lock`, `.python-version`, `justfile`,
  `requirements.txt` (удалить), `.gitignore`, `.docker/abt/Dockerfile`,
  `docker-compose.yml` (healthcheck), `README.md`, `INSTALLATION.md`,
  `AGENTS.md`, `docs/DOCKER.md`, `src/**` и `tests/**` в объёме карты правок,
  `.agents/decisions/*`, `CHANGELOG.md`.

## Main invariant

Поведение бота не меняется: `pytest -q` → 374 passed до и после; никаких
правок в `src/i18n/*.json` и `src/config/*.yaml`; diff в `src/` состоит только
из форматирования, докстрингов, импортов, логирования исключений, переименований
из карты правок и замен, перечисленных там же. Публичные имена меняются только
те, что названы в карте (`AgentTurnReset`, `AgentEventStreamTimeout`, приватные
атрибуты гейта).

## Risks and strategy

- Risk: `ruff format` впервые применяется к 61 файлу — огромный diff, в
  котором теряются смысловые правки.
  Decision: формат и safe-фиксы — отдельный коммит до любых ручных правок.
- Risk: переход на Python 3.14 в Docker; какая-нибудь зависимость без колеса
  под linux/3.14 соберётся из исходников или не соберётся.
  Decision: `docker compose build` — обязательная проверка шага 7; при
  провале образ остаётся на `python:3.12-slim`, `requires-python` держится
  `>=3.12`, решение фиксируется в журнале.
- Risk: `TC001`–`TC003` на 3.14 требуют 148 переносов импортов под
  `TYPE_CHECKING`, только unsafe-фиксами; dataclass-поля и pydantic-модели
  читают аннотации в рантайме.
  Decision: правила `TC001`–`TC003` в `ignore` с причиной (на 3.14 аннотации
  ленивые, импорт ради типа ничего не стоит); остальные `TC` остаются.
  См. Open questions.
- Risk: `BLE001` (25) — в боте `except Exception` держит цикл живым; слепая
  «починка» на узкие исключения меняет поведение.
  Decision: не сужать исключения. Там, где ошибка логируется — `exc_info=True`
  или `.exception()`, что снимает `BLE001`; там, где подавление намеренное
  (best-effort удаление сообщения и т.п.) — `# noqa: BLE001` с причиной.
- Risk: `SLF001` (55) — модули `interactions/*` читают приватные поля гейта.
  Decision: поля, которые читают четыре модуля, не приватные; убрать `_`
  у `_bot`, `_t`, `_timeout`, `_send_md`, `_pending`, `_aq`, `_aq_aborted`,
  `_plan_pending`, методов `_delete_prompt`, `_format_request`, `_cl`
  (`chat_logger`). См. Open questions, есть альтернатива.
- Risk: `DOC201`/`DOC501`/`DOC402` (106) — секции `Returns`/`Raises`/`Yields`
  нужно писать руками и по смыслу, а не по сигнатуре.
  Decision: отдельный шаг после всех кодовых правок, файл за файлом, с
  чтением функции; одна строка «Returns: …» без содержания не принимается.
- Risk: сервер стоит на pip-`.venv` по старому `INSTALLATION.md`.
  Decision: в `INSTALLATION.md` описать переход (`uv sync --locked`); в
  Deploy — шаг для bare-установки.

## Decision log

| Date       | Decision                                                                | Reason                                                     | Confirmed by |
| ---------- | ----------------------------------------------------------------------- | ---------------------------------------------------------- | ------------ |
| 2026-09-06 | План — migration/runbook с инвариантом «поведение не меняется»          | Все правки механические или документационные               | agent        |
| 2026-09-06 | Формат и safe-фиксы — отдельный первый коммит                            | Иначе смысловые правки тонут в diff на 61 файл             | agent        |
| 2026-09-06 | Python 3.14 везде: `.python-version`, `requires-python`, Docker          | Локально уже 3.14, правила для проектов — 3.14             | Eugene       |
| 2026-09-06 | `justfile` — точка входа для проверок                                    | Как в brain-agent; правила ссылаются на `just lint`        | Eugene       |
| 2026-09-06 | `python.md` и `ruff.md` в `.agents/rules/` не копируются                 | Применяются через `pyproject.toml`, читать их агенту не надо | Eugene     |
| 2026-09-06 | Пакет остаётся `src`; SLF001 — переименование полей гейта; TC001–003 в ignore; DOC-секции и BLE001 — по месту в этом плане | «Оставим src пока и давай делать», рекомендации плана приняты | Eugene |

## Step 0. Fix decisions before changes

- [x] Python: `3.14` везде (подтверждено 2026-09-06) (`.python-version`, `requires-python = ">=3.14"`,
  `python:3.14-slim`, `python-preference = "only-managed"`). Локальный `.venv`
  уже 3.14.4 и зелёный. Откат к 3.12 только по провалу `docker build`.
- [x] Инструменты: `ruff==0.16.6` (точный пин, full-tier с preview `DOC`),
  `mypy>=2.3`, `pytest>=8`, `pytest-asyncio`, `types-PyYAML`, `types-croniter`.
  `pyright` и `bandit` удаляются; `pip-audit` уходит в `just audit` через
  `uvx` с экспортом lock по рецепту `python.md`. `# nosec` в коде убираются.
- [x] Ruff: конфиг full из `ruff.md` целиком. Проектные адаптации с
  комментарием в `pyproject.toml`:
  - `line-length = 100` (уже есть; типизированные сигнатуры SDK);
  - `ANN401` в ignore (границы SDK/JSON; было в проекте, 42 места);
  - `TC001`, `TC002`, `TC003` в ignore (см. риск выше);
  - `src` не задаётся: пакет называется `src`, дефолт `[".", "src"]` покрывает;
  - per-file для `tests/**` — из правил; `src/**/__init__.py` — `D104`;
  - `pydocstyle.convention = "google"` (на текущих докстрингах 0 находок `D`);
  - `"src/i18n/*.json" = ["ALL"]` удалить: ruff json не линтит.
  - Из старого ignore не переносятся: `S101` (одно место, чинится), `S110`
    (0 находок), `E501` (и так выключено), `TC006` (10, safe-фикс), `RET504`
    (1, чинится).
- [x] mypy: `strict`, `warn_unreachable`, `warn_unused_configs`,
  `enable_error_code = ["ignore-without-code", "redundant-expr", "truthy-bool"]`,
  `files = ["src", "tests"]`, `cache_dir = "var/cache/mypy"`,
  `plugins = ["pydantic.mypy"]`. Глобальные `ignore_missing_imports` и
  `disallow_untyped_defs = false` удаляются; stubs ставятся пакетами.
  Проверить, что 20 существующих `# type: ignore[...]` не станут unused.
- [x] Точка входа — `justfile` (подтверждено 2026-09-06). Рецепты: `install` (`uv sync --locked`),
  `lint` (ruff check --no-fix + format --check + mypy), `format`, `fix`
  (`--fix --no-unsafe-fixes` затем `format`), `test`, `ci` (lint + test),
  `audit` (pip-audit по экспорту), `run` (`uv run python -m src.bot`).
- [x] Docker: `COPY --from=ghcr.io/astral-sh/uv` уже есть; установка
  `uv sync --locked --no-dev` в два слоя (сначала без проекта), `ENV
  PATH=/app/.venv/bin:$PATH`, `UV_PROJECT_ENVIRONMENT=/app/.venv`, CMD и
  healthcheck без изменений (`python` из venv на PATH). Editable-установка
  проекта сохраняется (uv ставит проект editable по умолчанию), bind-mount
  `/app/src/config/config.yaml` работает как раньше.
- [x] `requirements.txt` удаляется: помечен legacy, содержит
  `telegramify-markdown`, которого в коде нет.
- [x] Ветка: `chore/20260906_1300_tooling_follows_python_and_ruff_rules`.
  Коммиты: (1) pyproject/lock/justfile, (2) format + safe fixes, (3) правки
  кода по карте, (4) докстринги, (5) Docker и документация, (6) журналы.

## Step 1. Audit and baseline

- [ ] Сохранить в `var/agents/plans/<plan>/baseline/`: `ruff check` и `mypy`
  по старому конфигу, `pytest -q` (374), `ruff format --check` (61 файл),
  `docker compose build` текущего образа (успех/провал) — до правок.
- [ ] Сохранить полный список находок нового линта (уже есть:
  `var/agents/plans/<plan>/ruff_full.json`).

## Implementation steps

### Шаг 2. `pyproject.toml`, окружение, `justfile`

- [ ] `[project] requires-python = ">=3.14"`; `.python-version` → `3.14` через
  `uv python pin 3.14`.
- [ ] `[tool.uv] python-preference = "only-managed"`.
- [ ] `[project.optional-dependencies] dev` → `[dependency-groups] dev`.
- [ ] `[tool.ruff]` / `[tool.ruff.lint]` / `format` / `pydocstyle` / `pydoclint`
  / `flake8-annotations` / `flake8-type-checking` / `per-file-ignores` по Step 0.
- [ ] `[tool.mypy]` по Step 0; `[tool.pyright]` удалить.
- [ ] `[tool.pytest.ini_options] cache_dir = "var/cache/pytest"`.
- [ ] `uv lock`, `uv sync --locked`; убедиться, что `.venv/bin/ruff --version`
  → 0.16.6 и `mypy` ≥ 2.3.
- [ ] `justfile` по Step 0; `just` без аргументов печатает список.
- [ ] Удалить `requirements.txt`; `.gitignore` без `.mypy_cache/`, `.ruff_cache/`,
  `.pytest_cache/` и второго `.vscode/`; `var/` уже игнорируется.
- [ ] Удалить пустые `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/` из корня.

### Шаг 3. Формат и safe-фиксы (отдельный коммит)

- [ ] `uv run --locked ruff check --fix --no-unsafe-fixes .` → ~32 фикса
  (`TC006` 10, `RUF100` 1, `FURB110` 1, `PT018` часть), diff прочитан.
- [ ] `uv run --locked ruff format .` → 61 файл.
- [ ] `uv run --locked pytest -q` → 374 passed.

### Шаг 4. Правки кода по карте (src)

Карта: правило → файлы → действие. Числа — из сухого прогона.

- [ ] `LOG015` (3, `src/bot.py:437,448,462`): вызовы root-логгера → модульный
  `log = logging.getLogger(__name__)`.
- [ ] `G201` (1, `task_runner.py:234`): `.error(..., exc_info=True)` →
  `.exception(...)`.
- [ ] `DTZ006` (1, `message_db.py:202`): `fromtimestamp(ts)` → с `tz=UTC`
  и тем же форматом вывода; проверить, что формат `created_at` в БД не меняется
  (тест `test_message_db`).
- [ ] `FURB162` (1, `task_types.py:210`): `raw.replace("Z", "+00:00")` →
  `fromisoformat(raw)` (3.11+ понимает `Z`).
- [ ] `TRY004` (5, `config/__init__.py`): `raise ValueError` при проверке типа
  → `TypeError`. Проверить тесты конфига, ждущие `ValueError`.
- [ ] `TRY203` (1, `agent_base.py:60`): убрать `except: raise`.
- [ ] `TRY300` (2, `tool_status.py:219`, `test_message_db.py:24`): `return`
  в `else`.
- [ ] `N818` (2, `agent_types.py:47,51`): `AgentTurnReset` →
  `AgentTurnResetError`, `AgentEventStreamTimeout` →
  `AgentEventStreamTimeoutError`; `rg` по всем использованиям.
- [ ] `A002` (3, `claude_agent.py:211,218,233`): параметр `input` → `tool_input`
  (или как в SDK-сигнатуре хука, если имя позиционное — проверить и при
  необходимости `# noqa: A002` с причиной).
- [ ] `S101` (1, `uploads.py:37`): `assert ctx.uploads is not None` → явная
  проверка с `raise RuntimeError` или ранним возвратом; убрать `# nosec`.
- [ ] `T201` (1, `healthcheck.py:55`): stdout — назначение CLI →
  `# noqa: T201  # healthcheck output is the contract`.
- [ ] `RET504` (1, `markdown.py:213`), `PERF401` (7), `PLW0108` (3, тесты):
  по месту.
- [ ] `BLE001` (25, 16 файлов): по стратегии из «Risks».
- [ ] `SLF001` (55, `interactions/{plan_mode,ask_user_question,
  permission_prompt,push_notification}.py`): переименование полей гейта по
  Step 0; внутри `gate.py` доступ через новые имена; `rg '\._(bot|t|timeout|
  send_md|pending|aq|aq_aborted|plan_pending|delete_prompt|format_request|cl)\b'`
  пуст.
- [ ] `ANN401` (42): остаётся в ignore, ничего не делать.
- [ ] `rg 'nosec'` пуст.

### Шаг 5. Правки тестов

- [ ] `PT018` (20): составные `assert a and b` → отдельные `assert`.
- [ ] `PT011` (4, `test_task_types.py`, `test_task_store.py`):
  `pytest.raises(ValueError, match=...)`.
- [ ] `PLE2502` (1, `test_task_service.py:62`): литеральный U+202E →
  `"\u202e"` в строке; смысл теста сохраняется.
- [ ] `RUF100` (1, `test_graphiti_tool.py:81`): ушёл safe-фиксом в шаге 3,
  проверить.

### Шаг 6. mypy и докстринги

- [ ] `uv run --locked mypy` → `Success`; unused `# type: ignore` (если
  появятся из-за `warn_unused_ignores`) убрать.
- [ ] `DOC201` (74), `DOC501` (29), `DOC402` (3): добавить `Returns:` /
  `Raises:` / `Yields:` по смыслу. Порядок: `codex_agent.py`,
  `claude_agent.py`, `pi_agent.py`, `interactions/*`, `config/__init__.py`,
  `task_runner.py`, остальное. Правило: секция описывает контракт, а не
  повторяет тип.
- [ ] `uv run --locked ruff check --no-fix .` → 0 находок.

### Шаг 7. Docker и документация

- [ ] `Dockerfile`: `FROM python:3.14-slim`; `PIP_*` env удалить; слои
  `COPY pyproject.toml uv.lock README.md LICENSE ./` → `uv sync --locked
  --no-dev --no-install-project` → `COPY src .agents` → `uv sync --locked
  --no-dev`; `ENV PATH="/app/.venv/bin:$PATH"`.
- [ ] `docker compose build` успешен; `docker compose run --rm abt python -c
  "import src.bot"`; healthcheck-команда отрабатывает (`python -m
  src.infra.healthcheck` → код 1 «missing» без heartbeat — ожидаемо).
- [ ] `README.md` «Quick start» и «Tech»: `uv sync --locked`, `just`, список
  инструментов без pyright/bandit.
- [ ] `INSTALLATION.md`: разделы 3, 11 (дерево), 12 (Updating) — uv; убрать
  строку про `requirements.txt`; раздел «переход с pip-venv».
- [ ] `AGENTS.md` «Run and Check»: команды `just …` и `uv run --locked …`;
  «Contributor Rules»: `pydocstyle` → google, упомянуть `DOC`-секции и
  `# noqa` с кодом и причиной.
- [ ] `docs/DOCKER.md`: если описывает установку — обновить.
- [ ] `.claude/settings.local.json` не в git; разрешения на `.venv/bin/ruff`
  остаются валидными (uv кладёт бинари туда же).

### Шаг 8. Журналы

- [ ] `.agents/decisions/`: «Проект собирается uv на Python 3.14»,
  «Ruff full с адаптациями `ANN401`, `TC001–TC003`, `line-length 100`»,
  «Поля гейта, читаемые модулями interactions, публичные» (если выбран
  вариант с переименованием), «pyright и bandit убраны, pip-audit по
  расписанию через uvx».
- [ ] `CHANGELOG.md` под 6 сентября 2026: `Изменено:` установка через
  `uv sync --locked`, Docker-образ на Python 3.14; `Удалено:`
  `requirements.txt`, `pyright`/`bandit` из dev-инструментов.
- [ ] Open questions, оставшиеся после `done` → `.agents/decisions/_TOC.md`.

## Verification

Все команды из корня, после `uv sync --locked`.

| Check                | Command                                                   | Expected result                         | Status  |
| -------------------- | --------------------------------------------------------- | --------------------------------------- | ------- |
| Lock актуален        | `uv lock --check`                                         | exit 0                                  | not_run |
| Версии инструментов  | `uv run --locked ruff --version; uv run --locked mypy --version` | `ruff 0.16.6`, `mypy 2.3.x`        | not_run |
| Lint                 | `uv run --locked ruff check --no-fix .`                   | `All checks passed!`                    | not_run |
| Format               | `uv run --locked ruff format --check .`                   | `N files already formatted`, 0 would reformat | not_run |
| Types                | `uv run --locked mypy --no-incremental`                   | `Success: no issues found`              | not_run |
| Tests                | `uv run --locked pytest -q`                               | `374 passed`                            | not_run |
| Всё вместе           | `just ci`                                                 | exit 0                                  | not_run |
| Кэши в `var/`        | `ls var/cache`                                            | `mypy pytest ruff`; в корне нет `.*_cache` | not_run |
| Аудит                | `just audit`                                              | exit 0 или список CVE в отчёте          | not_run |
| Остатки старого      | `rg -n 'nosec\|pyright\|bandit\|requirements.txt\|pip install' --glob '!.agents/plans/**' --glob '!uv.lock'` | пусто (кроме `CHANGELOG.md`, решений) | not_run |
| Docker               | `docker compose build`                                    | образ собран                            | not_run |
| Docker импорт        | `docker compose run --rm --no-deps abt python -c 'import src.bot'` | exit 0                          | not_run |
| Инвариант поведения  | `git diff main -- src/i18n src/config/*.yaml`             | пусто                                   | not_run |

## Deploy / rollback

- [ ] Docker: `git pull && docker compose build && docker compose up -d`.
  Образ меняет базу (3.12 → 3.14) и способ установки; данные в `/app/var` —
  bind-mount, не затрагиваются.
- [ ] Bare-установка (если есть): поставить `uv`, `rm -rf .venv && uv sync
  --locked`, перезапустить процесс. Старый pip-`.venv` больше не описан.
- [ ] Rollback: `git checkout <prev>` + `docker compose build` — предыдущий
  Dockerfile и `pip install -e .` самодостаточны; миграций данных нет.
- [ ] Очереди, кэши, cron, CDN: не задействованы.

## Cleanup

- [ ] Временные файлы живут в `var/agents/plans/20260906_1300_tooling_follows_python_and_ruff_rules/`.
- [ ] Временные файлы не в git (`var/` в `.gitignore`).
- [ ] Сводка находок из `ruff_full.json` перенесена в «Known facts»; сам JSON
  остаётся в `var/`.

## Definition of done

- [ ] Код изменён только в scope; инвариант держится.
- [ ] `just ci` зелёный; `docker compose build` успешен или откат на 3.12
  записан в решении.
- [ ] Отклонения от правил перечислены в `pyproject.toml` с комментарием и в
  `.agents/decisions/`.
- [ ] `README.md`, `INSTALLATION.md`, `AGENTS.md` описывают только uv/just.
- [ ] Нет `- [0]`; нет временных файлов в `.agents/plans`.
- [ ] `CHANGELOG.md` содержит строки за день изменения.

## Known facts

Снимок 2026-09-06, до правок.

- Код: 63 файла `src/**/*.py`, 28 файлов `tests/*.py`, 91 файл всего под
  линтом. Пакет называется `src` (`packages = ["src"]`, `abt = "src.bot:_cli"`).
- Текущий `pyproject.toml`: `requires-python >=3.11`, `ruff>=0.6` (стоит
  0.15.18), `mypy>=1.10` (стоит 2.1.0), `pyright`, `bandit`, `pip-audit`,
  `pytest`, `pytest-asyncio` в `optional-dependencies.dev`; ruff `select`
  из 15 наборов, `pydocstyle = pep257`, `target-version = "py311"`,
  `line-length = 100`; mypy `ignore_missing_imports = true`,
  `disallow_untyped_defs = false`.
- `uv.lock` есть, актуален (`revision = 3`, 91 пакет), маркеры для 3.14 уже
  разрешены; `.venv` — Python 3.14.4, собран pip'ом (uv snapshot снимет 38
  пакетов).
- Docker: `python:3.12-slim`, `pip install -e .`, uv/uvx уже копируются в
  образ для MCP-серверов; `COPY .agents ./.agents` — в образе есть правила и
  планы.
- Новый линт (ruff 0.16.6, full, py311): 298 находок:

  | Rule    | src | tests | Действие                              |
  | ------- | --- | ----- | ------------------------------------- |
  | DOC201  | 74  | 0     | секция `Returns`                      |
  | SLF001  | 55  | 0     | публичные поля гейта                  |
  | ANN401  | 42  | 0     | ignore (SDK/JSON границы)             |
  | DOC501  | 29  | 0     | секция `Raises`                       |
  | BLE001  | 25  | 0     | `exc_info` / `noqa` с причиной        |
  | PT018   | 0   | 20    | раздельные assert                     |
  | TC006   | 7   | 3     | safe fix                              |
  | PERF401 | 7   | 0     | comprehension                         |
  | TRY004  | 5   | 0     | `TypeError`                           |
  | PT011   | 0   | 4     | `match=`                              |
  | A002    | 3   | 0     | rename `input`                        |
  | DOC402  | 3   | 0     | секция `Yields`                       |
  | LOG015  | 3   | 0     | модульный логгер                      |
  | TRY300  | 2   | 1     | `else`                                |
  | PLW0108 | 0   | 3     | inline                                |
  | N818    | 2   | 0     | `…Error`                              |
  | S101, DTZ006, FURB162, FURB110, G201, T201, RET504, RUF100, TRY203, PLE2502 | 1 каждое | — | по карте |

- Файлы с наибольшим числом находок: `codex_agent.py` 40, `claude_agent.py` 32,
  `interactions/ask_user_question.py` 25, `interactions/plan_mode.py` 23,
  `pi_agent.py` 19, `config/__init__.py` 15, `interactions/permission_prompt.py` 14,
  `task_runner.py` 12.
- `D` с `convention = "google"` на текущих докстрингах: 0 находок.
- С `target-version = "py314"` добавляются `TC001` 60, `TC003` 62, `TC002` 26
  (unsafe-фиксы), `UP037` 20 и `UP043` 3 (safe).
- Существующих `# noqa`: 2 (`ARG002` в тесте — теперь unused, `S608` в тесте);
  `# type: ignore[...]`: 20, все с кодом; `# nosec`: как минимум 1
  (`uploads.py:37`).
- `from __future__ import annotations`: 10 файлов.
- Пропущенные stubs: `yaml` → `types-PyYAML`, `croniter` → `types-croniter`.
- Кириллица есть только в тестах (4 файла) — `RUF001`–`RUF003` в ignore
  оправданы.

## Open questions

| Question | Why it blocks | Options | Recommendation | Status |
| -------- | ------------- | ------- | -------------- | ------ |
| Python 3.14 или остаёмся на 3.12? | Меняет `requires-python`, Docker-базу, набор `UP`/`TC` находок | (a) 3.14 везде; (b) `>=3.12`, образ 3.12 | (a): локально уже 3.14 и зелёно, правила для новых проектов — 3.14; откат по провалу `docker build` | decided: (a), 2026-09-06 |
| `SLF001` в `interactions/*`: переименовать поля гейта или per-file-ignore? | 55 правок в 5 файлах против одной строки конфига | (a) убрать `_` у 11 полей/методов гейта; (b) `"src/infra/interactions/*.py" = ["SLF001"]` с причиной «friend-модули гейта» | (a): поле, которое читают четыре модуля, не приватное; (b) допустимо, если не хочется трогать гейт | decided: (a), 2026-09-06 |
| `TC001`–`TC003` при 3.14: ignore или 148 переносов под `TYPE_CHECKING`? | Только unsafe-фиксы, которые агент не применяет; dataclass/pydantic читают аннотации | (a) ignore с причиной; (b) переносить руками файл за файлом | decided: (a), 2026-09-06 |
| `just` как точка входа — нужен? | Правила ссылаются на `just lint`; в проекте `just` нет | (a) добавить `justfile`; (b) только команды `uv run --locked …` в `AGENTS.md` | (a): как в brain-agent | decided: (a), 2026-09-06 |
| `DOC201`/`DOC501` — писать 106 секций сейчас или отдельным планом? | Самая трудоёмкая часть, ~10 файлов с плотной логикой | (a) в этом плане, шаг 6; (b) временно не выбирать `DOC201/DOC501/DOC402`, отдельный план | (a): иначе линт не full и появится второй план про то же | decided: (a), 2026-09-06 |
| Как развёрнут сервер: Docker или bare `.venv`? | Определяет шаг Deploy и текст `INSTALLATION.md` | (a) только Docker; (b) есть bare-установки | не знаю; `DEPLOY.md` не читал | assumed: Docker; bare-установка описана в Deploy на всякий случай |
| `BLE001`: чинить 25 мест или ignore на весь проект? | Правила выбирают `BLE`; в боте `except Exception` намеренный | (a) `exc_info`/`noqa` по месту; (b) `BLE001` в ignore с причиной | (a): каждое место получает либо лог с трассой, либо объяснение | decided: (a), 2026-09-06 |
