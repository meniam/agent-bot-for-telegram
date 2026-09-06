# Decision Log

Index of `.agents/decisions/`. Rules: `../rules/decisions.md`.
Решения до 2026-09-06 — в таблицах «Decision log» планов `../plans/202606*`.

- 2026-09-06 — [Журналы ведутся по общим правилам из `.agents/rules`](20260906_1247_journals_follow_shared_rules.md),
  revised by [Старые планы переименованы в схему `YYYYMMDD_HHMM_name`](20260906_1310_old_plans_renamed_to_timestamp_scheme.md)
- 2026-09-06 — [Старые планы переименованы в схему `YYYYMMDD_HHMM_name`](20260906_1310_old_plans_renamed_to_timestamp_scheme.md),
  revises [Журналы ведутся по общим правилам из `.agents/rules`](20260906_1247_journals_follow_shared_rules.md)
- 2026-09-06 — [Проект собирается uv на Python 3.14, проверки идут через `just`](20260906_1323_project_runs_on_uv_with_python_314.md)
- 2026-09-06 — [Ruff в полном наборе с четырьмя адаптациями под проект](20260906_1324_ruff_full_tier_with_project_adaptations.md)
- 2026-09-06 — [Состояние гейта, которое читают модули `interactions`, публичное](20260906_1325_gate_state_read_by_interactions_is_public.md)

## Open questions

- `just audit` (2026-09-06) нашёл CVE в запиненных зависимостях: aiohttp 3.14.1 (PYSEC-2026-3546/3547 → 3.14.2), cryptography 49.0.0 (PYSEC-2026-3552 → 50.0.0), mcp 1.28.0 (PYSEC-2026-3483 → 1.28.1); апгрейд — отдельным коммитом через `uv lock --upgrade-package`.
- Как развёрнут сервер: только Docker или есть установка в `.venv` без контейнера? От этого зависит, нужен ли на сервере `uv`.
