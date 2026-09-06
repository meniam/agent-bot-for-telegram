# Управление запланированными задачами (одноразовые / повторяющиеся, LLM / скрипт)

Файл плана: `.agents/plans/20260622_0441_scheduled_tasks.md`
Создан: 2026-06-22. Время в имени восстановлено по git reflog: checkout ветки `feat/scheduled-tasks` (файл переименован из `PLN-0001.md` 2026-09-06).

## PRD / Зачем мы это делаем

### Проблема
Шлюз сейчас чисто реактивный: ход агента происходит только когда пользователь
Telegram присылает сообщение. Нет способа запланировать работу — ни «напомнить /
отчитаться один раз в момент времени», ни «запускать это каждое утро». Также нет
способа запустить обычный shell/Python-скрипт по расписанию и доставить его вывод
в чат. Hermes (`/Users/eugene/Projects/hermes/cron/`) уже доказал модель:
JSON-хранилище задач, планировщик с тиком 60с, полиморфные расписания
(`once`/`interval`/`cron`) и флаг `no_agent`, переключающий между запуском LLM и
запуском «голого» скрипта. Хотим ту же возможность внутри нашего бота.

### Цель
Владелец бота может создавать, перечислять, редактировать, ставить на паузу /
возобновлять, запускать вручную и удалять запланированные задачи по чатам. Каждая
задача — это либо:
- **одноразовая** (запуск один раз в момент времени / после задержки), либо
  **повторяющаяся** (фиксированный интервал, опционально cron), и
- **LLM** (отправляет промпт в настроенный бэкенд агента и доставляет ответ) либо
  **скрипт** (запускает файл `*.sh`/`*.py` и доставляет его stdout).
Задачи переживают перезапуск процесса, срабатывают из фонового планировщика и
проактивно доставляют вывод в чат-владелец. Фича включается опционально на бот
через конфиг и безопасно отключается.

### Пользователи / Сценарии
- Владелец бота / разрешённый чат: «напомни через 2ч», «каждый день в 09:00
  делай саммари моих заметок», «запускай backup.sh ежечасно и пингуй меня, если
  что-то вывел».
- Старт сценария: пользователь шлёт `/task add ...` → строка задачи сохраняется →
  планировщик срабатывает в нужный момент → вывод доставляется в чат через
  `send_md_to_chat`.

### Требования
- REQ-1: Модель `Task` хранит: `id` (12-символьный hex, иммутабелен после
  создания — используется как компонент пути `history/<task_id>/`, поэтому
  валидируется против traversal), `owner_chat_id` (кто создал, для аудита),
  `scope` (`user` | `global`), `name`, `enabled`, `state`
  (`scheduled` | `paused` | `completed` | `error`), `kind` (`llm` | `script`),
  `schedule` (`once` | `interval` | `cron`), `prompt` (для llm) или `script`
  (для script), `exclusive: bool` (задача мутирует `working_dir` → требует
  эксклюзивного запуска), `repeat` (times/completed), `next_run_at`,
  `last_run_at`, `last_status`, `last_error`, `created_at`. Одноразовая vs
  повторяющаяся кодируется через `schedule.kind` + `repeat`. Для `kind=llm`
  `exclusive` всегда эффективно true.
- REQ-2: Задачи сохраняются на диск как JSON, один файл на чат. Каждый бот задаёт
  свою папку через `tasks_dir` в конфиге (как `sessions_dir`), напр.
  `var/brain/tasks`; `TaskStore` пишет файлы **прямо в `tasks_dir`, без
  добавления `<bot_name>`** (путь уже per-bot). Раскладка:
  `<tasks_dir>/<chat_id>.json` (+ `global.json` для глобальных). Всё под `var/`
  (gitignore). Запись **атомарна и durable**: tempfile → write → `flush` →
  `os.fsync` → `os.replace` → `chmod 0600` (строже, чем `SessionStore`, т.к.
  потерять задачу нельзя). In-process `asyncio.Lock` вокруг read-modify-write.
  Метаданные `updated_at` в файле.
- REQ-2b: Битый JSON не теряется молча: при ошибке парсинга файл переносится в
  `<tasks_dir>/_corrupt/<chat_id>.<ts>.json`, ошибка логируется, работа
  продолжается с пустой структурой (не падаем, как `SessionStore`, но и не
  теряем данные).
- REQ-12: Никакого вала задач после простоя. Состояние — единственный
  `next_run_at` (не очередь пропущенных); история на диске не исполняется.
  Повторяющиеся: grace = период/2, clamp [120с, 2ч]; просрочка > grace →
  fast-forward `next_run_at` вперёд со skip; иначе один догоняющий запуск.
  Одноразовые: grace 120с, иначе → `state=completed` без запуска. Худший случай
  при рестарте — один запуск на задачу, не N.
- REQ-11: История запусков персистится на диск как append-only JSON:
  `<tasks_dir>/history/<task_id>/<ts>.json`. Каждая
  запись: `task_id`, `scope`, `kind`, `started_at`, `finished_at`,
  `duration_ms`, `status` (`ok`|`error`), `exit_code` (скрипты), `output`
  (обрезан до `max_output_chars`), `error`, `delivered_to` (список chat_id).
  Быстрые поля `last_status`/`last_error`/`last_run_at` остаются в записи задачи
  для `/task list` без чтения истории. **Мягкий потолок** `tasks_history_limit`
  (конфиг, дефолт 100): при `append_history` удалять самые старые записи задачи
  сверх лимита (хранить последние N).
