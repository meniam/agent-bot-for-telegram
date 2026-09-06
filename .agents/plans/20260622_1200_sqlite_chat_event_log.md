# SQLite-логирование событий чата (роль + сессия) и reader

Файл плана: `.agents/plans/20260622_1200_sqlite_chat_event_log.md`
Создан: 2026-06-22. Время в имени восстановлено по git reflog: checkout ветки `feat/sqlite-message-log` (файл переименован из `PLN-0002.md` 2026-09-06).

## PRD / Зачем мы это делаем
### Проблема
Per-chat аудит пишется только в текстовый ротируемый файл
`<logs_dir>/<bot>/<chat_id>.log` через `BotLogs.for_chat`. По нему неудобно
делать выборки (по времени, роли, чату), и нет машинно-читаемой
структуры «кто что написал».

### Цель
Для каждого chat_id есть собственный SQLite-файл, в который зеркалятся все
события per-chat-логгера, и у каждой записи есть **роль автора**:
`user` / `bot` / `tool` (для tool — какой инструмент), а всё остальное —
`system`; а также **id сессии**, в рамках которой произошло событие.
Плюс провайдер-независимая reader-функция чтения этого лога: по сессии, по
интервалу времени, с лимитом; без интервала — последние N сообщений.
Потребитель (команда/tool/API) подключается позже отдельной задачей.

### Решения (зафиксированы в диалоге)
- **Роль обязательна**: важно понимать, кто написал сообщение —
  `user`, `bot`, `tool` (Read/Bash/…). Непомеченные внутренние события
  (permission-промпты, plan, ошибки, заголовки сессий) пишутся как
  `system`.
- **Сессия**: у каждой записи — `session_id` текущей сессии чата на момент
  события (может быть NULL, если сессии ещё нет). Метаданные сессии
  (title и т.д.) денормализуются в отдельную таблицу `sessions` в той же
  .db (ленивый UPSERT), чтобы файл был самодостаточен (JOIN с заголовками).
  Канон сессий остаётся в JSON `SessionStore`; таблица `sessions` — её
  read-only снимок, eventually-consistent (title подтягивается с лагом).
- **Scope**: все события чата (как сейчас в `<chat_id>.log`), но с
  проставленной ролью и сессией.
- **Хранение/включение**: всегда включено, когда задан `logs_dir`; файл
  `<logs_dir>/messages/<chat_id>.db`. `logs_dir` задаётся индивидуально
  для каждого бота → сегмент `<bot>` в пути БД не нужен, коллизий нет.
  Отдельного конфиг-флага не добавляем.
- **Реализация**: собственный `logging.Handler`, подключённый в
  `BotLogs.for_chat`; роль приходит через `extra={"role": ...}` на
  ключевых call-site (непомеченные → `system`). Сессия резолвится в момент
  записи через инъектированный в `BotLogs` резолвер
  (`SessionStore.current(chat_id) -> Session`): хендлер UPSERT-ит строку в
  `sessions` и пишет `messages.session_id = session.id` — call-site для
  сессии не трогаем. Конфиг не трогаем.

### Требования
- REQ-1: На каждый chat_id — отдельный файл БД
  `<logs_dir>/messages/<chat_id>.db`, создаётся лениво при первом событии;
  каталог `messages/` создаётся при первой записи.
- REQ-2: В .db две таблицы. `messages`:
  `id, ts (REAL), created_at (TEXT), session_id (TEXT NULL),
  role (TEXT NOT NULL), tool (TEXT NULL), level (TEXT), message (TEXT)`;
  индексы по `ts`, `(role, ts)`, `(session_id, ts)`. `sessions`:
  `id (TEXT PK), title (TEXT), auto_titled (INT), created_at (REAL),
  last_used (REAL), updated_at (REAL)`.
- REQ-3: Каждое событие per-chat-логгера попадает в БД. Роль берётся из
  `record.role` (через `extra`); если её нет — `system`. Для `role='tool'`
  имя инструмента кладётся в `tool` (из `record.tool`).
