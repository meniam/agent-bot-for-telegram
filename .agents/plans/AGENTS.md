# Планы

Правила общие — `plans.md`, подключён из корневого `AGENTS.md` папки с проектами.

Проектные уточнения:

- Планы, созданные до перехода на общие правила (2026-09-06), переименованы
  в `YYYYMMDD_HHMM_short_task_name.md`; время в имени восстановлено по git,
  соответствие старым `PLN-000N` — в
  [`../decisions/20260906_1310_old_plans_renamed_to_timestamp_scheme.md`](../decisions/20260906_1310_old_plans_renamed_to_timestamp_scheme.md).
  Новый план — `YYYYMMDD_HHMM_short_task_name.md`, ветка
  `<type>/YYYYMMDD_HHMM_short_task_name`.
- Проверки — команды из раздела «Run and Check» корневого `AGENTS.md`
  (`ruff check`, `mypy --strict`, `pytest -q` и остальные); в «Верификации»
  команда всегда с ожидаемым результатом.
- Временные артефакты — в `var/agents/plans/<planName>`, не здесь.
- Решение, пережившее план, — файл в [`../decisions/`](../decisions/) до
  статуса `done`. Изменение, видимое снаружи, из «Definition of done» —
  строка в `CHANGELOG.md` под датой изменения.
- Ключи заголовка и названия разделов — как в правилах, текст плана —
  по-русски.