- REQ-3: Фоновый asyncio-планировщик тикает с фиксированным интервалом (по
  умолчанию 60с), выбирает задачи к исполнению (`enabled and next_run_at <= now`),
  запускает каждую и сдвигает `next_run_at` для повторяющихся задач до запуска
  (чтобы медленный запуск не сработал повторно). Одноразовые задачи после запуска
  помечаются `state=completed` (`enabled=false`) и остаются в списке (не
  удаляются) — для истории; чистка вручную через `/task rm`.
- REQ-4: Скрипт-задачи запускают валидированный файл `*.sh`/`*.py` из настроенной
  директории скриптов через `asyncio.create_subprocess_exec` с таймаутом; stdout
  захватывается, обрезается и доставляется. Никакой интерполяции строк в shell;
  никакого выхода за пределы директории скриптов (path traversal).
- REQ-5: LLM-задачи запускают ход агента в **выделенной эфемерной сессии**,
  которая не загрязняет живой диалог чата, затем доставляют текст ответа.
- REQ-5b: Доставка зависит от `scope`. `user` → вывод идёт строго в
  `owner_chat_id` создателя. `global` → вывод **broadcast** во все
  `allowed_chat_ids` (минус `blacklist_chat_ids`); LLM-ход выполняется один раз,
  результат рассылается каждому через `send_md_to_chat`. Рассылка устойчива к
  ошибке отдельного чата (одна неудача не срывает остальных).
- REQ-6: Команда `/task` обеспечивает CRUD из Telegram: `add`, `list`, `show`,
  `pause`, `resume`, `run` (запустить сейчас), `rm`. Все пользовательские строки
  идут через `Translator.t`.
- REQ-7: Фича управляется новой опциональной секцией конфига `tasks`
  (`enabled`, `dir`, `tick_interval_sec`, `scripts_dir`, `max_output_chars`,
  `script_timeout_sec`, `allowed_tools`, `history_limit`). Когда `enabled`
  false/отсутствует —
  планировщик не стартует и `/task` отвечает сообщением «отключено». По
  умолчанию — отключено.
- REQ-13: Права LLM-задач задаются в `config.yaml`, секция `tasks.allowed_tools`
  (список имён инструментов). LLM-задачи работают **неинтерактивно**: инструмент
  из списка → разрешить, любой другой → запретить молча (без Telegram-кнопок,
  т.к. в фоне некому нажимать). Если поле не указано → дефолт read-only набор
  `[Read, Glob, Grep, WebFetch]`; явный `[]` → без инструментов (только
  размышление + текст). Это отдельный неинтерактивный путь, не трогает
  интерактивный `gate.can_use_tool` для живых ходов.
- REQ-8: Ошибки планировщика в одной задаче не должны рушить цикл или другие
  боты; каждый запуск задачи изолирован и логируется в per-chat лог.
- REQ-14: Перед запуском задачи планировщик проверяет права владельца:
  `scope=user` → `is_allowed(owner_chat_id)`, `scope=global` →
  `is_admin(owner_chat_id)`. Если доступ отозван — не запускать, поставить на
  паузу (`enabled=false`), записать `last_error=access_revoked`.
- REQ-10: Роль администратора задаётся новым полем `admin_chat_ids`
  (`tuple[int,...]`, секция `access`), fail-closed: пустой/отсутствует → админов
  нет. Только чат из `admin_chat_ids` может: (a) создавать/редактировать/удалять
  `scope=global` задачи и (b) создавать `kind=script` задачи (запуск файлов на
  сервере — чувствительная операция). Обычный пользователь создаёт только
  `kind=llm` `scope=user` задачи и управляет только своими (где
  `owner_chat_id == chat_id`); чужие/глобальные/скриптовые ему не видны и не
  доступны в `/task`. Админ видит и свои, и глобальные.
- REQ-9: Конкурентность сериализуется по `working_dir` (общий ресурс): все
  LLM-задачи и скрипт-задачи с `exclusive=true` берут один глобальный (на бот)
  `asyncio.Lock` и выполняются строго последовательно (одна за раз). Скрипт-задачи
  с `exclusive=false` могут выполняться параллельно друг с другом и с
  эксклюзивными (они не трогают общие файлы). Живые ходы агента пользователя в
  этот lock НЕ входят (вне области; см. Риски).

### Критерии приёмки
- AC-1: Одноразовая скрипт-задача, созданная с задержкой +1м, срабатывает один
  раз, доставляет stdout в чат, затем показывается в `/task list` как
  `completed` (не активна, не удалена).
- AC-2: Повторяющаяся интервальная задача (`every 30m`) сохраняет ненулевой
  `next_run_at` после каждого запуска и `repeat.completed` инкрементируется.
- AC-3: Одноразовая LLM-задача доставляет ответ агента в чат без изменения
  текущей выбранной сессии чата (проверка: список `/sess` и указатель current не
  изменились после запуска задачи).
