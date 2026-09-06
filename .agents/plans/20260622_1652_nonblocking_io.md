# Рефакторинг I/O: устранение зависаний subprocess и блокирующего логирования

Plan file: `.agents/plans/20260622_1652_nonblocking_io.md`
Created: 2026-06-22. Time in the name is reconstructed from git: commit `6a874ad` that closed the previous three plans (renamed from `PLN-0006.md` on 2026-09-06).

## PRD / Why We Are Doing This

### Problem
Аудит I/O выявил проблемы двух уровней важности. Бот **однопользовательский**, локальный sqlite WAL → блокировка event loop на sync-I/O измеряется долями миллисекунды и для юзера невидима. Поэтому чиним то, что реально ломается, а косметику откладываем.

**Реальные баги (бот может намертво повиснуть):**
- `pi_agent.py`: `stderr=PIPE` открыт ([pi_agent.py:79](../../src/infra/pi_agent.py#L79)), но дренируется только stdout ([:82](../../src/infra/pi_agent.py#L82)). PI CLI пишет >64 КБ в stderr → pipe переполняется → процесс блокируется на write → **deadlock**.
- `pi_agent.py`: `stdout.readline()` ([pi_agent.py:147](../../src/infra/pi_agent.py#L147)) ограничен дефолтным лимитом StreamReader 64 КБ → длинная строка кидает `LimitOverrunError`, reader-таск **тихо умирает**, pending-futures зависают навсегда.
- `pi_agent.py`: `asyncio.Queue()` без `maxsize` ([pi_agent.py:63](../../src/infra/pi_agent.py#L63)) — рост памяти при болтливом CLI и медленном потребителе.

**Частый блокирующий I/O (стоит починить дёшево):**
- `SqliteChatLogHandler.emit()` ([message_db.py:170-205](../../src/infra/message_db.py#L170)) — sync sqlite INSERT+commit на **каждый** лог-вызов из async-контекста. Самый частый I/O в боте.

**Косметика (откладываем — см. раздел «Отложено»):**
- Sync sqlite в `SessionStore`, sync файловый I/O в `TaskStore`, точечные `open/read_bytes` — блокируют loop, но на доли мс при одном юзере. Конвертация в async = широкая рябь по коду и риск регрессий ради нулевого практического выигрыша.

### Goal
PI-агент не зависает на больших stderr/длинных строках stdout. Логирование сообщений не блокирует event loop (вынесено в фоновый поток). sqlite настроен на быструю и устойчивую к конкуренции запись (PRAGMA). Поведение и контракты данных не меняются.

### Users / Scenarios
- Конечный пользователь: PI-агент не виснет на больших выводах; бот отзывчив при логировании.
- Разработчик: точечные правки без широкого async-рефакторинга; минимум риска регрессий.

### Requirements
- REQ-1: Дренировать stderr PI-процесса, чтобы исключить deadlock на переполнении pipe.
- REQ-2: Reader stdout PI устойчив к строкам >лимита (не умирает молча; pending-futures получают ошибку или строка читается).
- REQ-3: Очередь событий PI ограничена по размеру и не течёт по памяти.
- REQ-4: Логирование сообщений в sqlite не выполняется на event loop (вынос в фоновый поток через `QueueHandler`/`QueueListener`).
- REQ-5: sqlite настроен: `PRAGMA synchronous=NORMAL` + `PRAGMA busy_timeout` (при уже включённом WAL) для быстрой и устойчивой к конкуренции записи.
- REQ-6 (NFR): Никаких изменений формата данных, схемы sqlite, имён конфигов, публичных контрактов API бота. Без новых внешних зависимостей.

### Acceptance Criteria
- AC-1: При выводе PI CLI >64 КБ в stderr и/или одной строки >лимита stdout бот не зависает; запрос завершается результатом или явной ошибкой; pending-futures всегда резолвятся.
- AC-2: Очередь событий PI ограничена; переполнение не кидает наружу и не течёт по памяти.
- AC-3: Запись лога сообщения не блокирует event loop; строки доходят в sqlite через фоновый listener; при shutdown хвост логов не теряется.
- AC-4: sqlite-коннекты применяют `synchronous=NORMAL` и `busy_timeout`; запись не падает с `SQLITE_BUSY` при конкуренции.
- AC-5 (регрессия): Контент логов сообщений, формат файлов задач, содержимое сессий идентичны до/после; существующие тесты проходят.

### Out of Scope
- ~~Async-конвертация `SessionStore`/`TaskStore`~~ — изначально отложено, затем реализовано по запросу «делать всё» (см. раздел в конце).
- aiosqlite и любая смена БД-драйвера (под капотом то же thread-offload, что и `to_thread`; для одного юзера выигрыша нет). Остаётся out of scope.
- ~~Точечный sync file I/O (`uploads.py`, `pi_agent.py`)~~ — также реализовано.
- Изменение схемы sqlite, формата JSON задач, протокола PI RPC (кроме дренажа stderr).

### Constraints and Dependencies
- Python asyncio; stdlib `logging.handlers.QueueHandler`/`QueueListener` (без новых зависимостей).
- sqlite-коннекты в `SqliteChatLogHandler` создаются с `check_same_thread=False` ([message_db.py:130](../../src/infra/message_db.py#L130)) — совместимо с исполнением в потоке listener'а.
- WAL уже включён ([message_db.py:131](../../src/infra/message_db.py#L131)) — `synchronous=NORMAL` безопасен в этом режиме.
- pytest-asyncio `asyncio_mode = "auto"` ([pyproject.toml:87](../../pyproject.toml#L87)).

## Code Context
- Fact: offload-примитивов нет нигде — `grep to_thread|run_in_executor|aiofiles|aiosqlite src/` пуст.
- Fact: `SqliteChatLogHandler.emit()` — `logging.Handler`, синхронный по контракту logging; зовётся инлайн в потоке вызывающего (event loop при логах из корутин). `for_chat` создаёт хэндлеры лениво ([logs.py](../../src/infra/logs.py)).
- Fact: `connect()` ([message_db.py:123-133](../../src/infra/message_db.py#L123)) ставит WAL, но не `synchronous`/`busy_timeout`. `session_store.connect` переиспользует ту же функцию.
- Fact: PI reader создаёт только stdout-таск ([pi_agent.py:82](../../src/infra/pi_agent.py#L82)); stderr=PIPE не читается; `readline()` без явного лимита; `Queue()` без maxsize.
- Fact: тест-харнесс: `test_agent_backends.py` (PI transport), `test_message_db.py`, `test_logs.py`.
- Conclusion: `emit()` нельзя «заавэйтить» (logging sync) — правильный фикс `QueueHandler` на хэндлере + общий `QueueListener` с фоновым потоком, держащим реальный `SqliteChatLogHandler`. Это stdlib best practice для медленных sinks, не `to_thread`/aiosqlite.
- Assumption: PI CLI может писать диагностику в stderr (иначе PIPE не открывали бы) — `stderr=STDOUT` смешает её в JSONL и сломает парсер, поэтому нужен отдельный дренаж-таск, а не слияние.
- Unknown: реальный объём stderr и макс. длина строки stdout PI — выбрать лимит readline консервативно (напр. 4 МБ) и логировать переполнение.

## Architecture Guidance
- **PI subprocess** ([pi_agent.py](../../src/infra/pi_agent.py)): добавить `_read_stderr()` таск рядом с `_read_stdout()` ([:82](../../src/infra/pi_agent.py#L82)), читать stderr в ограниченный буфер/лог; останавливать/awaitить в `close()` ([:104-108](../../src/infra/pi_agent.py#L104)) аналогично reader_task. Поднять лимит StreamReader через `create_subprocess_exec(..., limit=...)` и/или ловить `LimitOverrunError`/`ValueError` в цикле readline без убийства таска. Очередь — `asyncio.Queue(maxsize=N)` + при `QueueFull` drop oldest + `log.warning`.
- **Логирование** ([logs.py](../../src/infra/logs.py)): обернуть создаваемый `SqliteChatLogHandler` в `QueueHandler`; один общий `QueueListener` (фоновый поток), старт при инициализации логов, `listener.stop()` при shutdown бота ([bot.py](../../src/bot.py), рядом с `bot.session.close()`). Реальный sqlite-I/O уходит в поток listener'а — emit на hot-path становится мгновенным put в очередь.
- **sqlite PRAGMA** ([message_db.py:connect](../../src/infra/message_db.py#L123)): добавить `PRAGMA synchronous=NORMAL` и `PRAGMA busy_timeout=5000` сразу после WAL. Применяется ко всем коннектам (логи + сессии), т.к. оба идут через `connect`.
- **Anti-patterns:** не смешивать stderr в stdout JSONL; не менять схему/формат; не тащить aiosqlite.

## Affected Contracts
- API (бот/команды): не меняется.
- Data/schema (sqlite messages/sessions, JSON задач): не меняется.
- Permissions: не меняется.
- Events/queues/integrations: внутренняя `asyncio.Queue` PI получает `maxsize`; протокол PI RPC по проводу не меняется (stderr теперь дренируется).
- Configuration/feature flags: не меняется (лимиты readline/queue, busy_timeout — внутренние константы).

## Phases and Tasks

### Phase 1: Баги PI subprocess (критический путь — реальные зависания)
- [x] [REQ-1, AC-1] Добавить `_read_stderr()` таск в `PiRpcTransport.start()` рядом со stdout-reader ([pi_agent.py:82](../../src/infra/pi_agent.py#L82)); читать stderr в ограниченный буфер и логировать `log.warning`. Останавливать/awaitить таск в `close()` ([pi_agent.py:104-108](../../src/infra/pi_agent.py#L104)) как reader_task → Verify: новый тест в `test_agent_backends.py` — фейковый процесс пишет >64 КБ в stderr, `request()` завершается без зависания.
- [x] [REQ-2, AC-1] Поднять лимит StreamReader (`create_subprocess_exec(..., limit=4*1024*1024)`) и обернуть `readline()` ([pi_agent.py:147](../../src/infra/pi_agent.py#L147)) в обработку `LimitOverrunError`/`ValueError`: дренировать остаток строки, залогировать, продолжить цикл (не убивать reader) → Verify: тест — строка stdout >64 КБ не убивает reader; следующий валидный response доходит до future.
- [x] [REQ-3, AC-2] Заменить `asyncio.Queue()` ([pi_agent.py:63](../../src/infra/pi_agent.py#L63)) на `asyncio.Queue(maxsize=N)`; в `put_nowait` ([:166](../../src/infra/pi_agent.py#L166)) ловить `QueueFull` → drop oldest + `log.warning` → Verify: тест — переполнение очереди не кидает наружу; счётчик элементов ограничен N.

### Phase 2: Логирование sqlite вне event loop
- [x] [REQ-4, AC-3] В `logs.py` обернуть `SqliteChatLogHandler` в `QueueHandler`; создать общий `QueueListener` (фоновый поток), стартовать при инициализации логов и `listener.stop()` при shutdown бота ([bot.py](../../src/bot.py), точка cleanup рядом с `bot.session.close()`) → Verify: `test_logs.py`/`test_message_db.py` — запись лога не блокирует, строки доходят в sqlite после flush listener'а; тест на остановку listener'а без потери буфера.

### Phase 3: sqlite PRAGMA-тюнинг
- [x] [REQ-5, AC-4] В `connect()` ([message_db.py:123-133](../../src/infra/message_db.py#L123)) добавить `PRAGMA synchronous=NORMAL` и `PRAGMA busy_timeout=5000` после WAL → Verify: `test_message_db.py`/`test_session_store.py` — PRAGMA применены (`PRAGMA synchronous`→1, `busy_timeout`→5000); запись не падает с `SQLITE_BUSY` под конкуренцией.

### Phase 4: Финальная проверка
- [x] [AC-5] Прогон всего набора тестов → Verify: `pytest` зелёный; форматы данных/схема не изменились.

## Done When
- [x] PI-агент не зависает на больших stderr/длинных строках stdout; pending-futures всегда резолвятся (AC-1).
- [x] Очередь событий PI ограничена и не течёт (AC-2).
- [x] Логи сообщений пишутся в sqlite из фонового потока, не на event loop; хвост не теряется при shutdown (AC-3).
- [x] sqlite применяет `synchronous=NORMAL` + `busy_timeout` (AC-4).
- [x] Существующее поведение и форматы данных не изменились; `pytest` зелёный (AC-5).

## Risks / Unknowns
- Risk: `stderr=STDOUT` сломал бы JSONL-парсер → используем отдельный дренаж-таск, не слияние.
- Risk: `QueueListener` не остановлен при shutdown → потеря хвоста логов; гарантировать `listener.stop()` в cleanup бота.
- Risk: `synchronous=NORMAL` при сбое питания может откатить последнюю транзакцию (но не повредить БД) — приемлемо для логов/сессий бота; durability критичных данных не требуется.
- Open question: вынести лимиты readline/queue PI в конфиг или оставить константами? (по умолчанию — константы).

## Изначально отложенное — реализовано по запросу «делать всё»
Исходно эти пункты были вынесены из scope (для одного юзера блокировка loop невидима). По явному решению сделаны в этом же заходе. `to_thread`, НЕ aiosqlite. Все call sites переведены на `await`; `mypy --strict`, `pyright`, `ruff`, `pytest` (314) — зелёные.

- [x] **`SessionStore` → async**: публичные методы `async def` + `asyncio.to_thread`; `get_by_id` через индексный `WHERE id=?` вместо O(N)-скана ([session_store.py](../../src/infra/session_store.py)). Рябь: `agent_base.py`, `claude_agent.py`, `agent_types.py` (Protocol), `handlers/sessions.py`. Добавлен sync-вход `current_sync` для резолвера лог-хендлера (зовётся в фоновом потоке listener'а — await там невозможен).
- [x] **`TaskStore` → async**: write-методы — I/O через `to_thread` (семантика `_lock` сохранена); read-методы (`list_all/list_due/list_global/get/list_history`) → `async def` + `to_thread`. Рябь: `task_service.py` (`list`/`get` → async), `task_scheduler.py`, `task_tool.py`, `handlers/tasks.py`.
- [x] **Точечный file I/O**: `uploads.py` (`open`/`close` через `to_thread`), `pi_agent.py` `_extract_images` (`read_bytes` → `to_thread`, метод стал async).
- Замечание на будущее: при переходе на shared долгоживущий коннект добавить `threading.Lock` для сериализации (`check_same_thread=False` разрешает разные потоки, но не одновременный доступ). Сейчас коннекты короткоживущие (per-call) — Lock не нужен.
