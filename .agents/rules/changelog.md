# Changelog Rules

How agents record what changed in the project. The changelog answers "what
changed and when", the plan "how", the decision "why", the learning "what we
learned". The changelog reader is whoever updates the project or looks for
the day something broke.

## Location

- One file `CHANGELOG.md` in the project root. The unit is a day, so a file
  per record and `_TOC.md` are not needed.
- **No releases and no `## Unreleased`.** A release ritual assumes someone
  deploys and decides when a batch is cut. That decision is not made here, so
  a permanent `Unreleased` would only grow and the changelog would stop
  answering "when".
- The heading is the date of the change, newest on top:
  `## 5 сентября 2026, Суббота` — day, month in the genitive, year, then the
  weekday capitalised. The weekday is there so a reader recognises the day
  without a calendar.
- Inside a day, newest action on top. Reading the file top to bottom means
  walking back in time, and the last thing done is the first thing seen.

## What Is Recorded

A change visible outside the code: behaviour, API, DB schema, data format,
config, environment variables, commands, deploy order, dependencies with
incompatibilities.

Not recorded: refactoring with no behaviour change, tests, documentation,
plans and journals, text and typo edits, internal renames.

Test: "after the update a user or operator will notice that …" completes
without guessing. If it does not, it is not in the changelog.

## When

- A line is added under today's date in the same commit as the change. If
  today has no heading yet, it is created at the top of the file.
- A plan that reaches `done` leaves at least one line, or an explicit note in
  the plan that nothing visible outside changed.

## Structure

The type of change is a prefix on the line, not a section. Sections split one
day's work into blocks that have to be read in parallel; a prefix keeps the
day in one list and in one order.

```markdown
# Changelog

## 5 сентября 2026, Суббота

- Изменено: API возвращает московское время без смещения:
  `2026-06-25T12:05:00` вместо `2026-06-25T09:05:00+00:00`
  ([решение](.agents/decisions/20260831_1554_time_is_moscow_without_zone.md)).
- Добавлено: поиск по транскриптам через `/search`
  ([план](.agents/plans/20260901_1200_search.md)).

## 1 сентября 2026, Вторник

- Удалено: таблица `meta`, настройки переехали в `config.yml`.
- Исправлено: индекс `transcripts_date_idx` снова используется фильтром по дате.
- Безопасность: из `docs/servers/README.md` убран лежавший открытым текстом
  токен Cloudflare — вместо него плейсхолдер.
```

Prefixes: `Добавлено`, `Изменено`, `Устарело`, `Удалено`, `Исправлено`,
`Безопасность`. One prefix per line, capitalised, colon after it.

Change line:

- One line, one change, phrased as the result: "Индекс снова используется",
  not "Починили индекс".
- Written for the reader outside: what it is now, not what was done in code.
- An incompatible change starts with `**Breaking.**` after the prefix and says
  what to do: "Изменено: **Breaking.** ключи переехали в `public.api_access`,
  перевыпустите старые командой `just keys migrate`".
- Link to the plan or decision at the end of the line, if any.
- A migration with a manual step goes under `Изменено` with the command.

## Relation to Journals

- Plan: one changelog line per change visible outside from the plan's
  "Definition of done".
- Decision: the changelog line links the decision when the change follows
  from it. The decision itself is not retold in the changelog.
- Learning never goes to the changelog: it is not a project change.

## Checklist

1. Line under today's date in the same commit as the change.
2. The change is visible outside the code; otherwise no line.
3. Prefix is one of the six and matches what happened.
4. Breaking is marked and says what to do.
5. Link to the plan or decision, if any.
6. Newest day on top, newest action on top inside the day.