- AC-4: При `tasks.enabled=false` (или отсутствии секции) бот стартует,
  планировщик не запускается, `/task` возвращает сообщение «отключено».
  Существующие потоки сообщений / голоса / загрузок не изменены.
- AC-5: Падающий скрипт (ненулевой код выхода) ставит `last_status=error`,
  записывает `last_error` и НЕ удаляет повторяющуюся задачу.
- AC-6: Задачи перезагружаются с диска при рестарте и планировщик их
  возобновляет; одноразовая, чьё время прошло в пределах grace (120с), срабатывает
  один раз, иначе → `completed` без запуска; повторяющаяся, просроченная больше
  grace (период/2), перематывается вперёд со skip, а не выстреливает залпом все
  пропущенные запуски (худший случай — один запуск на задачу).
- AC-7: Две LLM-задачи, ставшие due одновременно, выполняются последовательно
  (вторая ждёт `workdir_lock`); две неэксклюзивные скрипт-задачи выполняются
  параллельно (проверка по таймстампам начала/конца в per-chat логе).
- AC-8: Пользователь A не видит задачи пользователя B в `/task list` и не может
  их удалить/изменить. Неадмин получает отказ на `/task add --global ...` и на
  создание `kind=script`. Админ создаёт глобальную/скрипт-задачу, вывод
  глобальной приходит во все `allowed_chat_ids`.

### Вне области
- Цепочки задач (`context_from`), переопределения модели / провайдера на задачу,
  мультиплатформенная доставка, web/CLI UI управления, многоуровневые роли (есть
  только админ vs пользователь), настраиваемые списки целей доставки (только
  `user`→владелец и `global`→broadcast). Поддержка cron включена, но минимальная
  (одно выражение, без UI таймзон).
- Интерактивные запросы прав во время запусков LLM-задач (см. Риски: LLM-задачи
  работают с неинтерактивной политикой прав).

### Ограничения и зависимости
- Должны сохранить fail-closed контроль доступа: `/task` — обычный хендлер и
  наследует `AclMiddleware`; планировщик запускает только задачи, загруженные для
  уже известных чатов, и доставляет только в эти `chat_id`.
- `bot.py` остаётся только wiring; жизненный цикл планировщика стартует/стопается
  там, но логика живёт в `infra/`.
- Новая зависимость `croniter` (маленькая, чистый Python) — cron включён в v1
  (решено), поэтому `croniter` добавляется в `[project] dependencies` и
  `requirements.txt` сразу.
- Python 3.11+, aiogram 3, pydantic 2, строгий mypy/pyright, набор ruff-линтов
  (вкл. `S`, `ANN`, `ASYNC`, `PTH`).

## Контекст кода
- Факт: `run_bot` (`src/bot.py:178-275`) инстанцирует каждую зависимость, строит
  замороженный `BotContext` (`src/handlers/context.py:30-48`), затем запускает
  `await dp.start_polling(bot)` внутри `try/finally`, который вызывает
  `agent.close_all()`. Это единственное место для спавна и отмены фонового
  планировщика.
- Факт: Конфиг использует `NESTED_CONFIG_SECTIONS`
  (`src/config/__init__.py:22-77`) для маппинга YAML-секций → плоские поля
  `BotConfig`, `_resolve_path` (`:263-269`) привязывает относительные пути к
  директории конфига, а `_build` (`:272-370`) делает per-field резолв путей +
  `mkdir` и собирает `payload`. Опциональные фичи управляются nullable-полем
  (например `groq_api_key`, `uploads_dir`). Это точный паттерн для добавления
  секции `tasks`.
- Факт: Протокол `AgentBackend` (`src/infra/agent_types.py:36-85`) даёт
  `ask(chat_id, prompt) -> str`, `new_session`, `switch_session`,
  `current_session`, `list_sessions`. `ask` возобновляет/создаёт *текущую* сессию
  чата (`claude_agent.py:262-272`), так что прямой вызов запустил бы задачу
  внутри живого диалога пользователя — неприемлемо для REQ-5.
- Факт: `SessionStore` (`src/infra/session_store.py`) — JSON-на-чат с атомарным
  `_write`, `create`, `current`, `set_current`, `delete` — шаблон для
  `TaskStore`. Сессии ключуются по `chat_id`; SDK хранит историю по `session_id`
  UUID (`AGENTS.md` «Multi-session per chat»).
- Факт: `send_md_to_chat(bot, chat_id, text)` (`src/ui/markdown.py:338`) —
  существующий путь бот-инициированной доставки (HTML rich message + fallback на
  plain-text). `ctx.bot` — aiogram `Bot`.
- Факт: Хендлеры регистрируются через `register_all`
  (`src/handlers/__init__.py:24-41`); точные фильтры `Command(...)` должны идти до
  жадного хендлера `text`. Хендлеры получают `ctx`, `cl` (per-chat логгер),
  `chat_id` от `AclMiddleware`.
- Вывод: Скрипт-задачи самодостаточны и низкорисковы; LLM-задачи требуют новой
  точки входа в бэкенд, запускающей *эфемерную* сессию, привязанную к чату, но без
  мутации `current`. Разбить работу так, чтобы скрипт-задачи приземлились первыми.