- REQ-3b: В момент emit резолвер `SessionStore.current(chat_id)` (инъектирован
  в `BotLogs`) возвращает `Session`. Хендлер UPSERT-ит её в `sessions`
  (по `id`, поля title/auto_titled/created_at/last_used + updated_at) и
  пишет `messages.session_id = session.id`. Нет резолвера/сессии →
  `session_id` NULL, UPSERT не делается.
- REQ-4: Ключевые call-site помечаются ролью:
  user — текст/транскрипт/загрузки; bot — финальный ответ/доставка
  файлов/опросник; tool — события `ToolStatusMirror.handle` (с `tool_name`).
- REQ-5: Наследует поведение `BotLogs`: при `base_dir is None`
  (console-only) БД не создаётся; при LRU-вытеснении чата SQLite-соединение
  закрывается вместе с остальными хендлерами.
- REQ-6: Сбой записи в SQLite не ломает обработку — ошибки гасятся через
  `Handler.handleError`. `.log` продолжает писаться независимо.
- REQ-7: Reader-функция `query_messages` читает `messages.db` заданного
  чата (по пути) и возвращает сообщения. **Провайдер-независима** — чистая
  функция над SQLite, не трогает агент-бэкенды и Telegram. User/agent-facing
  потребитель — вне этого плана.
- REQ-8: Параметры `query_messages` (все опциональны): `session_id` —
  фильтр по сессии; `since`/`until` (epoch/`datetime`) — интервал по `ts`;
  `limit` — сколько вернуть (дефолт `MESSAGES_DEFAULT_LIMIT`, кап
  `MESSAGES_MAX_LIMIT`); `role` — фильтр по роли. Без `since`/`until` →
  последние `limit` сообщений (хронологический порядок). С интервалом →
  сообщения в диапазоне (ASC) с лимитом.
- REQ-9: Чтение из БД — read-only соединение (`mode=ro`, WAL допускает
  конкурентного читателя при пишущем хендлере); отсутствует файл/таблица →
  пустой список, не ошибка.

### Критерии приёмки
- AC-1: После пользовательского текста в БД есть строка `role='user'`,
  `message` содержит текст.
- AC-2: После ответа бота есть строка `role='bot'`.
- AC-3: После события инструмента есть строка `role='tool'`,
  `tool` = имя инструмента (напр. `Read`).
- AC-4: Непомеченное событие (напр. `cl.exception(...)`) пишется с
  `role='system'`.
- AC-5: При наличии текущей сессии `messages.session_id` равен её id, а в
  `sessions` есть строка с этим id и её title; без сессии/резолвера —
  `session_id` NULL.
- AC-6: Существующий `<chat_id>.log` пишется без изменений.
- AC-7: При вытеснении чата из LRU SQLite-соединение закрыто.
- AC-8: `query_messages(db, limit=3)` без интервала → 3 последних сообщения
  в хронологическом порядке.
- AC-9: `query_messages(db, session_id=...)` → только сообщения этой сессии;
  `query_messages(db, since=...)` → только сообщения в диапазоне `ts`.
- AC-10: `query_messages` на несуществующий файл → `[]`, без исключения.

### Объём
- In: новый logging-handler с SQLite-бэкендом; подключение в
  `BotLogs.for_chat`; теги ролей через `extra` на call-site; read-only
  reader-функция `query_messages`; unit-тесты.
- Out: миграция старых `.log` в БД; конфиг-флаги; изменение формата `.log`;
  хранение бинарных вложений; **любой user/agent-facing потребитель чтения**
  (Telegram-команда, LLM-tool, MCP, HTTP — отдельной задачей позже);
  перенос `SessionStore` в SQLite.

