# Состояние гейта, которое читают модули `interactions`, публичное

Decided: 2026-09-06
Plan: `.agents/plans/20260906_1300_tooling_follows_python_and_ruff_rules.md`
Revised: —

## Context

`TelegramInteractionGate` держит бота, переводчик, таймаут, реестры ожидающих
запросов. Логика четырёх потоков (`ask_user_question`, `plan_mode`,
`permission_prompt`, `push_notification`) вынесена в модули
`src/infra/interactions/*`, которые получают гейт параметром и читали его
поля с подчёркиванием: 55 срабатываний `SLF001`.

## Decision

Поля и методы гейта, которые читают эти модули, без подчёркивания: `bot`,
`translator` (бывший `_t`), `timeout`, `send_md`, `pending`, `aq`,
`aq_aborted`, `plan_pending`, `chat_log()` (бывший `_cl`), `delete_prompt()`,
`format_request()`. `_chat_logger` остаётся приватным: его читает только
сам гейт. Внешние потребители гейта (`bot.py`, `plan_router`, `context`)
по-прежнему ходят через `can_use_tool`, `on_callback`, `cancel_active_aq`.

## Why

- Поле, которое читают четыре модуля, по факту не приватное; подчёркивание
  врало, а `SLF001` это показал.
- Отвергнуто «per-file-ignore `SLF001` для `interactions/*`»: правило бы
  молчало и о настоящих нарушениях в этих файлах.
- Отвергнуто «передавать нужные поля параметрами»: у потоков 5–8 общих
  полей, сигнатуры раздуются, а реестры (`pending`, `aq`) должны быть
  общими по ссылке.

## Cost

- Публичные имена читаются как API гейта, хотя предназначены только для
  `interactions/*`; это записано в докстринге класса и здесь.