- Допущение: Один процесс владеет JSON-файлами; in-process `asyncio.Lock` вокруг
  записей в файлы задач достаточен (без мульти-процессного локинга, как fcntl у
  Hermes). Верно для текущего однопроцессного, мультиботового дизайна.
- Решено: политика прав LLM-задач — `config.yaml` `tasks.allowed_tools`,
  неинтерактивный allowlist (REQ-13). Все открытые вопросы по фиче закрыты.

## Архитектурные указания
- **Новые модули (все в `src/infra/`):**
  - `task_types.py` — pydantic-модели `TaskSchedule`, `TaskRepeat`, `Task` и
    хелперы `parse_schedule(text) -> TaskSchedule` и
    `compute_next_run(schedule, after) -> datetime | None` (портировать
    полиморфную логику `once`/`interval`/`cron` из `hermes/cron/jobs.py:209-420`).
  - `task_store.py` — `TaskStore(base_dir, ...)`, JSON-на-чат, методы: `add`,
    `get`, `list_all(chat_id)`, `list_due(now)` (скан всех файлов чатов +
    `global.json`, дёшево), `update`, `remove`, `append_history(task, record)`,
    `list_history(task_id)`, повторяя форму `SessionStore`. **Атомик-хелпер**
    `_atomic_write(path, data)`: tempfile в той же директории → `json.dump` →
    `flush` → `os.fsync` → `os.replace` → `chmod 0600` (портировать
    `save_jobs` hermes [cron/jobs.py:474]). `asyncio.Lock` вокруг
    read-modify-write. **Битый JSON** (`_read`): переносить в
    `_corrupt/<chat_id>.<ts>.json`, логировать, вернуть пустую структуру.
    **Scope-хранение:** `scope=user` → `<tasks_dir>/<chat_id>.json`;
    `scope=global` → `<tasks_dir>/global.json` (без `<bot_name>` — `tasks_dir`
    уже per-bot из конфига). `list_due` сканирует оба источника. `list_all`
    принимает `chat_id` (свои `user`-задачи) или флаг `include_global` для админа.
    **Traversal-guard** `_history_dir(task_id)`: валидировать `task_id` как
    12-hex, отклонять `..`/separators (портировать `_job_output_dir` hermes
    [cron/jobs.py:54]).
  - `task_runner.py` — `TaskRunner` держит ссылки на `bot`, `agent`,
    `translator`, фабрику per-chat логгеров, конфиг. `run(task)` диспетчеризует по
    `task.kind`: `_run_script` (subprocess) / `_run_llm` (эфемерная сессия) и
    возвращает `(status, output_text)`. После запуска пишет запись истории через
    `store.append_history` (REQ-11) и обновляет `last_status`/`last_error`/
    `last_run_at` в задаче. Доставка зависит от `task.scope`:
    `user` → `send_md_to_chat(bot, task.owner_chat_id, out)`; `global` →
    `_broadcast(out)` по списку получателей (см. ниже), каждый в `try/except`,
    ошибка одного чата логируется и не срывает остальных.
  - **Получатели broadcast:** `cfg.allowed_chat_ids` минус
    `cfg.blacklist_chat_ids`. Запросить у `BotConfig` через хелпер
    `broadcast_targets(cfg)`. Учесть кейс `allowed_for_all=true`: явного списка
    чатов нет (Telegram polling не даёт список чатов) → broadcast возможен только
    по чатам, которые уже есть в `allowed_chat_ids`; иначе глобальная задача
    логирует предупреждение «нет адресатов» (см. Риски).
  - `task_scheduler.py` — `TaskScheduler(store, runner, glog, tick_interval,
    workdir_lock)`. `start()` спавнит цикл `asyncio.Task`; `stop()` отменяет его.
    Каждый тик: `for task in store.list_due(now): сдвинуть next_run
    (повторяющиеся) / пометить completed (одноразовые) -> запланировать запуск ->
    store.update`. Каждый запуск
    оборачивается в `asyncio.create_task` (fire-and-forget с трекингом id
    выполняющихся, чтобы не ставить задачу в очередь повторно — зеркало
    `_running_job_ids` у Hermes), а не блокирует тик. Обернуть каждую задачу в
    `try/except` + per-chat лог.
  - **Модель конкурентности (REQ-9):** один общий `asyncio.Lock` на бот
    (`workdir_lock`), создаётся в `run_bot` и прокидывается в `TaskRunner`.
    `TaskRunner.run(task)`: если `task.kind == "llm"` или `task.exclusive` —
    выполнять под `async with workdir_lock`; скрипт-задачи без `exclusive` —
    выполнять без lock (параллельно). Это сериализует всё, что мутирует
    `working_dir`, и не штрафует независимые скрипты.
- **Точка входа агента для LLM-задач:** добавить `ask_ephemeral(chat_id, prompt,
  *, allowed_tools) -> str` в протокол `AgentBackend` и реализовать в
  `ClaudeAgentBackend` (Codex/PI пока могут кидать `NotImplementedError`, повторяя
  их ограничения по сессиям из `AGENTS.md`). Метод создаёт свежий
  `ClaudeSDKClient` с новым `session_id`, запускает один query, возвращает текст и
  удаляет клиент — не трогая `_clients[chat_id]`, `SessionStore.current` или живую
  per-chat сессию. Это удовлетворяет AC-3. **Права (REQ-13):** передать
  неинтерактивный `can_use_tool`-колбэк, который разрешает инструмент только если
  он в `tasks.allowed_tools`, иначе `PermissionResultDeny` (без обращения к
  Telegram-gate). Опционально продублировать список в `ClaudeAgentOptions.
  allowed_tools` для ранней фильтрации SDK.