## Code Context
- Fact: `BotLogs.for_chat` ([src/infra/logs.py:73](../../src/infra/logs.py#L73))
  создаёт `RotatingFileHandler` на `<chat_id>.log`, держит логгеры в
  bounded LRU; при превышении `_capacity` `_evict` закрывает все хендлеры
  логгера ([logs.py:100](../../src/infra/logs.py#L100)) → новый хендлер
  закроется автоматически, нужен корректный `close()`.
- Fact: при `base_dir is None` `for_chat` возвращает `_NOOP` до создания
  хендлеров ([logs.py:74](../../src/infra/logs.py#L74)) → «нет БД без
  logs_dir» выполняется само.
- Fact: в `bot.py` ([_make_logs](../../src/bot.py#L97)) `base_dir` =
  `Path(cfg.logs_dir) / cfg.name`, т.е. `self._base.parent` = `<logs_dir>`.
  → каталог `messages/` = `self._base.parent / "messages"`, без правок wiring.
- Fact: `bot_logs` создаётся ([bot.py:196](../../src/bot.py#L196)) **раньше**
  `SessionStore` ([bot.py:211](../../src/bot.py#L211)). → резолвер сессии
  нельзя передать в конструктор; инъектируем сеттером после создания
  `sessions`, до старта polling (per-chat логгеры создаются лениво на первом
  сообщении, т.е. уже после инъекции).
- Fact: `SessionStore.current(chat_id) -> Session | None`
  ([session_store.py:112](../../src/infra/session_store.py#L112)) — резолвер;
  `Session` = `(id, title, auto_titled, created_at, last_used)`
  ([session_store.py:36](../../src/infra/session_store.py#L36)). Читает JSON
  чата без in-memory кэша (учесть в рисках про per-emit чтение).
- Fact: ключевые call-site логирования с готовыми данными для роли:
  user — [text.py:45](../../src/handlers/text.py#L45),
  [voice.py:90](../../src/handlers/voice.py#L90),
  uploads ([uploads.py:60](../../src/handlers/uploads.py#L60));
  bot — [agent_reply.py:148/134/145](../../src/ui/agent_reply.py#L148);
  tool — [tool_status.py:222/246/249](../../src/ui/tool_status.py#L222),
  где доступен `tool_name`.
- Fact: `logging` `extra={"role": ..., "tool": ...}` кладёт атрибуты на
  `LogRecord`; ключи `role`/`tool` не зарезервированы → безопасны.
  `record.getMessage()` даёт отрендеренную строку, `record.created` — ts.
- Fact: стдлиб `sqlite3` доступен, внешних зависимостей не нужно. Прецедент
  per-chat стораджа — `SessionStore`/`TaskStore`. Тест-паттерн `tmp_path`
  есть в [tests/test_logs.py](../../tests/test_logs.py).
- Conclusion: один новый модуль-хендлер + ~3 строки в `for_chat` +
  точечные `extra=` на ~6-8 call-site + reader-функция. Остальной код не
  меняется.
- Fact: ни codex, ни pi не имеют плумбинга кастомных/внешних MCP-серверов
  (`get_mcp_status` → `{"mcpServers": []}` в обоих), у pi есть `no_tools`;
  `task_server_factory` прокинут только в `ClaudeAgentBackend`
  ([agent_factory.py:48](../../src/infra/agent_factory.py#L48)). → единый
  LLM-tool «для всех провайдеров» нетривиален; поэтому reader — чистая
  провайдер-независимая функция, а способ её доставки решается отдельно.
- Assumption: per-chat emit идут из одного потока event loop (фоновые
  asyncio-задачи — тот же поток). Для страховки соединение открыть с
  `check_same_thread=False`.
- Unknown: блокирующих нет.

## Architecture Guidance
- Новый файл `src/infra/message_db.py`:
  - Константы ролей: `ROLE_USER="user"`, `ROLE_BOT="bot"`,
    `ROLE_TOOL="tool"`, `ROLE_SYSTEM="system"` (импортируются на call-site,
    чтобы не плодить строковые литералы).
  - `class SqliteChatLogHandler(logging.Handler)`:
    - `__init__(db_path: Path, session_of: Callable[[], Session | None] | None = None)`:
      `sqlite3.connect(db_path, check_same_thread=False)`,
      `PRAGMA journal_mode=WAL`, idempotent `CREATE TABLE IF NOT EXISTS`
      для `messages` и `sessions` + индексы. `session_of` — zero-arg
      резолвер `Session` для этого чата.
    - `emit(record)`: `role = getattr(record, "role", None) or ROLE_SYSTEM`,
      `tool = getattr(record, "tool", None)`. В `try` получить
      `session = self._session_of() if self._session_of else None`; если
      есть — UPSERT в `sessions` (`INSERT ... ON CONFLICT(id) DO UPDATE SET
      title=…, auto_titled=…, last_used=…, updated_at=record.created`) и
      `session_id = session.id`, иначе NULL (сбой резолвера не валит запись).
      `INSERT` в `messages` (ts=`record.created`, created_at=ISO,
      session_id, role, tool, level=`record.levelname`,
      message=`record.getMessage()`) + `commit()`; при исключении —
      `self.handleError(record)` (REQ-6).
    - `close()`: закрыть соединение, затем `super().close()` (REQ-5).
  - Схема:
    ```sql
    CREATE TABLE IF NOT EXISTS sessions (
        id          TEXT PRIMARY KEY,
        title       TEXT,
        auto_titled INTEGER,
        created_at  REAL,
        last_used   REAL,
        updated_at  REAL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         REAL    NOT NULL,
        created_at TEXT    NOT NULL,
        session_id TEXT,
        role       TEXT    NOT NULL,
        tool       TEXT,
        level      TEXT    NOT NULL,
        message    TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_ts         ON messages(ts);
    CREATE INDEX IF NOT EXISTS idx_messages_role_ts    ON messages(role, ts);
    CREATE INDEX IF NOT EXISTS idx_messages_session_ts ON messages(session_id, ts);
    ```
    `messages.session_id` ссылается на `sessions.id` «мягко» (без FK).
- `BotLogs`: добавить поле `_session_resolver: Callable[[int], Session | None] | None`
  (по умолчанию None) и сеттер `set_session_resolver(fn)`.
- В `BotLogs.for_chat` ([logs.py:84](../../src/infra/logs.py#L84)) после
  `RotatingFileHandler`: `msg_dir = self._base.parent / "messages"`,
  `msg_dir.mkdir(parents=True, exist_ok=True)`,
  `session_of = (lambda cid=chat_id: self._session_resolver(cid)) if
  self._session_resolver else None`,
  `log.addHandler(SqliteChatLogHandler(msg_dir / f"{chat_id}.db", session_of))`.
- В `bot.py` ([run_bot](../../src/bot.py#L211)) сразу после создания
  `sessions` вызвать `bot_logs.set_session_resolver(sessions.current)`.
- Теги ролей на call-site через `extra` (текст лога не меняем, только
  добавляем `extra=`):
  - user: `cl.info("user: %s", text, extra={"role": ROLE_USER})` и т.п. в
    `text.py` (обычный текст, plan-armed, plan-rejected), `voice.py`
    (`voice:` метаданные и `transcript:`), `uploads.py` (сохранённые файлы).
  - bot: `agent_reply.py` — финальный ответ, file delivery, questionnaire.
  - tool: `tool_status.py` `handle` —
    `cl.info("hook %s: %s", ..., extra={"role": ROLE_TOOL, "tool": tool_name})`
    во всех трёх ветках pre/post/«готово».
- Call-site, не относящиеся к user/bot/tool, **не трогаем** — они упадут в
  `system` автоматически.
- Не плодить отдельный SessionStore-подобный стор с ручными вызовами —
  переиспользуем logging-инфраструктуру.

### Phase 2: чтение (reader-функция)
- Reader в `src/infra/message_db.py` (рядом с хендлером, общая схема):
  `MESSAGES_DEFAULT_LIMIT`/`MESSAGES_MAX_LIMIT` + функция
  `query_messages(db_path, *, session_id=None, since=None, until=None,
  limit=DEFAULT, role=None) -> list[dict]`:
  - открыть read-only: `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`;
    файла/таблицы нет → `[]`.
  - WHERE по `session_id`/`role`/`ts BETWEEN`; без интервала —
    `ORDER BY ts DESC LIMIT n`, затем развернуть в хронологический порядок;
    с интервалом — `ORDER BY ts ASC LIMIT n`.
  - вернуть поля `ts, created_at, role, tool, session_id, message`
    (+ `title` через `LEFT JOIN sessions`), `limit = min(limit, MAX)`.
  - `since/until` принимать как epoch/`datetime` (без парсинга строк — это
    забота будущего потребителя).
- Никаких хендлеров, регистраций, bot command list, i18n, агент-бэкендов,
  `agent_factory`, MCP — **не трогаем**. Reader — чистая функция с тестами.

## Affected Contracts
- API: не меняется. Новой команды/tool нет — только внутренняя
  reader-функция `query_messages`. Агент-бэкенды/`agent_factory`/MCP — без
  изменений.
- Data/schema: новый каталог `<logs_dir>/messages/` + файлы `<chat_id>.db`
  (таблицы `messages` + `sessions`). `sessions` — денормализованный снимок
  JSON-стора, не источник правды. WAL даёт сайдкары `*.db-wal`/`-shm` —
  учесть в бэкапах/`.gitignore`.
- Внутренний контракт: `BotLogs` получает новый сеттер
  `set_session_resolver` (необязательный; без него `session_id`=NULL) —
  обратносовместимо для тестов и task-runner, которые `BotLogs` уже
  используют.
- Permissions: не меняются.
- Config/feature flags: не меняются (флага нет).
- Events/queues/integrations: не меняются (call-site лишь получают `extra`).

## Phases and Tasks
### Phase 1: SQLite-логирование (запись)
- [ ] [REQ-2, REQ-3, REQ-3b, REQ-6] Создать `src/infra/message_db.py`:
  константы ролей + `SqliteChatLogHandler` (connect+WAL, схемы `sessions` и
  `messages` + 3 индекса, `emit` с role-fallback `system`, `tool`, UPSERT
  сессии и `session_id` через `session_of`, `handleError`, `close`) →
  Verify: unit-тест — хендлер на `tmp/x.db` с
  `session_of=lambda: Session("sess-1","Title",False,1.0,2.0)`; emit без
  `extra` → в `messages` строка `role='system'`, `session_id='sess-1'`, в
  `sessions` строка `id='sess-1'`, `title='Title'`; emit с
  `extra={"role":"tool","tool":"Read"}` → `role='tool'`, `tool='Read'`.
- [ ] [REQ-3b] Тест UPSERT-идемпотентности: два emit с одной сессией, но
  разным title (эмулируя фоновое переименование) → Verify: в `sessions`
  одна строка с последним title, в `messages` две строки.
- [ ] [REQ-1, REQ-3b, REQ-5] В `BotLogs` добавить
  `_session_resolver`/`set_session_resolver`; подключить хендлер в
  `for_chat` (`msg_dir = self._base.parent / "messages"`, прокинуть
  `session_of`) → Verify: `logs=BotLogs(base_dir=tmp/"bot")`,
  `logs.set_session_resolver(lambda cid: Session("s"+str(cid),"t",False,0,0))`,
  `logs.for_chat(42).info("hi")`; файл `tmp/messages/42.db` существует, в
  `messages` строка `session_id='s42'`, в `sessions` строка `id='s42'`.
- [ ] [REQ-3b] Прокинуть резолвер в `bot.py`: после создания `sessions`
  вызвать `bot_logs.set_session_resolver(sessions.current)` → Verify:
  `ruff`/`mypy` чисто; ручной прогон — в `messages.db` активного чата
  `session_id` совпадает с текущей сессией, в `sessions` есть её title.
- [ ] [REQ-4, AC-1] Пометить user-события (`text.py`, `voice.py`,
  `uploads.py`) `extra={"role": ROLE_USER}` → Verify: эмулировать вызов /
  юнит на хендлере с этим `extra` → строка `role='user'` с текстом.
- [ ] [REQ-4, AC-2] Пометить bot-события в `agent_reply.py` (ответ,
  delivery, questionnaire) `extra={"role": ROLE_BOT}` → Verify: строка
  `role='bot'`.
- [ ] [REQ-4, AC-3] Пометить tool-события в `tool_status.py.handle`
  `extra={"role": ROLE_TOOL, "tool": tool_name}` (3 ветки) → Verify:
  строка `role='tool'`, `tool` = имя инструмента.
- [ ] [REQ-5, AC-7] Тест закрытия соединения при LRU-eviction (`capacity=1`)
  → Verify: соединение вытесненного чата закрыто (повторный доступ к conn
  бросает `sqlite3.ProgrammingError`), `log.handlers == []`.
- [ ] [REQ-5] Тест console-only: `BotLogs(base_dir=None).for_chat(1)` не
  создаёт `.db` → Verify: каталога `messages/` нет, логгер — `_NOOP`.

### Phase 2: чтение (reader-функция)
- [ ] [REQ-7, REQ-8, REQ-9, AC-8, AC-9, AC-10] Добавить в `message_db.py`
  reader `query_messages(db_path, *, session_id, since, until, limit, role)`
  (read-only `mode=ro`, фильтры, latest-vs-interval сортировка, кап лимита,
  `LEFT JOIN sessions`) + константы
  `MESSAGES_DEFAULT_LIMIT`/`MESSAGES_MAX_LIMIT` → Verify: unit на заранее
  наполненной .db — без интервала `limit=2` отдаёт 2 последних в
  хронологическом порядке; `session_id=` фильтрует; `since=` по `ts`
  фильтрует; несуществующий файл → `[]`.

## Done When
- [ ] Для активного чата создаётся `<logs_dir>/messages/<chat_id>.db`;
  события user/bot/tool помечены ролью, прочие — `system`; у записей есть
  `session_id`, а в таблице `sessions` — соответствующие строки с title.
- [ ] `query_messages` отдаёт сообщения чата из его `messages.db`: последние
  N без интервала, с фильтрами `session_id`/`since`/`until`/`limit`/`role`;
  несуществующий файл → `[]`.
- [ ] Существующее логирование `.log` не сломано (старые тесты `test_logs.py`
  зелёные).
- [ ] `pytest -q` (включая новые тесты) проходит; `ruff check src tests` и
  `mypy src tests --strict` чисто для нового модуля, reader и правок
  call-site.

## Risks / Unknowns
- Risk: блокирующий `commit()` на каждый emit тормозит event loop при
  высокой частоте логов → WAL + быстрый single-row insert; при проблемах
  батчить. На объёмах чат-событий приемлемо (как текущий синхронный file-IO).
- Risk: рост числа открытых SQLite-соединений с числом чатов → ограничено
  тем же LRU `chat_logger_capacity` (по умолчанию 256), что и .log-дескрипторы.
- Risk: многопоточный доступ к соединению (фоновые задачи) →
  `check_same_thread=False`; emit короткий, сериализуется GIL; при реальных
  гонках добавить `threading.Lock` в хендлер.
- Risk: забыть `extra` на новом call-site → событие молча станет `system`
  (не теряется, но без точной роли). Митигировать константами ролей и
  тестами на ключевые точки.
- Risk: `SessionStore.current` читает JSON чата на каждый emit (нет
  in-memory кэша) → лишние disk-read + UPSERT на каждую строку лога. На
  объёмах чат-событий приемлемо (маленький файл, OS page cache). При
  проблемах — кэшировать `Session` в хендлере с инвалидацией по id, либо
  резолвить из in-memory состояния агента.
- Risk: `sessions` eventually-consistent — title генерится фоном после
  создания сессии ([agent_reply.py:67](../../src/ui/agent_reply.py#L67)),
  поэтому в .db он появится/обновится только на следующем emit этой сессии.
  Приемлемо: JSON остаётся каноном, .db — снимок для чтения.
- Risk: импорт `Session` в `message_db.py` (typing) — держать через
  `TYPE_CHECKING`, чтобы не плодить runtime-связь infra↔infra (цикла нет:
  `session_store` не импортирует `logs`).
- Open question: блокирующих нет.