- **Конфиг:** добавить поля в `BotConfig` (`tasks_enabled`, `tasks_dir`,
  `tasks_tick_interval_sec`, `tasks_scripts_dir`, `tasks_max_output_chars`,
  `tasks_script_timeout_sec`, `tasks_allowed_tools: tuple[str,...] | None`
  с дефолтом read-only при `None`, `tasks_history_limit: int = 100`), запись
  `"tasks"` в `NESTED_CONFIG_SECTIONS`,
  резолв
  путей + `mkdir` для `tasks_dir`/`tasks_scripts_dir` в `_build` и ключи в
  `payload`. Следовать прецеденту `uploads`/`voice` точь-в-точь. Отдельно добавить
  `admin_chat_ids: tuple[int,...] = ()` в `BotConfig`, ключ `admin_chat_ids` в
  секцию `"access"` (`NESTED_CONFIG_SECTIONS` + `GATEWAY_ACCESS_FIELDS`), парсинг
  через существующий `_parse_chat_id_list` в `_build` и ключ в `payload`. Хелпер
  `is_admin(cfg, chat_id) -> bool` (рядом с `is_allowed`).
- **Wiring:** в `run_bot`, после построения `BotContext` и только при
  `cfg.tasks_enabled`, создать общий `workdir_lock = asyncio.Lock()` и
  сконструировать `TaskStore` + `TaskRunner(workdir_lock=...)` +
  `TaskScheduler`, вызвать `scheduler.start()` до `start_polling` и
  `scheduler.stop()` в `finally`. Прокинуть store в `BotContext` (новое
  опциональное поле `tasks: TaskStore | None`), чтобы хендлер `/task` мог до него
  дотянуться.
- **Хендлер:** новый `src/handlers/tasks.py` с `register(dp)`; зарегистрировать в
  `register_all` до `text.register` (рядом с `sessions`). Подкоманды парсятся из
  аргументов `Command("task")`. Гард на `ctx.tasks is None` → сообщение
  «отключено». При `add`: `--global` ИЛИ `kind=script` → проверка
  `is_admin(cfg, chat_id)`, иначе отказ (REQ-10). `list`/`show`/`rm`/`pause`/
  `resume`: пользователь видит только свои `user`-задачи; админ дополнительно
  видит `global`. Фильтрация по `owner_chat_id`/`scope` обязательна на каждой
  операции (fail-closed).
- **Антипаттерны, которых избегать:** не запускать LLM-задачи через `agent.ask`
  на живой сессии; не строить shell-строки команд (`asyncio.create_subprocess_exec`
  со списком argv, никогда `shell=True`); не резолвить пути скриптов из ввода
  пользователя без containment в `scripts_dir`; не класть логику планировщика в
  `bot.py`; не хардкодить UI-строки.

## Затрагиваемые контракты
- API (команды Telegram): добавляется `/task`. Существующие команды не меняются.
- Данные/схема: новые JSON-файлы под `<tasks_dir>/` (per-bot, напр.
  `var/brain/tasks/`): `<chat_id>.json`, `global.json`,
  `history/<task_id>/<ts>.json`, `_corrupt/<chat_id>.<ts>.json`. Файлы сессий не
  меняются. Новый метод
  `AgentBackend.ask_ephemeral` (аддитивен к протоколу; конкретные бэкенды должны
  реализовать или кидать исключение).
- Права: ACL для сообщений не меняется. Добавляется новое поле `admin_chat_ids` и
  роль админа (только для `scope=global` задач). Новое соображение: политика прав
  LLM-задач (неинтерактивная) — не меняет существующий gate для интерактивных
  ходов. Видимость/управление задачами фильтруется по `owner_chat_id`/`scope`.
- События/очереди/интеграции: новая внутренняя фоновая asyncio-задача на бот.
- Конфигурация/feature flags: новая опциональная секция `tasks`; по умолчанию
  отключено. Новое поле `admin_chat_ids` в секции `access`; по умолчанию пусто.

## Фазы и задачи

### Фаза 1: Модель задачи + персистентность
- [x] [REQ-1] Добавить `src/infra/task_types.py` с pydantic `TaskSchedule`
  (`kind: Literal["once","interval","cron"]`, `run_at`, `interval_sec`, `expr`,
  `display`), `TaskRepeat` (`times: int | None`, `completed: int`) и `Task`
  (все поля REQ-1, `kind: Literal["llm","script"]`, `scope: Literal["user",
  "global"]`, `state: Literal["scheduled","paused","completed","error"]`,
  `owner_chat_id: int`, `exclusive: bool`). → Verify:
  `python -m py_compile src/infra/task_types.py` и юнит-тест строит `Task` и
  делает round-trip `model_dump`/`model_validate`.
- [x] [REQ-1] Реализовать `parse_schedule(text)` (принимает `"in 2h"`, `"30m"`,
  ISO-timestamp → `once`; `"every 30m"`/`"every 1d"` → `interval`; 5-польную
  cron-строку → `cron`) и `compute_next_run(schedule, after)`, портируя
  `hermes/cron/jobs.py:209-420`. → Verify: юнит-тесты проверяют next-run для
  каждого kind, вкл. перекат интервала и выброс `ValueError` на невалидной
  строке.
- [x] [REQ-2, REQ-2b, REQ-10] Добавить `src/infra/task_store.py` (`TaskStore`)
  JSON-на-чат с durable атомик-хелпером (`tempfile` → `flush` → `os.fsync` →
  `os.replace` → `chmod 0600`), `asyncio.Lock` вокруг read-modify-write,
  `add/get/list_all/update/remove` и `list_due(now)`, сканирующим
  `<chat_id>.json` И `global.json`. `scope=global` → `global.json`, `scope=user`
  → файл владельца. Битый JSON → перенос в `_corrupt/<chat_id>.<ts>.json` +
  лог + пустая структура. → Verify: юнит-тест в `tests/test_task_store.py`
  добавляет user- и global-задачи под `tmp_path`, перезагружает свежий store,
  проверяет `list_due` из обоих источников, `list_all(chat_id)` без
  `include_global` не отдаёт чужие/глобальные, и подсунутый битый файл уезжает
  в `_corrupt/`.
- [x] [REQ-1, REQ-11] Реализовать в `TaskStore` `append_history(task, record)` /
  `list_history(task_id)` с traversal-guard `_history_dir(task_id)` (валидация
  12-hex, отклонение `..`/separators), генерацию иммутабельного hex-`id` в `add`
  и обрезку истории до `history_limit` (удалять старейшие записи сверх N при
  записи). → Verify: юнит-тест пишет запись истории и читает обратно; запись
  N+1 удаляет старейшую (остаётся N); `id` с `..` или не-hex отклоняется
  `ValueError`.

### Фаза 2: Конфиг + wiring (без логики планировщика пока)
- [x] [REQ-7] Добавить поля `tasks_*` в `BotConfig`, запись `"tasks"` в
  `NESTED_CONFIG_SECTIONS`, резолв путей + `mkdir` для `tasks_dir` /
  `tasks_scripts_dir` в `_build` и ключи в `payload`. → Verify: кейс в
  `tests/test_config.py` загружает YAML с секцией `tasks:` и проверяет
  отрезолвленные абсолютные пути + `tasks_enabled=True`; отсутствие секции →
  `tasks_enabled=False`.
- [x] [REQ-10] Добавить `admin_chat_ids: tuple[int,...] = ()` в `BotConfig`,
  ключ в секцию `access` (`NESTED_CONFIG_SECTIONS` + `GATEWAY_ACCESS_FIELDS`),
  парсинг через `_parse_chat_id_list` в `_build`, ключ в `payload`, и хелпер
  `is_admin(cfg, chat_id)`. → Verify: `tests/test_config.py` грузит
  `access.admin_chat_ids` и проверяет tuple; `is_admin` true только для
  перечисленных, false при пустом списке.
- [x] [REQ-7] Добавить опциональное `tasks: TaskStore | None = None` в
  `BotContext`; в `run_bot` строить `TaskStore` только при `cfg.tasks_enabled` и
  прокидывать его. → Verify: проверка в стиле `tests/test_bot_factories.py` (или
  ручной запуск) показывает, что бот стартует с секцией и без неё;
  `python -m py_compile` чистый.

### Фаза 3: Скрипт-задачи (вертикальный срез end-to-end)
- [x] [REQ-4] Добавить `src/infra/task_runner.py` с `_run_script(task)` через
  `asyncio.create_subprocess_exec` (список argv, без shell), интерпретатор по
  расширению (`.sh`/`.bash`→bash, иначе `sys.executable`), таймаут
  `script_timeout_sec`, захват stdout, обрезка вывода до `max_output_chars` и
  валидация containment пути относительно `tasks_scripts_dir`. → Verify:
  `tests/test_task_runner.py` запускает временный `echo`/`print` скрипт и
  проверяет захваченный stdout; путь с traversal отклоняется.
- [x] [REQ-3, REQ-8, REQ-9, REQ-14, AC-1, AC-5, AC-7] Добавить
  `src/infra/task_scheduler.py` с тик-циклом: выбрать due, проверить права
  владельца (REQ-14: отозван → пауза + `access_revoked`, skip), сдвинуть
  `next_run_at` для повторяющихся до запуска, запустить через `TaskRunner`
  (fire-and-forget с трекингом выполняющихся id), доставить через
  `send_md_to_chat`, затем пометить `completed`
  одноразовые / `update` повторяющиеся; изолировать каждую задачу в `try/except`
  с per-chat логом. Реализовать `workdir_lock` в `TaskRunner.run`: LLM и
  `exclusive`-скрипты — под lock, неэксклюзивные скрипты — без. Старт в `run_bot`
  (управляемый `tasks_enabled`), стоп в `finally`. → Verify: ручной запуск —
  создать одноразовую скрипт-задачу через `parse_schedule("in 1m")`, подтвердить
  доставку и `state=completed` в `/task list`; падающий скрипт сохраняет повторяющуюся
  задачу и ставит `last_status=error` (проверка лога); две одновременные
  LLM-задачи идут последовательно по таймстампам лога (AC-7).

### Фаза 4: LLM-задачи
- [x] [REQ-5, REQ-13, AC-3] Добавить `ask_ephemeral(chat_id, prompt, *,
  allowed_tools)` в протокол `AgentBackend`; реализовать в `ClaudeAgentBackend`
  (свежий `ClaudeSDKClient` с новым `session_id`, один query, удаление; никогда
  не мутируя `_clients[chat_id]` или `SessionStore.current`). Неинтерактивный
  `can_use_tool`: разрешать только инструменты из `allowed_tools`, иначе deny.
  Codex/PI кидают `NotImplementedError`. → Verify: `tests/test_agent_backends.py`
  проверяет, что указатель `current_session`/store не изменился после
  `ask_ephemeral`, и что инструмент вне `allowed_tools` получает deny (мок
  SDK-клиента).
- [x] [REQ-5] Подключить `_run_llm(task)` в `TaskRunner`: читать
  `cfg.tasks_allowed_tools` (с дефолтом read-only при `None`), вызвать
  `ask_ephemeral(..., allowed_tools=...)`, доставить ответ. → Verify: ручной
  запуск — одноразовая LLM-задача доставляет ответ; `/sess` показывает
  неизменённую текущую сессию пользователя (AC-3).
- [x] [REQ-5b, AC-8] Реализовать доставку по `scope` в `TaskRunner`: `user` →
  один `send_md_to_chat(owner_chat_id)`; `global` → `_broadcast` по
  `broadcast_targets(cfg)` (allowed минус blacklist), каждый чат в `try/except`.
  → Verify: юнит-тест на `broadcast_targets` (вычитание blacklist, кейс
  `allowed_for_all`); ручной запуск глобальной задачи доставляет во все
  разрешённые чаты.

### Фаза 5: Команда `/task` + i18n
- [x] [REQ-6, REQ-10, AC-8] Добавить `src/handlers/tasks.py` с `register(dp)`,
  обрабатывающим `add|list|show|pause|resume|run|rm` через `Command("task")`;
  достучаться до задач через `ctx.tasks`; гард `ctx.tasks is None` → «отключено».
  При `add`: `--global` ИЛИ `kind=script` → `is_admin(cfg, chat_id)`, иначе
  отказ. Все операции фильтруют по `owner_chat_id`/`scope` (пользователь — только
  свои user-задачи; админ дополнительно видит global). Зарегистрировать в
  `register_all` до `text.register`. → Verify: ручная проверка — пользователь не
  видит/не удаляет чужие задачи; неадмин получает отказ на `--global` и на
  `kind=script`; админ создаёт глобальную/скриптовую; `list` показывает name,
  kind, scope, schedule, next run, last status.
- [x] [REQ-6, REQ-7, REQ-10] Добавить ключи i18n `task_*` в каждый
  `src/i18n/<lang>.json` (created, list header/row, not_found, paused, resumed,
  removed, triggered, disabled, error, admin_only, global_created,
  no_broadcast_targets). → Verify: `tests/test_i18n.py` подтверждает, что каждый
  новый ключ есть во всех файлах локалей и форматируется со своими kwargs.

### Фаза 6: Устойчивость к рестарту + доки + финальные проверки
- [x] [REQ-3, REQ-12, AC-6] Реализовать grace/fast-forward логику в выборе due
  (порт `get_due_jobs` + `_compute_grace_seconds` из hermes
  [cron/jobs.py:1012,344]). Принципы: (a) состояние задачи — единственный
  `next_run_at`, никакого бэклога пропущенных моментов; история на диске НЕ
  исполняется. (b) Повторяющиеся: `grace = период/2`, clamp `[120с, 2ч]`; если
  `(now - next_run_at) > grace` → перемотать `next_run_at` на следующий будущий
  через `compute_next_run` и `continue` (skip, без запуска); иначе — один
  догоняющий запуск. (c) Одноразовые: grace-окно 120с — если `run_at` старше
  `now-120с` → не запускать (перевести в `state=completed` без запуска); иначе
  один запуск; при выставленном `last_run_at` — больше никогда. → Verify:
  юнит-тесты: ежедневная задача, просроченная на 1ч → один запуск; на 5ч → skip +
  перемотка; «каждые 10 мин», просроченная на 1ч → один skip+перемотка (НЕ 6
  запусков); одноразовая старше 120с → completed без запуска; одноразовая в
  пределах 120с → один запуск.
- [x] [REQ-7] Задокументировать секцию `tasks` в `CONFIG.md` и команду `/task` +
  правила скриптов в `COMMANDS.md`/`AGENTS.md`; добавить `croniter` в deps
  `pyproject.toml` + `requirements.txt`. → Verify: доки
  читаются чисто; `pip install -e ".[dev]"` резолвится.

## Готово, когда
- [x] Одноразовая скрипт-задача и повторяющаяся интервальная задача обе
  срабатывают и доставляют вывод в чат (AC-1, AC-2).
- [x] Одноразовая LLM-задача доставляет ответ без изменения текущей сессии чата
  (AC-3).
- [x] При отсутствующей/отключённой секции `tasks` бот стартует нормально,
  планировщик не запускается, `/task` возвращает сообщение «отключено», а потоки
  сообщений/голоса/загрузок не изменены (AC-4).
- [x] Падающие скрипты/LLM-запуски записываются (`last_status=error`,
  `last_error`) и не удаляют повторяющиеся задачи и не рушат цикл (AC-5, REQ-8).
- [x] Пользователи изолированы: видят/управляют только своими задачами; только
  админ создаёт глобальные; глобальный вывод рассылается во все allowed-чаты
  (AC-8, REQ-5b, REQ-10).
- [x] Задачи перезагружаются после рестарта и устаревшие одноразовые срабатывают
  один раз в пределах grace-окна (AC-6).
- [x] Каждый запуск пишет запись истории в
  `<tasks_dir>/history/<task_id>/<ts>.json`; запись файлов durable (fsync) и
  битый JSON уезжает в `_corrupt/` (REQ-2, REQ-2b, REQ-11).
- [x] Зелёные: `ruff check src/ tests/`, `mypy src/ tests/ --strict`,
  `pyright src/ tests/`, `bandit -r src/ -q`, `pip-audit --strict`, `pytest -q` и
  `py_compile` по `src/`.

## Риски / Неизвестное
- Решено (REQ-13): LLM-задачам нужны права, но в фоне некому нажать кнопки gate.
  → Права задаются в `config.yaml` `tasks.allowed_tools`; неинтерактивный колбэк
  разрешает только инструменты из списка, остальное — молчаливый deny. Дефолт
  (поле не указано) — read-only `[Read, Glob, Grep, WebFetch]`; `[]` — без
  инструментов. Отдельно от интерактивного `gate.can_use_tool`.
- Риск: Subprocess-исполнение скриптов — это поверхность RCE. → Containment путей
  скриптов в `tasks_scripts_dir`, отклонять traversal, использовать
  `create_subprocess_exec` (argv, без shell), форсить таймаут, запускать только
  для уже разрешённых чатов и никогда не передавать пользовательский текст в
  shell. Bandit должен оставаться зелёным. **Доп. защита (решено):** создавать
  `kind=script` задачи может только админ (REQ-10).
- Риск: Долгий запуск задачи перекрывает тик-интервал и срабатывает повторно. →
  Сдвигать `next_run_at` до запуска; трекать id выполняющихся задач, чтобы
  пропускать повторную постановку в очередь (зеркало `_running_job_ids` у Hermes).
- Риск (REQ-9): LLM-задача и живой ход агента пользователя делят `working_dir` →
  потенциальная гонка на файлах, т.к. `workdir_lock` задач НЕ охватывает
  пользовательские ходы (`ask_stream`). → Осознанно вне области для v1 (проблема
  уже существует между чатами одного бота); если станет реальной — поднять
  `workdir_lock` в `AgentBackend` и брать его и в `ask_stream`, и в
  `ask_ephemeral`. Зафиксировано как будущее расширение.
- Риск: Эксклюзивная задача может надолго захватить `workdir_lock` и задержать
  другие LLM-задачи. → Таймаут хода агента (`agent_timeout_sec`) ограничивает
  удержание; долгие задачи планировать на разнесённое время.
- Риск: При `allowed_for_all=true` явного списка `allowed_chat_ids` нет, а
  Telegram polling не даёт перечень чатов → у глобальной задачи нет адресатов для
  broadcast. → `broadcast_targets` рассылает только по непустому
  `allowed_chat_ids`; при пустом — логировать предупреждение и не падать. Для
  реального broadcast при `allowed_for_all` потребуется реестр «виденных» чатов
  (вне области v1).
- Риск: Глобальная задача от админа исполняет LLM-ход с правами на `working_dir`
  для всех пользователей сразу (эскалация). → `scope=global` создаёт только
  админ (REQ-10, fail-closed); промпт глобальной задачи проходит то же
  сканирование инъекций, что и обычные.
- Решено: рост истории ограничен мягким потолком `tasks_history_limit` (дефолт
  100 записей на задачу) — `append_history` удаляет старейшие сверх N. `output`
  обрезан до `max_output_chars`, так что и одна запись ограничена. Диск под
  контролем без ручной чистки.
- Решено (REQ-14): задачи юзера с отозванным доступом не должны срабатывать. →
  Перед запуском планировщик проверяет `is_allowed(owner_chat_id)`; если доступ
  отозван — не запускать, поставить задачу на паузу (`enabled=false`) и записать
  `last_error=access_revoked`. (Для `scope=global` проверять `is_admin(owner)`.)
- Риск: `ask_ephemeral`, создающий SDK-клиенты на каждый запуск, может течь
  ресурсами. → Всегда удалять в `finally`; ограничить конкурентность в
  планировщике.
- Решено: cron включён в v1 (все три типа once/interval/cron); `croniter` —
  твёрдая зависимость с самого начала.
- Решено: завершённая одноразовая хранится с `state=completed` (`enabled=false`),
  остаётся в `/task list` для аудита; чистка вручную через `/task rm`.
