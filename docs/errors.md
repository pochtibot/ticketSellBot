# Реестр ошибок TicketBot

Формат записи:
- **Дата:** когда обнаружено
- **Описание:** что произошло + логи
- **Причина:** почему возникло
- **Исправление:** что изменено (файл + суть)
- **Коммит:** ссылка на git

---

## 001 — vkbottle>=5.0 не найден в PyPI

- **Дата:** 2026-07-05
- **Описание:** `pip install vkbottle>=5.0` падает с ошибкой — пакет не найден. PyPI содержит только версии до 4.10.0.
- **Причина:** Неверно указана версия библиотеки. vkbottle на PyPI не превышает 4.10.0.
- **Исправление:** `requirements.txt` — `vkbottle>=5.0` → `vkbottle>=4.10.0`
- **Коммит:** — (исправлено в процессе первой настройки)

---

## 002 — Docker wait-for-db выходит без запуска приложения

- **Дата:** 2026-07-05
- **Описание:** Контейнер ждёт PostgreSQL, пишет «PostgreSQL готов!» и завершается. Бот не запускается.
- **Причина:** В конце скрипта `wait-for-db` стоял `exit 0`, а не `exec "$@"`. Скрипт завершал контейнер вместо передачи управления приложению.
- **Исправление:** `Dockerfile` — `exit 0` → `exec "$@"`
- **Коммит:** — (исправлено в процессе)

---

## 003 — VK_GROUP_ID="" не парсится как Optional[int]

- **Дата:** 2026-07-05
- **Описание:** При пустом значении `VK_GROUP_ID` в .env pydantic выдаёт ошибку валидации, не может привести пустую строку к `Optional[int]`.
- **Причина:** Поле объявлено как `Optional[int]`, пустая строка не конвертируется в None.
- **Исправление:** `config.py` — `vk_group_id: Optional[str]` (храним как строку, конвертируем при использовании)
- **Коммит:** — (исправлено в процессе)

---

## 004 — Node.js 20 deprecated в GitHub Actions

- **Дата:** 2026-07-05
- **Описание:** Warning при запуске workflow — `Node.js 20 actions are deprecated. Please update to Node.js 20.`
- **Причина:** Используются старые версии actions: checkout@v4, setup-python@v5.
- **Исправление:** `.github/workflows/deploy.yml` — `checkout@v4→v6`, `setup-python@v5→v6`
- **Коммит:** — (исправлено в процессе)

---

## 005 — IndentationError в проверке импортов на CI

- **Дата:** 2026-07-05
- **Описание:** Деплой падает на шаге проверки импортов с `IndentationError: unexpected indent`.
- **Причина:** Многострочный YAML с preserve-whitespace привёл к сломанному отступу в Python коде.
- **Исправление:** `.github/workflows/deploy.yml` — проверка импортов одной строкой `python -c "..."`.
- **Коммит:** — (исправлено в процессе)

---

## 006 — "Repository path is not under workspace" (checkout в /opt/ticketbot)

- **Дата:** 2026-07-05
- **Описание:** `Error: Repository path '/opt/ticketbot' is not under '/home/user1/actions-runner/_work/ticketSellBot/ticketSellBot'`
- **Причина:** Использован `path:` в actions/checkout@v6, указывающий директорию вне рабочей области раннера.
- **Исправление:** Удалён `path:` из checkout, все `working-directory` ссылки удалены. Runner сам клонирует в `_work/`.
- **Коммит:** — (исправлено в процессе)

---

## 007 — Docker permission denied (self-hosted runner)

- **Дата:** 2026-07-05
- **Описание:** `permission denied while trying to connect to the Docker daemon socket` — раннер не может выполнять docker compose.
- **Причина:** В `deploy/setup-runner.sh` команда `usermod -aG docker` была внутри блока `else`, поэтому существующий пользователь runner не добавлялся в группу docker.
- **Исправление:** `deploy/setup-runner.sh` — `usermod -aG docker` вынесен из `if-else` и выполняется всегда.
- **Коммит:** `6d26730` — Fix setup-runner.sh: usermod -aG docker теперь выполняется всегда

---

## 008 — Telegram API timeout с российского хостинга

- **Дата:** 2026-07-07
- **Описание:** Бот падает при старте с `TelegramNetworkError: Request timeout error`. Запускается только на 3-й раз, когда Docker перезапустит контейнер через restart policy.
- **Причина:** С российского VPS (Beget/LightNode) соединение с Telegram API (api.telegram.org) идёт нестабильно, первые 2 попытки уходят в таймаут.
- **Исправление:** `platforms/telegram/bot.py` — добавлен retry-цикл в `run()`: до 10 попыток с экспоненциальной задержкой (5с → 60с). Аналогично поправлено в `platforms/vk/bot.py`.
- **Коммит:** `62752d3` — Telegram: retry при ошибках подключения к API

---

## 009 — VK бот: команды с параметрами не работали

- **Дата:** 2026-07-07
- **Описание:** Хендлеры вида `text=["/buy <event_id>"]` используют список, что предполагает точное совпадение текста. Пользователь не может передать параметр команде.
- **Причина:** vkbottle ожидает строку или паттерн, а не список с литералом.
- **Исправление:** `platforms/vk/bot.py` — `text=["/buy <event_id>"]` → `text="/buy <event_id>"` (строка вместо списка). Также добавлена безопасная проверка `message.sender` и retry-логика.
- **Коммит:** `e17488f` — Разделение кода соцсетей + правки VK бота

---

## 010 — Таблица `events` не создаётся при деплое

- **Дата:** 2026-07-07
- **Описание:** После деплоя команда `/events` в Telegram падает с `UndefinedTableError: relation "events" does not exist`. База запущена, init_db() вызвана, но таблиц нет.
- **Причина:** `init_db()` вызывает `Base.metadata.create_all`, но на момент вызова ни одна модель не импортирована. SQLAlchemy не знает о таблицах.
- **Исправление:** `core/database.py` — добавлен `from core.models import User, Event, Ticket, Payment` внутрь `init_db()`. Модели импортируются перед `create_all`.
- **Коммит:** `29746ef` — Fix: init_db теперь импортирует модели перед create_all + seed в деплой

---

## 011 — VK бот: message.sender может быть None

- **Дата:** 2026-07-07
- **Описание:** Код обращается к `message.sender.first_name` без проверки на None. Если VK API не вернул данные пользователя — AttributeError.
- **Причина:** vkbottle возвращает `sender = None` при ошибках получения профиля.
- **Исправление:** `platforms/vk/bot.py` — добавлен метод `_get_user_name()` с проверкой `if message.sender`.
- **Коммит:** `e17488f` — Разделение кода соцсетей + правки VK бота

---

## 012 — Payment.ticket_id = null при покупке билета

- **Дата:** 2026-07-07
- **Статус:** ✅ Исправлено
- **Описание:** При покупке билета (`/buy`) бот падает с `NotNullViolationError: null value in column "ticket_id" of relation "payments" violates not-null constraint`. Лог показывает, что в INSERT в payments параметр `ticket_id` = None.
- **Анализ:** `core/services.py:180-192`
   1. `ticket = Ticket(...)` — в конструкторе не передан `id`. Поле `id` модели имеет `default=uuid.uuid4`, но это column-level default, который не выполняется при создании объекта в Python → `ticket.id == None`
   2. `payment = Payment(ticket_id=ticket.id)` — передаётся None
   3. `self.session.flush()` — Ticket получает UUID от БД, но Payment уже сформирован с `ticket_id = None`
- **Причина (подтверждено: core/models.py:79-80, core/services.py:180-192):** `default=uuid.uuid4` в SQLAlchemy ORM — это column default, не заполняющий атрибут объекта до flush()
- **Исправление:** `core/services.py:180` — явная генерация `id=uuid.uuid4()` при создании Ticket
- **Связанные ошибки:** нет

---

## 013 — GitHub Actions: Unrecognized named-value 'secrets' в if: выражениях

- **Дата:** 2026-07-10
- **Статус:** ✅ Исправлено
- **Описание:** Деплой падает на этапе парсинга workflow с ошибкой:
  ```
  (Line: 77, Col: 13): Unrecognized named-value: 'secrets'. Located at position 1
  within expression: secrets.VK_TOKEN != ''
  (Line: 88, Col: 13): Unrecognized named-value: 'secrets'. Located at position 1
  within expression: secrets.MAX_TOKEN != ''
  (Line: 189, Col: 13): Unrecognized named-value: 'secrets'. Located at position 1
  within expression: secrets.VK_TOKEN != ''
  ```
  Шаги создания `.env.vk`, `.env.max` и запуска VK бота не выполняются, workflow завершается ошибкой до запуска контейнеров.
- **Анализ:**
  - **Гипотеза:** `${{ secrets.X }}` в `if:` не поддерживается GitHub Actions, т.к. `${{ }}` вычисляется на этапе парсинга, а `secrets` доступен только в runtime контексте.
  - **Подтверждено (docs.github.com):** GitHub Actions документация явно указывает, что `secrets` нельзя использовать в `if:` условиях. Вместо этого секрет передаётся через `env:` блок, а в `if:` используется `env.VK_TOKEN != ''`.
- **Исправление (подтверждено: .github/workflows/deploy.yml:77-79, 90-92, 193-195):**
  Замена во всех трёх местах:
  ```yaml
  # Было:
  if: ${{ secrets.VK_TOKEN != '' }}

  # Стало:
  env:
    VK_TOKEN: ${{ secrets.VK_TOKEN }}
  if: env.VK_TOKEN != ''
  ```
  Принцип: `${{ secrets.X }}` в `env:` резолвится в пустую строку, если секрет не задан. `env.X != ''` — корректное runtime-выражение.
- **Коммит:** `3f9d205` — fix: secrets in GitHub Actions if: expressions
- **Связанные ошибки:** нет

---

## 014 — CI: RuntimeError — starlette.testclient requires httpx2 (тесты test_web_api не собираются)

- **Дата:** 2026-07-10
- **Статус:** ✅ Исправлено
- **Описание:** На CI (GitHub Actions) тесты падают при сборе с ошибкой:
  ```
  ERROR collecting tests/test_web_api.py
  RuntimeError: The starlette.testclient module requires the httpx2 package
  ```
  Локально тесты проходят, на CI — нет. При этом `test_web_api.py` импортирует `TestClient` из `fastapi.testclient`.
- **Анализ:**
  - **Гипотеза:** `httpx` не указан в зависимостях, на CI fresh install без него.
  - **Подтверждено (`pyproject.toml:19-24`):** В `[project.optional-dependencies] dev` отсутствует `httpx`. Локально он уже есть в окружении (`pip list`), на CI — нет.
  - Ошибка говорит "httpx2", но имеется в виду пакет `httpx` версии 2.x (современный). `fastapi.testclient` требует `httpx>=0.28`.
- **Исправление (подтверждено: pyproject.toml:24):**
  Добавлена строка `"httpx>=0.28"` в `dev`-зависимости.
- **Коммит:** `7bb4543` — fix: add httpx to dev dependencies for FastAPI TestClient
- **Связанные ошибки:** нет

---

## 015 — Deploy: service "app" has neither an image nor a build context (docker compose exec без COMPOSE_FILES)

- **Дата:** 2026-07-10
- **Статус:** ✅ Исправлено
- **Описание:** Деплой падает на шаге «Создать роли PostgreSQL» с ошибкой:
  ```
  service "app" has neither an image nor a build context specified: invalid compose project
  ```
  Лог показывает, что команда `docker compose exec -T db bash -c "..."` запущена без флагов `-f`.
- **Анализ:**
  - **Гипотеза:** `docker compose exec` без `${{ env.COMPOSE_FILES }}` пытается перепарсить compose-файлы. На self-hosted runner находит неполный compose-проект (например, файл `compose.yaml` вне репозитория) и падает.
  - **Подтверждено (`deploy.yml:128, 171, 118, 203, 205, 208`):** Все `docker compose exec`, `ps`, `logs` команды используют только дефолтный `docker-compose.yml` без Beget-override. При этом проект создан с `-f docker-compose.yml -f deploy/docker-compose.beget.yml`.
- **Исправление (подтверждено: .github/workflows/deploy.yml):**
  Добавлен `${{ env.COMPOSE_FILES }}` (`-f docker-compose.yml -f deploy/docker-compose.beget.yml`) во все `docker compose` команды:
  - Ожидание PostgreSQL (pg_isready)
  - Создание ролей (psql heredoc)
  - Выдача прав на таблицы (psql -c)
  - Проверка работоспособности (ps, logs)
- **Коммит:** `96384b8` — fix: add COMPOSE_FILES to all docker compose commands in deploy
- **Связанные ошибки:** нет

---

## 016 — ModuleNotFoundError: No module named 'bot' (неверный Python-модуль в docker-compose)

- **Дата:** 2026-07-11
- **Статус:** ✅ Исправлено
- **Описание:** При деплое контейнер telegram падает с циклическим перезапуском:
  ```
  /usr/local/bin/python: Error while finding module specification for 'bot.telegram'
  (ModuleNotFoundError: No module named 'bot')
  ```
  Контейнер уходит в `Restarting (1)` и не поднимается.
- **Анализ:**
  - **Подтверждено (`Dockerfile:6`, `docker-compose.yml:30`, `docker-compose.yml:44`, `docker-compose.yml:58`, `docker-compose.yml:68`, `docker-compose.yml:78`):**
    - В Dockerfile: `WORKDIR=/app`, `PYTHONPATH=/app`
    - Код проекта копируется в `/app`, реальная структура: `/app/app/bot/telegram.py` — то есть пакет `app.bot.telegram`
    - Команды в docker-compose.yml: `python -m bot.telegram` — поиск идёт по пути `/app/bot/telegram.py` (не существует)
    - Ранее, когда `bot/` лежал в корне репозитория, команда `python -m bot.telegram` работала. После реструктуризации (перенос `bot/` под `app/`) docker-compose.yml не обновлён.
  - Причина: несоответствие между PYTHONPATH и фактическим расположением модулей после реструктуризации.
- **Исправление (подтверждено: `docker-compose.yml:30,44,58,68,78`):**
  Все команды `python -m bot.xxx` заменены на `python -m app.bot.xxx`:
  ```yaml
  # Было:
  command: ["python", "-m", "bot.telegram"]
  # Стало:
  command: ["python", "-m", "app.bot.telegram"]
  ```
  Изменены сервисы: `telegram`, `vk`, `max`, `seed`, `web`.
- **Коммит:** (будет в след. коммите)
- **Связанные ошибки:** нет

---

## 017 — ADMIN_TELEGRAM_IDS пустой в контейнере (переопределяется compose override)

- **Дата:** 2026-07-11
- **Статус:** ✅ Исправлено
- **Описание:** В контейнере `ticketbot-telegram` переменная `ADMIN_TELEGRAM_IDS` пустая, хотя в GitHub Secrets значение задано, и `.env.telegram` создаётся из секрета. Админ-команды (`/repost_events`, `/admin`) отвечают «У вас нет доступа к панели администратора».
- **Анализ:**
  - **Подтверждено (`deploy/docker-compose.beget.yml:38-39`):** В beget override для сервиса `telegram` указан блок:
    ```yaml
    environment:
      ADMIN_TELEGRAM_IDS: ${ADMIN_TELEGRAM_IDS:-}
    ```
  - Docker Compose при merge даёт приоритет `environment` над `env_file`. Значение из `.env.telegram` затирается.
  - Переменная `${ADMIN_TELEGRAM_IDS}` не задана в shell окружении раннера → раскрывается в пустую строку.
- **Исправление (подтверждено: `deploy/docker-compose.beget.yml:38-39`):**
  Удалён блок `environment` у сервиса `telegram` в beget override. Значение `ADMIN_TELEGRAM_IDS` теперь берётся из `.env.telegram`, который создаётся на шаге деплоя из GitHub Secret.
- **Коммит:** `03113f3`
- **Связанные ошибки:** нет

---

## 018 — Alembic миграция падает на CI: "table channels already exists" / "column channel_id does not exist"

- **Дата:** 2026-07-11
- **Статус:** ✅ Исправлено
- **Описание:** Деплой на CI проходит через несколько итераций ошибок:
  1. `init_db()` создаёт таблицы → alembic stamp head → миграция 0002 пытается создать таблицы повторно → `relation "channels" already exists`
  2. После замены `init_db()` на alembic — ошибка `column channel_id of relation "events" does not exist`, потому что init_db() не добавляет колонки в существующие таблицы (только DROP ALL → CREATE ALL, но данные уже есть)
- **Анализ:**
  - **Подтверждено:** init_db() вызывает `Base.metadata.create_all`, она **создаёт только новые таблицы**, но **не добавляет колонки** в существующие.
  - Alembic migration 0002 корректно описывает изменения, но при наличии данных `drop_all → create_all` недопустим.
- **Исправление (подтверждено: `.github/workflows/deploy.yml:157-186`):**
  Три шага в деплое:
  1. `alembic stamp head` — проставляет версию в существующей БД (без миграции)
  2. `ALTER TABLE ADD COLUMN IF NOT EXISTS` — идемпотентное добавление колонок
  3. `INSERT INTO channels ... WHERE NOT EXISTS` — создание legacy канала
  4. `UPDATE events SET channel_id = ... WHERE channel_id IS NULL` — бэкфилл
  Итоговая версия — stamp head + идемпотентный SQL через psql.
- **Коммиты:** `bed6e60`, `1449908`, `b5eb13a`, `a7d6e6a`
- **Связанные ошибки:** нет

---

## 019 — "кнопка подтвердить не активна" в create_event FSM

- **Дата:** 2026-07-11
- **Статус:** ✅ Исправлено
- **Описание:** При создании мероприятия через FSM, на шаге подтверждения кнопка "✅ Подтвердить" не активна (disabled). Пользователь не может завершить создание мероприятия.
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py`, хендлер `admin:confirm_create`):**
    - `callback.answer()` вызывалась **после** всех DB-операций (создание Event + запись в БД)
    - Telegram требует `callback.answer()` в течение 30 секунд. Если не вызвана — кнопка "висит" в состоянии ожидания
    - При ошибке БД или долгом запросе Telegram не получает answer → деактивирует кнопку
  - Дополнительно: не было проверки state перед выполнением (если пользователь не в FSM)
- **Исправление (подтверждено: `app/platforms/telegram/bot.py`):**
  1. `callback.answer()` перенесён в самое начало хендлера
  2. Добавлен try-except с отправкой сообщения об ошибке
  3. Добавлена валидация state (если нет данных в FSM — отправить alert)
- **Коммит:** `a7f15ff`
- **Связанные ошибки:** нет

---

## 020 — Legacy канал: admin_telegram_user_id='0' приводит к пустому меню админа

- **Дата:** 2026-07-11
- **Статус:** ✅ Исправлено
- **Описание:** После деплоя multi-tenant в каналах пропали inline-кнопки. Команда `/admin` показывала «Панель управления» с кнопками, но все админ-команды возвращали ошибку. Пользователи в канале вообще не видели кнопок.
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py`, метод `_get_admin_channel()`):**
    - Метод ищет канал по `admin_telegram_user_id == str(user_id)`
    - Legacy канал создаётся с `admin_telegram_user_id = '0'`
    - Ни один реальный пользователь не имеет Telegram ID = 0
    - → `_get_admin_channel()` возвращает None → админ не может управлять каналом
  - Дополнительно: канальные кнопки (`channel_buy`, `channel_events`) требуют контекст канала, который определялся через `_get_admin_channel()` — не работал.
- **Исправление (подтверждено: `app/platforms/telegram/bot.py`):**
  Добавлен fallback в `_get_admin_channel()`:
  1. Если канал с `admin_telegram_user_id = '0'` существует — автоматически привязать к текущему админу
  2. Обновить `admin_telegram_user_id` на реальный ID пользователя
  3. Записать изменения в БД
  4. Вернуть канал админу
- **Коммит:** `913b104`
- **Связанные ошибки:** нет

---

## 021 — CI test_admin_menu_unauthorized падает: _get_admin_channel требует БД

- **Дата:** 2026-07-11
- **Статус:** ✅ Исправлено
- **Описание:** На CI тест `test_admin_menu_unauthorized` падает с ошибкой, потому что `admin_menu()` теперь вызывает `_get_admin_channel()`, который требует подключения к БД и сервисов.
- **Анализ:**
  - **Подтверждено (`tests/test_telegram_bot.py:164-171`):**
    - После перехода на button-based меню, `admin_menu()` вызывает `_get_admin_channel()` даже для неавторизованного пользователя (проверка на super-admin происходит раньше, но channel admin path тоже вызывает метод)
    - Тест использует mock-объекты без подключения к БД
    - `_get_admin_channel()` не замокан → падает
- **Исправление (подтверждено: `tests/test_telegram_bot.py:167`):**
  Добавлен `patch.object(telegram_bot, "_get_admin_channel", new_callable=AsyncMock, return_value=None)`:
  ```python
  with patch.object(telegram_bot, "_get_admin_channel", new_callable=AsyncMock, return_value=None):
      await telegram_bot.admin_menu(mock_message)
  ```
- **Коммит:** `18e56ed`
- **Связанные ошибки:** нет

---

## 022 — CI test_admin_menu_authorized: текст меню изменился на кнопочный

- **Дата:** 2026-07-11
- **Статус:** ✅ Исправлено
- **Описание:** На CI тест `test_admin_menu_authorized` падает, потому что проверяет текст "Панель администратора", но после перехода на button-based меню текст изменился на "Панель управления".
- **Анализ:**
  - **Подтверждено (`tests/test_telegram_bot.py:174-188`):**
    - Тест проверяет `assert "Панель администратора" in text`
    - В новой реализации `admin_menu()` отправляет "Панель управления" с inline-кнопками
- **Исправление (подтверждено: `tests/test_telegram_bot.py:185`):**
  Изменена проверка текста:
  ```python
  assert "Панель управления" in text
  ```
  Добавлена проверка наличия `reply_markup` в kwargs.
- **Коммит:** `f8846be`
- **Связанные ошибки:** нет

---

## 023 — Permission denied for schema public при CREATE TYPE platformtype

- **Дата:** 2026-07-12
- **Статус:** ✅ Исправлено
- **Описание:** После деплоя контейнер telegram циклически перезапускается с ошибкой:
  ```
  asyncpg.exceptions.InsufficientPrivilegeError: permission denied for schema public
  [SQL: CREATE TYPE platformtype AS ENUM ('telegram', 'vk', 'max')]
  ```
  `init_db()` не может создать ENUM-тип `platformtype`, бот не стартует.
- **Анализ:**
  - **Подтверждено (`.github/workflows/deploy.yml:135,143,151`):**
    - На шаге «Создать роли PostgreSQL» платформенным ролям выдаётся только `GRANT USAGE ON SCHEMA public`
    - `USAGE` позволяет обращаться к существующим объектам, но `CREATE TYPE/platformtype` требует права `CREATE` на схему
    - `init_db()` → `Base.metadata.create_all` → создание ENUM → `InsufficientPrivilegeError`
    - Аналогичная проблема с последовательностями (sequences) — не выданы права на `ALL SEQUENCES`
- **Исправление (подтверждено: `.github/workflows/deploy.yml`):**
  1. `GRANT USAGE` → `GRANT USAGE, CREATE ON SCHEMA public` для всех трёх ролей (`tg_user`, `vk_user`, `max_user`)
  2. Добавлены `GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public` для всех трёх ролей в шаг «Права на таблицы»
- **Коммит:** `2a8c29b`
- **Связанные ошибки:** нет

---

## 024 — UnboundLocalError: cannot access local variable 'select' where it is not associated with a value

- **Дата:** 2026-07-12
- **Статус:** ✅ Исправлено
- **Описание:** При нажатии на кнопки админ-меню (кроме stats_all) бот падает с `UnboundLocalError: cannot access local variable 'select' where it is not associated with a value`. Traceback указывает на строку `select(Channel).where(...)` в `cmd_callback`.
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py`, callback handler):**
    - `from sqlalchemy import select, func` стоял внутри `if action == "stats_all":` — это сделало `select` локальной переменной для всей функции `cmd_callback`
    - Python при компиляции функции видит `select` в любом блоке как локальную переменную (из-за присваивания через import в одном из блоков)
    - Все остальные `if action == ...` блоки (check_expired, list_channels, health и др.) используют `select` без собственного импорта → `UnboundLocalError`
- **Исправление (подтверждено: `app/platforms/telegram/bot.py`):**
  1. Перенесён `from sqlalchemy import select, func` на верхний уровень файла (после стандартных импортов)
  2. Убран дублирующийся локальный импорт внутри `if action == "stats_all"`
- **Коммит:** `305691e`
- **Связанные ошибки:** нет

---

## 025 — NameError: TicketStatus / PaymentStatus / User / Event / Ticket / Payment не импортированы

- **Дата:** 2026-07-12
- **Статус:** ✅ Исправлено
- **Описание:** В `bot.py` отсутствовали импорты `TicketStatus`, `PaymentStatus`, `User`, `Event`, `Ticket`, `Payment` из `app.core.models`. При попытке выполнить действия, использующие эти имена (статистика, отмена билета, инфо о канале и т.д.), бот падал бы с `NameError`.
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py:15`):**
    - Импорт был только `from app.core.models import PlatformType, Channel`
    - `User`, `Event`, `Ticket`, `Payment` использовались в запросах SQLAlchemy (`select(func.count()).select_from(User)` и т.д.)
    - `TicketStatus`, `PaymentStatus` использовались как значения enum (`TicketStatus.active`, `PaymentStatus.completed`)
    - Баг не проявлялся раньше, т.к. код до недавних правок не использовал эти имена напрямую
- **Исправление (подтверждено: `app/platforms/telegram/bot.py:15-18`):**
  Расширен импорт:
  ```python
  from app.core.models import (
      PlatformType, Channel, User, Event, Ticket, Payment,
      TicketStatus, PaymentStatus,
  )
  ```
- **Коммит:** `e2fa6a8`
- **Связанные ошибки:** нет

---

## 026 — Каналу при подписке назначается суперадмин вместо реального админа

- **Дата:** 2026-07-13
- **Статус:** ✅ Исправлено
- **Описание:** Super-admin подписывает канал через `/subscribe @channel 30` (или через FSM-кнопку «Подписать»). Канал регистрируется в БД с `admin_telegram_user_id = super_admin_id`. Реальный админ канала добавляет бота, но не может управлять каналом — запись уже занята суперадмином. Дополнительно: при добавлении бота в канал создаётся дубликат записи, т.к. поиск по числовому ID не находит запись, созданную с @username.
- **Причина (подтверждено: `app/platforms/telegram/bot.py`):**
  - Две несогласованные точки создания/обновления записей канала:
    1. `/subscribe` при создании нового канала записывает `admin_telegram_user_id = str(message.from_user.id)` (суперадмин)
    2. `on_chat_member_update` ищет канал по `str(chat.id)` — не находит запись с @username → создаёт дубликат
  - Формат ключа не совпадает: `/subscribe` сохраняет @username, `on_chat_member_update` ищет по числовому ID
- **Исправление (подтверждено: `app/platforms/telegram/bot.py`):**
  1. `/subscribe` (text command, ~line 1379 и FSM handler, ~line 857): `admin_telegram_user_id=str(message.from_user.id)` → `admin_telegram_user_id=""` (пустая строка). Реальный админ определится, когда добавит бота.
  2. `on_chat_member_update` (~line 1486): после неудачного поиска по `str(chat.id)` добавлен fallback — поиск по `chat.username` (если есть @username у канала). Если запись найдена — обновляется: числовой ID, admin_telegram_user_id, title.
  3. Условие замены `telegram_channel_id` расширено: теперь также срабатывает при `channel.telegram_channel_id.startswith("@")`.
- **Коммит:** —

---

## 027 — UnboundLocalError: cannot access local variable 'datetime' where it is not associated with a value

- **Дата:** 2026-07-16 (обновлено 2026-07-16)
- **Статус:** ✅ Исправлено
- **Описание:** В runtime при нажатии inline-кнопки «Общая статистика» бот падает с ошибкой:
  ```
  UnboundLocalError: cannot access local variable 'datetime' where it is not associated with a value
  Traceback (most recent call last):
    File "/app/app/platforms/telegram/bot.py", line 1684, in cmd_callback
      select(func.count()).select_from(Event).where(Event.date >= datetime.now(timezone.utc))
                                                                  ^^^^^^^^
  UnboundLocalError: cannot access local variable 'datetime' where it is not associated with a value
  ```
  Ошибка повторяется дважды (на двух разных сессиях).
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py:1596`):**
    - Внутри функции `cmd_callback` (строка 1596) есть локальный импорт `from datetime import datetime` в ветке `if action == "admin:confirm_create":`
    - Python при компиляции видит присваивание `datetime` через импорт в одном из блоков функции — `datetime` становится локальной переменной для всей функции
    - Когда срабатывает ветка `if action == "stats_all":` (строка 1685), код использует `datetime.now(timezone.utc)` — но локальная `datetime` ещё не присвоена (ветка `admin:confirm_create` не выполнялась), возникает `UnboundLocalError`
  - **Корень проблемы:** Предыдущее «исправление» (добавление `from datetime import datetime, timezone` на верхний уровень) было недостаточным. Локальный импорт на строке 1596 остался и продолжал затенять глобальный `datetime`, потому что Python видит `import` внутри функции как присваивание локальной переменной, что делает `datetime` локальным для всей функции `cmd_callback` независимо от того, выполнилась ли эта ветка.
- **Исправление (подтверждено: `app/platforms/telegram/bot.py:1596,593`):**
  1. Удалена строка 1596 `from datetime import datetime` внутри `cmd_callback`. Глобальный импорт `from datetime import datetime, timezone` (строка 2) уже предоставляет оба имени. `datetime.fromisoformat()` — классовый метод, доступный через глобальный `datetime`.
  2. Дополнительно удалена строка 593 `from sqlalchemy import select, func` внутри `sa_channel_info()` — избыточный импорт, т.к. `select` и `func` уже импортированы глобально на строке 12. Этот импорт не вызывал ошибки (использовался сразу после объявления), но являлся техническим долгом.
  Аналогичная проблема (локальный импорт, затеняющий глобальный) была ранее с #024 (`select`), исправлена тем же способом.
- **Коммит:** —

---

## 028 — CI test_admin_create_event_unauthorized падает: _get_admin_channel требует БД (InvalidCatalogNameError)

- **Дата:** 2026-07-16
- **Статус:** ✅ Исправлено
- **Описание:** На CI тест `test_admin_create_event_unauthorized` падает с ошибкой:
  ```
  asyncpg.exceptions.InvalidCatalogNameError: database "ticketbot" does not exist
  ```
  Остальные 83 теста проходят. Ошибка возникает, потому что `admin_create_event()` вызывает `_is_channel_admin()`, который при незаадминенном пользователе вызывает `_get_admin_channel()`, который открывает реальную сессию БД через `async_session_factory()`, использующую `settings.database_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/ticketbot"`. В CI есть только `ticketbot_test`.
- **Причина (подтверждено: `tests/test_telegram_bot.py:190-199`):**
  - После замены `_is_admin` на `_is_channel_admin` в `admin_create_event()` (строка 1048), метод вызывает `_get_admin_channel()` (транзитивно через `_is_channel_admin` на строке 426), который требует реальную БД
  - Тест не мокает `_get_admin_channel`, в отличие от `test_admin_menu_unauthorized` (строка 167), который мокает этот же метод
  - `async_session_factory` на строке 8 `database.py` использует дефолтный URL `ticketbot`, а в CI существует только `ticketbot_test`
- **Исправление (подтверждено: `tests/test_telegram_bot.py:194`):**
  Добавлен `patch.object(telegram_bot, "_get_admin_channel", new_callable=AsyncMock, return_value=None)`:
  ```python
  with patch.object(telegram_bot, "_get_admin_channel", new_callable=AsyncMock, return_value=None):
      await telegram_bot.admin_create_event(mock_message, mock_state)
  ```
  По аналогии с `test_admin_menu_unauthorized` (строка 167). Теперь `_is_channel_admin` вызывает замоканный `_get_admin_channel`, получает None, возвращает False, и хендлер корректно отвечает «У вас нет доступа», без обращения к БД.
- **Связанные ошибки:** #021 (аналогичная проблема для `test_admin_menu_unauthorized`)

---

## 029 — on_chat_member_update не обрабатывает статус "administrator" (каналы)

- **Дата:** 2026-07-16
- **Статус:** ✅ Исправлено
- **Описание:** Admin канала добавляет бота в канал, но `on_chat_member_update()` не срабатывает. `admin_telegram_user_id` остаётся пустым, админ не может создавать мероприятия — «У вас нет доступа к панели администратора». При этом `/create_event` в канале просто игнорируется (нет channel_post хендлера).
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py:1513`):** Проверка `new_chat_member.status == "member"`. В Telegram каналах бот всегда добавляется как администратор (`status = "administrator"`), а не как участник (`"member"`). Проверка никогда не совпадает.
  - Дополнительно: при вводе команд не было проверки через Telegram API, что пользователь действительно является админом канала сейчас. Система полностью доверяла БД.
- **Исправление (подтверждено: `app/platforms/telegram/bot.py`):**
  1. `on_chat_member_update`: `== "member"` → `in ("member", "administrator")` (строка 1513)
  2. Добавлен метод `_verify_channel_admin()` (строка 390) — вызывает `bot.get_chat_member()` через Telegram API
  3. В `_get_admin_channel()` после проверки подписки добавляется Telegram-верификация (строка 412)
  4. При провале верификации — подписка деактивируется, пользователь видит «Нет канала с активной подпиской»
  5. Fallback-каналы (unassigned, legacy) также проверяются перед привязкой (строки 430, 450)
  6. Устранены двойные вызовы `_get_admin_channel()` в 5 хендлерах
  7. В тесты добавлен мок `get_chat_member` в fixture `telegram_bot`
- **Связанные ошибки:** #021, #028

---

## 030 — _verify_channel_admin деактивирует подписку при временной ошибке Telegram API

- **Дата:** 2026-07-16
- **Статус:** ✅ Исправлено
- **Описание:** Админ канала вводит `/create_event` — бот отвечает «У вас нет канала с активной подпиской», хотя канал есть в БД и подписка активна. Причина: `_verify_channel_admin()` вызывает `bot.get_chat_member()`, который падает с ошибкой (неверный формат ID, сетевой сбой, rate limit), и подписка необратимо деактивируется.
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py:390-403`, `412-421`):**
    1. `_verify_channel_admin()` возвращал `False` на любую ошибку API (exception)
    2. `_get_admin_channel()` трактовал `False` как «пользователь не админ» → вызывал `deactivate_subscription()`
    3. Следующий вызов уже не находил канал (подписка удалена)
  - **Дополнительно:** `on_chat_member_update()` не обновлял `telegram_channel_id`, если super-admin ввёл голое число (`1234567890`) без `-100` префикса. `get_chat_member(chat_id="1234567890")` падал с ошибкой.
- **Исправление (подтверждено: `app/platforms/telegram/bot.py`):**
  1. `_verify_channel_admin()` возвращает `True` (админ), `False` (не админ, подтверждено API), `None` (ошибка API)
  2. `_get_admin_channel()`: 
     - `True` → возвращает канал
     - `False` → деактивирует подписку (подтверждено API)
     - `None` → не трогает подписку, логирует, пробует следующий канал
  3. `on_chat_member_update()`: добавлена проверка `channel.telegram_channel_id.lstrip("-").isdigit()` — исправляет голые числа
- **Связанные ошибки:** #029

---

## 031 — При ошибке Telegram API _get_admin_channel пропускает канал, а не возвращает его

- **Дата:** 2026-07-18
- **Статус:** ✅ Исправлено
- **Описание:** Пользователь имеет канал в БД с активной подпиской, бот добавлен в канал. Но при `/create_event` получает «❌ У вас нет канала с активной подпиской.».
  - `/create_event` не появляется в Menu Button (админские команды не видны)
  - При ручном вводе `/create_event` — отказ
  - При этом канал в `/my_channels` отображается, подписка активна
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py:471-476`):** После фикса #059 `_verify_channel_admin()` возвращает `None` при ошибке API. Код в `_get_admin_channel()` при `None` **пропускает канал** (логирует и идёт к следующему), но не возвращает его. Если канал один — цикл завершается без результата, fallback'ов для обычного админа нет.
  - При этом подписка в БД **остаётся активной** — проблема не в подписке, а в том, что `_get_admin_channel()` не доверяет БД при ошибке API.
  - Причины ошибки API: может быть сетевой сбой, rate limit, временная недоступность Telegram API — любая причина, не связанная с правами пользователя.
- **Исправление (подтверждено: `app/platforms/telegram/bot.py:478-484`):**
  ```python
  # verified is None — ошибка API (сеть, таймаут, формат ID).
  # Доверяем БД: подписка активна, пользователь админ.
  logger.warning(...)
  return channel
  ```
  При `None` (ошибка API) — возвращаем канал. Подписку не деактивируем, действие не блокируем.
  Логируем предупреждение, чтобы админ знал о проблеме.
- **Связанные ошибки:** #030 (это доработка фикса #030)

---

## 032 — Кнопка "Сменить админа" показывает usage вместо FSM-диалога

- **Дата:** 2026-07-21
- **Статус:** ✅ Исправлено
- **Описание:** Super-admin нажимает кнопку «🔄 Сменить админа» в админ-меню. Вместо FSM-диалога (ввод @channel и new_user_id) получает сообщение: «Использование: /change_admin <channel_id> <new_user_id>».
- **Анализ:**
  - **Был вызов:**

    Пользователь кликает кнопку с `callback_data="admin_menu:change_admin"`. Callback-хендлер `cmd_callback` (строка 1889) обрабатывает её и попадает в блок `input_actions` (строка 2215-2227): устанавливает `AwaitingAdminInput.text` state и отправляет приглашение ввести параметры.

    Однако если пользователь после этого (или вместо этого) вводит текстовую команду `/change_admin`, срабатывает **не** FSM-хендлер `_handle_admin_input`, а **текстовый** команда-хендлер `sa_change_admin`, потому что:

    1. `sa_change_admin` зарегистрирован на строке 125: `self.dp.message.register(self.sa_change_admin, Command("change_admin"))` — **без StateFilter**
    2. `_handle_admin_input` зарегистрирован на строке 131: `self.dp.message.register(self._handle_admin_input, StateFilter(AwaitingAdminInput.text))`
    3. aiogram 3 проверяет хендлеры в порядке регистрации. Первый подошедший — `sa_change_admin` (строка 125), так как у него нет StateFilter, и он подходит для любого сообщения `/change_admin`, включая те, что пришли во время FSM.
    4. В `sa_change_admin` (строка 1197-1224): `args = message.text.split(maxsplit=2)` → если аргументов меньше 3, выводится usage-сообщение (строка 1204-1205).

  - **Корень проблемы (подтверждено: `app/platforms/telegram/bot.py:125`):**

    `Command("change_admin")` не имеет `StateFilter`, поэтому перехватывает сообщения `/change_admin` даже когда пользователь находится в состоянии `AwaitingAdminInput.text`. FSM-хендлер `_handle_admin_input` (строка 131) не успевает сработать.

  - **Аналогичная проблема** у других команд, имеющих FSM-альтернативу:
    - `sa_channel_info` (строка 119) — `Command("channel_info")`, без StateFilter
    - `sa_user_info` (строка 120) — `Command("user_info")`, без StateFilter
    - `sa_admin_cancel` (строка 121) — `Command("admin_cancel")`, без StateFilter
    - `admin_subscribe` (строка 112) — `Command("subscribe")`, без StateFilter
    - `admin_unsubscribe` (строка 113) — `Command("unsubscribe")`, без StateFilter

- **Исправление (подтверждено: `app/platforms/telegram/bot.py:125`):**

  Добавить `StateFilter(None)` в регистрацию `sa_change_admin`, чтобы хендлер срабатывал **только** когда пользователь не находится ни в одном FSM-состоянии. Когда пользователь в `AwaitingAdminInput.text`, сообщение `/change_admin` будет обработано FSM-хендлером `_handle_admin_input`.

  ```python
  # Было (строка 125):
  self.dp.message.register(self.sa_change_admin, Command("change_admin"))

  # Стало:
  self.dp.message.register(self.sa_change_admin, Command("change_admin"), StateFilter(None))
  ```

  **Дополнительно:** Аналогично стоит поправить регистрацию других команда-хендлеров, конфликтующих с FSM:
  - `sa_channel_info` (строка 119)
  - `sa_user_info` (строка 120)
  - `sa_admin_cancel` (строка 121)
  - `admin_subscribe` (строка 112)
  - `admin_unsubscribe` (строка 113)

- **Связанные ошибки:** нет
---

## 038 — Инлайн-кнопка "Список каналов" не реагирует на нажатие

- **Дата:** 2026-07-21
- **Статус:** ✅ Исправлено
- **Описание:** Super-admin нажимает кнопку "📋 Список каналов" в админ-меню. Спиннер на кнопке гаснет, но сообщение не меняется — создаётся ощущение, что кнопка "не реагирует".
- **Анализ:**
  - **Корень проблемы (подтверждено: `app/platforms/telegram/bot.py:2036`):**
    
    `callback.answer()` вызывался ДО выполнения работы (на строке 2036, сразу при входе в `admin_menu:`). Если последующий код (`edit()` на строке 2140) выбрасывал исключение (ошибка БД, таймаут, превышение лимита символов), Telegram уже убирал спиннер, а сообщение не менялось.

    Для `stats_all` (простые `SELECT COUNT`) исключений не было — поэтому она работала. Для `list_channels` (запрос всех каналов + N запросов админов) вероятность ошибки выше.

  - **Дополнительно:** Во всём блоке `admin_menu:` не было вызовов `callback.answer()` в return-путях — только один вызов в начале. Если исключение происходило после `callback.answer()`, пользователь видел только исчезновение спиннера.

- **Исправление:**
  1. Убран ранний `await callback.answer()` на строке 2036
  2. `callback.answer()` теперь вызывается ПОСЛЕ успешного `edit()` в каждом action-хендлере
  3. `list_channels` обёрнут в `try/except` с логированием ошибки и показом сообщения пользователю
  4. `callback.answer()` добавлен во все return-пути (включая ошибки доступа, отсутствие данных и FSM-старт)
  5. Затронутые хендлеры: `back`, `stats_all`, `check_expired`, `list_channels`, `health`, `events_all`, `my_channels`, `create_event`, `broadcast`, `input_actions`

---

## 039 — Lazy `from sqlalchemy import select` вызывает UnboundLocalError в кнопках админ-меню

- **Дата:** 2026-07-21
- **Статус:** ✅ Исправлено
- **Описание:** Кнопки «📋 Список каналов», «📊 Общая статистика» и «🔍 Проверить подписки» в админ-меню показывают «❌ Ошибка». В логах `UnboundLocalError: cannot access local variable 'select' where it is not associated with a value`.

- **Анализ:**
  - **Корень проблемы (подтверждено: `app/platforms/telegram/bot.py:2293`, семантика Python):**

    Внутри `cmd_callback` на строке 2293 был `from sqlalchemy import select` внутри блока `if event_ids:`:
    ```python
    if event_ids:
        async with async_session_factory() as session:
            from sqlalchemy import select  # ← lazy import
            stmt = select(Event).where(...)
    ```

    **Правило Python:** если в теле функции есть `import name` (или другое присваивание `name =`) хотя бы в одной ветке, **Python считает `name` локальной переменной во всей функции** — даже в тех ветках, которые идут ДО этого импорта.

    Хендлеры, выполняющиеся РАНЬШЕ (строки 2070-2140), используют `select()`. Python видит `select` как локальную переменную, но она ещё не инициализирована → `UnboundLocalError`.

  - **Почему другие кнопки работали:** не используют `select()` напрямую, либо уходят в отдельные методы (FSM).

  - **Почему глобальный импорт (строка 12) не спасал:** Python внутри `cmd_callback` игнорирует его из-за локального импорта на строке 2293.

- **Исправление (подтверждено: `app/platforms/telegram/bot.py:2293`):**

    Удалена строка `from sqlalchemy import select`. `select` уже импортирован глобально на строке 12. После удаления Python использует глобальный `select` во всей `cmd_callback`.

    ```python
    # Было:
    if event_ids:
        async with async_session_factory() as session:
            from sqlalchemy import select
            stmt = select(Event).where(...)
    # Стало:
    if event_ids:
        async with async_session_factory() as session:
            stmt = select(Event).where(...)
    ```

- **Добавлены тесты** (`TestAdminMenuSelectScope`):
  - `test_list_channels_success` / `test_list_channels_empty`
  - `test_stats_all_success`
  - `test_check_expired_success` / `test_check_expired_deactivates`
  - `test_admin_ev_page_pagination`

- **Связанные ошибки:** #038 (логировал ошибку `list_channels`, но не устранил корень)

---

## 040 — При активации подписки на новый канал админы не синхронизируются (прочерк в списке)

- **Дата:** 2026-07-26
- **Статус:** ✅ Исправлено
- **Описание:** Super-admin подписывает канал через `/subscribe @channel 30` (или FSM-кнопку «Подписать»). Канал создаётся в БД, но при просмотре «📋 Список каналов» у канала в графе «Админы» стоит прочерк. Админ канала не может управлять мероприятиями.
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py:1154-1166`):**
    - При создании канала через подписку (else-ветка, когда канала нет в БД по @username) код создаёт канал, активирует подписку, но НЕ вызывает `get_chat_administrators` + `sync_admins`.
    - Аналогичная проблема во втором else-блоке в `admin_subscribe()` (строка 1915-1929).
    - В `if channel:`-ветках (канал уже есть в БД) синхронизация админов есть.
    - Логи: `subscription.activated` есть, `channel_admins.synced` нет.
- **Исправление (подтверждено: `app/platforms/telegram/bot.py:1154-1166` и `1915-1929`):**
  В обе else-ветки добавлен блок:
  ```python
  admin_svc = ChannelAdminService(session)
  try:
      admins = await self.bot.get_chat_administrators(chat_id=channel_telegram_id)
      admin_ids = [str(a.user.id) for a in admins
                   if a.status in ("creator", "administrator") and not a.user.is_bot]
      if admin_ids:
          await admin_svc.sync_admins(channel.id, admin_ids)
          channel.admin_telegram_user_id = admin_ids[0]
  except Exception:
      pass  # бот не в канале — норм, сообщение подскажет
  ```
- **Связанные ошибки:** нет

## 041 — CI-деплой падает: `service "nginx" depends on undefined service "web": invalid compose project`

- **Дата:** 2026-08-05
- **Статус:** 🔄 Требует доработки
- **Описание:** Пуш в `dev` (коммит `01e1799`, trigger deploy) — workflow `deploy.yml` падает на шаге «🏗️ Собрать новые образы» с ошибкой:
  ```
  service "nginx" depends on undefined service "web": invalid compose project
  ```
  Job `test` проходит (123 теста), job `deploy` падает → контейнеры ticketbot не поднимаются.
- **Анализ:**
  - **Подтверждено (`docker-compose.yml:79-93`):** сервис `web` объявлен внутри `profiles: all`.
  - **Подтверждено (`deploy/docker-compose.nginx.yml:15`):** сервис `nginx` имеет `depends_on: web`.
  - **Подтверждено (`.github/workflows/deploy.yml:9`):** `COMPOSE_FILES` включает `deploy/docker-compose.nginx.yml`, но ни build, ни up не активируют профиль `all` (`--profile all` отсутствует). Без активации профиля compose не видит `web` → `invalid compose project`.
  - Проверено на VPS: `docker compose ... build` (без `--profile all`) падает с той же ошибкой; `docker compose ... --profile all build` — успешно, все 4 образа (`web`, `telegram`, `vk`, `max`) собираются.
- **Исправление (применено, коммит `7b6da4b`):**
  Выбран безопасный вариант — **`web` вынесен из `profiles: all`** в `docker-compose.yml` (теперь обычный сервис, виден nginx всегда). Дополнительно в `.dockerignore` добавлено `.env*` (с `!*.env.example`), чтобы секреты из `.env.telegram/.env.vk/.env.max` не попадали в образы. `--profile all` не нужен.
- **Связанные ошибки:** нет

## 042 — CI-деплой: шаг SSL-сертификата падает (сломанный перенос строки в docker run)

- **Дата:** 2026-08-05
- **Статус:** ✅ Исправлено
- **Описание:** После фикса #041 деплой доходит до шага «🔐 Получить SSL-сертификат (Let's Encrypt)», но сертификат не выдаётся (`/etc/letsencrypt/live/pochtibot.online` отсутствует), nginx не поднимается, порты 80/443 закрыты.
- **Анализ:**
  - **Подтверждено (`.github/workflows/deploy.yml:280-284`):** команда `docker run` была записана с обратными слэшами `\` в конце строк. В block-scalar (`run: |`) эти слэши — часть текста, поэтому команда собиралась в одну длинную строку и падала на неизвестном аргументе `-v`. `|| true` в конце проглатывал ошибку → шаг формально «completed», но сертификат не выдан.
  - Проверено через `cat -A`: слэши реально присутствуют как `$` в конце строк.
  - Шаг «Накатить миграции» (строки 177-179) содержит легитимные слэши-переносы внутри `docker compose run` — их не трогать.
- **Исправление (применено, коммит `394076c`):**
  Команда объединена в одну строку: `docker run --rm -v /etc/letsencrypt:/etc/letsencrypt -v /var/www/certbot:/var/www/certbot -p 80:80 certbot/certbot:latest certonly --standalone -d pochtibot.online --non-interactive --agree-tos -m admin@pochtibot.online --preferred-challenges http || true`
- **Связанные ошибки:** #041

## 043 — Веб-кабинет не открывается на Telegram Desktop (initData пуст)

- **Дата:** 2026-08-05
- **Статус:** ✅ Исправлено
- **Описание:** Telegram Mini App (личный кабинет) работает на мобильном Telegram, но на **Telegram Desktop** показывает экран «Откройте кабинет в Telegram» — `/api/me` не вызывается, список пуст.
- **Анализ:**
  - **Подтверждено (логи web на VPS):** телефон делает `/api/me 200` + `/api/events 200`, десктоп — только `GET /` + `static`, обрыв без `/api/me`.
  - **Подтверждено (`app/web/static/app.js:32-41`):** initData берётся только из `window.Telegram.WebApp.initData`. Если SDK нет → `state.initData = ""`.
  - **Подтверждено (URL с десктопа, диагностика экрана):** `window.Telegram: нет`, `WebApp: НЕТ`, но в URL-фрагменте есть `#tgWebAppData=user=...&auth_date=...&signature=...&hash=...` и `tgWebAppPlatform=tdesktop`. То есть Telegram Desktop открывает Mini App как обычную страницу и **передаёт данные через URL-хэш**, но **не внедряет `window.Telegram`** (в отличие от мобильного клиента). Скрипт `telegram-web-app.js` создаёт SDK только при внедрении интерфейса клиентом.
- **Исправление (применено, `app/web/static/app.js`):**
  Добавлен фоллбек `extractInitDataFromUrl()`: если `window.Telegram` нет, initData извлекается из `#tgWebAppData=` в URL-фрагменте. Это тот же initData (user/hash/auth_date), который `validate_init_data` на бэкенде уже умеет парсить (HMAC-проверка проходит).
- **Связанные ошибки:** нет

## 044 — Кнопка «Добавить канал» не видна, когда каналов нет

- **Дата:** 2026-08-05
- **Статус:** ✅ Исправлено
- **Описание:** На вкладке «Каналы» при пустом списке видна только надпись «Нет каналов» — кнопка «➕ Добавить канал» не отображается. Пользователь-супер-админ не может добавить первый канал.
- **Анализ:**
  - **Подтверждено (`app/web/static/app.js`, `renderAdminChannels`):** в ветке `if (!channels || channels.length === 0)` рендерится только empty-state и `return`, кнопка «➕ Добавить канал» добавлена только в ветку, когда каналы есть. Парадокс: кнопка нужна именно при пустом списке.
- **Исправление (применено, `app/web/static/app.js`):**
  В empty-state-ветку добавлена кнопка «➕ Добавить канал` перед надписью «Нет каналов».
- **Связанные ошибки:** нет

## 045 — Харнесс имитации: TokenValidationError (test:token не проходит валидацию aiogram)

- **Дата:** 2026-08-06
- **Статус:** ✅ Исправлено
- **Описание:** При построении харнесса имитации (Подход A) реальный `Bot(token="test:token", session=fake)` падает с `aiogram.utils.token.TokenValidationError: Token is invalid!`. В обычных тестах бот-хендлеров `Bot` замокан (`AsyncMock`), поэтому токен не валидируется; в харнессе используется настоящий `Bot` — валидация активна.
- **Анализ:**
  - **Подтверждено (`aiogram/utils/token.py`):** валидация требует формат `left:right`, где `left` — все цифры, `right` непустой. `"test:token"` → left="test" не числовой → ошибка.
  - **Подтверждено (`tests/harness.py`):** тест использовал `Bot(token="test:token", ...)`.
- **Исправление (применено, `tests/harness.py`):**
  Токен заменён на валидный формат `"123456789:TESTTOKEN"` (число:буквы). Обновлено в тестах.
- **Связанные ошибки:** нет

## 046 — Харнесс имитации: feed_update вне контекста патчей (фабрика БД откатывалась)

- **Дата:** 2026-08-06
- **Статус:** ✅ Исправлено
- **Описание:** При построении харнесса имитации (Подход A) `Dispatcher.feed_update` вызывался **после** выхода из `with patch(...)`-блока. Патчи `async_session_factory` откатывались → хендлеры открывали сессию на оригинальной фабрике (`settings.database_url` → host `db`, нерезолвится локально) → `socket.gaierror: Name or service not known`.
- **Анализ:**
  - **Подтверждено (тест `tests/test_telegram_sim.py`):** конструктор `TelegramBot()` и `dp.feed_update()` были в разных блоках — патчи жили только на время конструктора.
  - **Подтверждено дебагом:** `SELECT 1` через патчнутую фабрику внутри `with` работал; вне `with` — падал на `gaierror` (host `db`).
  - Диагностика усложнилась: `async_session_factory` — объект `async_sessionmaker`; сравнение `is` внутри контекста давало True, но реальный вызов вне контекста шёл через оригинал.
- **Исправление (применено, `tests/test_telegram_sim.py`):**
  `feed_update` (и весь сценарий) перенесён внутрь `with patch(...)`-блока — патчи активны на всё время конвейера. Использован `patch.object` вместо `patch`-строки.
- **Связанные ошибки:** #045

## 047 — Харнесс имитации: TelegramBot создавал реальный Bot (DM уходил в сеть)

- **Дата:** 2026-08-06
- **Статус:** ✅ Исправлено
- **Описание:** В харнессе имитации `_make_bot` патчил `async_session_factory`, но **не патчил `Bot`**. При `TelegramBot()` создавался реальный `aiogram.Bot` с `AiohttpSession` — `send_message` (DM после покупки) уходил в сеть и падал (поймано `except Exception`), поэтому DM-уведомления не появлялись в `fake.calls`. Ответы `callback.message.edit_text` шли через fake (у Message из Update `.bot` = тестовый bot), что маскировало проблему.
- **Анализ:**
  - **Подтверждено (тест `test_inline_buy_legacy`):** `assert tb.bot is bot` → `False` (разные объекты). `fake.calls` содержал только `AnswerCallbackQuery`, `EditMessageText` — DM-`SendMessage` отсутствовал, хотя edit_text содержал код.
  - **Подтверждено (debug):** прямой `bot.send_message(...)` через fake работает; через `tb.bot` — падал (сетевой AiohttpSession).
  - Причина: в `_make_bot` был запатчен `async_session_factory`, но не `Bot` (в отличие от базовых тестов `TestTelegramSimulation`, где `patch("...Bot")` был).
- **Исправление (применено, `tests/test_telegram_sim.py`):**
  В `_make_bot` добавлен `patch.object(_bot_mod, "Bot", lambda token, **kw: bot)` — `TelegramBot()` теперь получает тестового бота с фейк-сессией.
- **Связанные ошибки:** #046

## 048 — Сквозной web-flow: TestClient зависал (кросс-loop с db_session)

- **Дата:** 2026-08-06
- **Статус:** ✅ Исправлено
- **Описание:** Фикстура `db_client` для сквозного web-flow на реальной БД использовала `fastapi.testclient.TestClient` + передачу `db_session` (созданного в pytest event loop). Тест **зависал** (выхода не было до timeout). Причина — кросс-loop: TestClient запускает приложение в собственном event loop (portal), а `AsyncSession` из pytest loop несовместим.
- **Анализ:**
  - **Подтверждено:** `TestClient` использует `portal.call(self.app, ...)` в отдельном loop (starlette/testclient.py); `db_session` создан в loop pytest. Доступ к `AsyncSession` из чужого loop → дедлок.
  - Дополнительно: `admin_auth` мокает `UserService.get_or_create` на уровне класса — ломало и `buy` (реальный юзер не создавался, FK violation на tickets).
- **Исправление (применено, `tests/conftest.py` + `tests/test_web_api.py`):**
  - `db_client` переписан как `pytest_asyncio` фикстура, возвращающая `httpx.AsyncClient(transport=ASGITransport(app))` — тот же event loop, что и `db_session`.
  - В сквозном тесте вместо `admin_auth` (мокает UserService) — юзер 12345 назначен channel-admin через `ChannelAdminService.sync_admins`, роль через реальные зависимости.
- **Связанные ошибки:** нет

## 049 — VDS: postgres (ticketbot-db) аномально грузил CPU 333% без запросов

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено (перезапуск контейнера)
- **Описание:** На VDS пришло уведомление о превышении лимитов. `ticketbot-db` (PostgreSQL) жрал **333% CPU** (load ~4.7 при 4 ядрах) и **2.5 GiB RAM**. При этом:
  - Активных запросов нет (`pg_stat_activity` пуст)
  - Автовакуум не работал (dead_tup=0, xid_age мал)
  - `restarts=0`, uptime контейнера нормальный
  - 4 потока postgres (PID внутри контейнера) активно крутили CPU в цикле
- **Анализ:**
  - **Подтверждено (`top -H`):** 4 треда postgres `R` состояние, ~54+54+45+18% CPU каждый
  - **Подтверждено (`pg_stat_activity`):** нет ни активных клиентских запросов, ни autovacuum — аномалия на уровне потоков (спин-луп / зависший background worker)
  - Все остальные контейнеры (seeker, ticketbot-web/telegram) — <1.5% CPU
- **Исправление (применено):**
  `docker restart ticketbot-db` — сбросил зависшие потоки. CPU → 0.04%, RAM → 17 MB, load → 2.34. Данные на volume не пострадали.
- **Связанные ошибки:** нет

## 050 — Owner-мероприятия недоступны организатору на админ-действиях (403)

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** После введения роли «Организатор» и `Event.owner_user_id` организатор **без канала** может создать своё мероприятие (owner), но не может им управлять: статистика, toggle, update, delete, publish, repost, билеты → **403**.
- **Анализ:**
  - **Подтверждено (QA-агент, сквозные e2e на реальной БД):** все админ-эндпоинты гейтят `current.can_manage(event.channel_id)`. Для owner-мероприятия `channel_id=None` → `can_manage(None)` всегда False для не-суперадмина → 403 владельцу.
  - **Подтверждено (`app/web/routes.py`):** работает только `admin_get_event` (owner-проверка есть); остальные (update 447, toggle 477, delete 501, stats 576, tickets 599, csv 623, cancel 707, qr 1186, publish 528, repost 553) — только can_manage.
- **Исправление (не применено):**
  Во всех гейтах добавить owner-проверку: `event.channel_id is None and event.owner_user_id == current.user_id`, по образцу `admin_get_event`.
- **Связанные ошибки:** нет

## 051 — CI: ModuleNotFoundError: No module named 'dateutil' (python-dateutil не в deps)

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** CI-деплой (`d1ccedf`) упал на шаге «Запуск тестов»: `ImportError while loading conftest / ModuleNotFoundError: No module named 'dateutil'`. Локально всё зелёное (262 теста).
- **Анализ:**
  - **Подтверждено (лог CI через API):** `ImportError ... No module named 'dateutil'` при загрузке `tests/conftest.py`.
  - **Подтверждено (`app/core/services.py`):** в `_add_period` используется `from dateutil.relativedelta import relativedelta` (добавлено в фиче «смена типа+срока подписки»), но `python-dateutil` **не был указан в `pyproject.toml` dependencies**. Локально пакет тянулся транзитивно (через другой пакет), а в чистой установке CI (`pip install -e ".[dev]"`) отсутствовал.
- **Исправление (применено, `pyproject.toml`):**
  Добавлен `"python-dateutil>=2.9"` в `[project.dependencies]`.
- **Связанные ошибки:** нет

## 052 — Организатор без канала не мог выдавать пригласительные (403)

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** После введения роли «Организатор» (owner-мероприятия) организатор БЕЗ канала не мог выдавать пригласительные: даже с pro-подпиской и квотой получал 403 «Пригласительные выдаёт только админ канала». Обнаружено QA-агентром при сквозном тестировании полного цикла организатора.
- **Анализ:**
  - **Подтверждено (`app/web/routes.py`):** `_can_issue_invites` допускал только `event.channel_id in managed_channel_ids` — для owner-события (`channel_id=None`) → False.
  - **Подтверждено (эндпоинт issue_invite):** pro-гейт вызывал `ChannelService.require_feature(event.channel_id, "invite_tickets")` — для owner-события `channel_id=None` → False.
- **Исправление (применено, `app/web/routes.py`):**
  - `_can_issue_invites`: для owner-мероприятия → `event.owner_user_id == current.user_id` (организатор без канала).
  - Pro-гейт: если `channel_id` есть → `ChannelService.require_feature`; если нет (owner) → `UserService.require_feature(event.owner_user_id, "invite_tickets")`.
  - Тест обновлён: `test_owner_event_invites_blocked` → `test_owner_event_invites_allowed` (201).
- **Связанные ошибки:** #050

## 053 — Замечания QA: checkin без проверки доступа + SAWarning на owner-событиях

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** QA-агент при сквозном тестировании организатора отметил 2 замечания (не блокеры): (C) любой админ мог checkin/validate чужой билет (нет проверки доступа к мероприятию); (B) SAWarning при списке owner-событий (`channel_svc.get_by_id(None)`).
- **Анализ:**
  - **C (подтверждено, `app/web/routes.py` admin_checkin_ticket):** после `check_in_by_code` не загружалось событие и не проверялся доступ. Чужой организатор мог отметить вход по чужому коду.
  - **B (подтверждено, `app/web/routes.py` admin_list_events):** `channel_ids = {e.channel_id for e in events}` включал None для owner-событий → `get_by_id(None)` делал NULL-pk запрос (SQLAlchemy предупреждение, в будущем ошибка).
- **Исправление (применено, `app/web/routes.py`):**
  - **C:** в `admin_checkin_ticket` после нахождения билета загружается его событие (`EventService.get_by_id`) и проверяется `_can_manage_event` → 403 если нет доступа (rollback).
  - **B:** `channel_ids` фильтрует `e.channel_id is not None`.
  - Тесты: `TestCheckinAccess` (owner может / чужой 403), обновлён `test_admin_checkin_ok`.
- **Связанные ошибки:** #050

## 054 — Нативные confirm()/prompt() не работают в Telegram Mini App (кнопка «Отменить подписку» молчит)

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено (confirm); prompt — требует доработки
- **Описание:** При нажатии «Отменить подписку» не происходит видимых действий. Причина — `confirm("Отключить подписку?")` не работает в Telegram WebView (диалог не показывается, функция молча возвращает false). Проблема затрагивает все кнопки с `confirm()`/`prompt()`.
- **Анализ:**
  - **Подтверждено (`app/web/static/app.js`):** `adminUnsubscribe` (было) начинался с `confirm()`. Всего 5 мест `confirm()` и 11 мест `prompt()`.
  - **Подтверждено (исследование):** Telegram Mini App не поддерживает нативные браузерные `confirm`/`prompt` в WebView. Официальное решение — `window.Telegram.WebApp.showPopup` / `showAlert`.
  - **Подтверждено (dev-копия):** эндпоинт `/unsubscribe` работает (200 при супер-админе), значит баг именно в UI-диалоге, не в API.
- **Исправление (применено, `app/web/static/app.js`):**
  - Добавлены обёртки `tgConfirm`, `tgPrompt`, `tgAlert`, `tgShowPopup` — используют Telegram PopUp API с fallback на нативный `confirm`/`prompt` вне Telegram.
  - Все 5 `confirm()` → `await tgConfirm()`.
  - **Ограничение:** Telegram PopUp не поддерживает текстовый ввод — `tgPrompt` в Telegram показывает подсказку и возвращает null. Формы ввода (prompt для кодов/имени/тарифа) требуют замены на отдельные страницы-формы (TODO).
- **Связанные ошибки:** нет

## 055 — Двойной ввод тарифа: кнопка «Только сменить тариф» дублировала dropdown через prompt

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** Кнопка «🔄 Только сменить тариф» открывала попап-окно «Новый тариф (basic/pro)» (через `prompt`), хотя на той же странице уже есть выпадающее меню `#sub_tier` (Basic/Pro). Пользователь получал двойной выбор тарифа. Дополнительно `prompt` в Telegram не работает (#054).
- **Анализ:**
  - **Подтверждено (`app/web/static/app.js`):** `changeTierPrompt` использовал `prompt("Новый тариф (basic/pro):", currentTier)` — дублировал `#sub_tier` на странице формы подписки.
- **Исправление (применено, `app/web/static/app.js`):**
  - `changeTierPrompt` → `changeTier`: читает уже выбранный тариф из `#sub_tier` (без prompt). Кнопка обновлена.
- **Связанные ошибки:** #054

## 056 — QR-коды не гейтились по подписке + UI не скрывал pro-функции для basic

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** Сопоставление UI с подписками выявило 2 расхождения: (1) QR-коды определены в матрице как фича pro, но бэкенд не проверял `require_feature("qr_codes")` — basic получал QR; (2) UI показывал pro-функции (платные, пригласительные, QR) всем организаторам, включая basic, который получал ошибку при нажатии.
- **Анализ:**
  - **Подтверждено (app/web/routes.py):** эндпоинт `admin_ticket_qr` проверял только `_can_manage_event`, без QR-гейта.
  - **Подтверждено (app/web/static/app.js):** поля «Цена», «Пригласительных», кнопка «Выдать пригласительное», QR — не скрывались для basic.
- **Исправление (применено):**
  - **Бэкенд (`routes.py`):** в `admin_ticket_qr` добавлен QR-гейт: `require_feature("qr_codes")` (канал ИЛИ пользователь) → 403 для basic.
  - **Бэкенд (`routes.py get_me`):** `/api/me` теперь возвращает `subscription_tier`/`is_subscription_active`/`subscription_until` пользователя.
  - **Фронтенд (`app.js`):** хелпер `isPro()` (пользователь pro ИЛИ pro-канал); скрыты/заблокированы: поле «Пригласительных» (только pro), кнопка «Выдать пригласительное» (только pro), поле «Цена» (basic → disabled).
- **Связанные ошибки:** нет

## 057 — Режим «только web»: убраны все команды бота кроме входа

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** Решение перейти на «только web»: бот служит только входом в веб-кабинет. Все пользовательские и админ-команды бота (покупка, билеты, /check, FSM-создание, супер-админ команды, channel_post) убраны из регистрации.
- **Анализ:**
  - **Подтверждено (`app/platforms/telegram/bot.py` _register_handlers):** регистрировались /events, /event, /buy, /my_tickets, /cancel, /check, /stats_all, /list_channels, /channel_info, /user_info, /admin_cancel, /broadcast, /health, /check_expired, /change_admin, FSM, channel_post.
- **Исправление (применено, `app/platforms/telegram/bot.py`):**
  Оставлены: /start, /menu, /admin (→ WebApp-кнопка), my_chat_member (синхронизация админов канала), deep-links buy_/invite_ в /start. Всё остальное убрано — функции работают через Mini App.
  Тесты: удалены 8 имитационных тестов убранных функций (TestBotFlows) — они переехали в web (покрыты web-тестами).
- **Связанные ошибки:** нет

## 058 — Открытое создание мероприятий + эндпоинт покупки подписки пользователя

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** По матрице фич создание мероприятий должно быть открыто для любого пользователя (бесплатное/черновик), а pro-функции активируются фактом покупки подписки. Но создание гейтилось `require_admin` → `is_organizer` (требует активную подписку), а подписку нельзя было активировать через приложение.
- **Анализ:**
  - **Подтверждено (app/web/routes.py):** `admin_create_event` зависел от `require_admin` (только организатор). Нет эндпоинта активации подписки пользователя.
- **Исправление (применено):**
  - `admin_create_event` → `get_current_user` (создание открыто любому аутентифицированному).
  - Новый эндпоинт `POST /api/me/subscription` (`SubscribeMeIn`): покупка/активация подписки пользователя (tier basic/pro).
  - Платное по-прежнему требует pro (`require_feature("paid_events")` в EventService.create).
  - Тесты: `TestOpenCreateAndSubscribe`, `TestRealUserPath` (E2E), `TestRoleContract`.
- **Связанные ошибки:** нет

---

## 059 — Расхождение frontend/backend: обычный пользователь не может создать мероприятие через UI

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** Пользователь сообщил, что не может создать мероприятие. Бэкенд `POST /admin/events` был открыт (использовал `get_current_user`, не `require_admin`) — ошибка 058 частично открыла создание. Но фронтенд `app.js` скрывал вкладку «Панель» для `role === "user"`, а 6 event-management эндпоинтов (`GET /:id`, `PATCH`, `toggle`, `delete`, `publish`, `GET /admin/events`) использовали `require_admin` — пользователь не мог управлять даже своими созданными мероприятиями.
- **Анализ:**
  - **Подтверждено (`app/web/static/app.js:223`):** вкладка «Панель» добавлялась только при `state.role !== "user"`. Обычный пользователь → `role === "user"` → вкладка скрыта.
  - **Подтверждено (`app/web/routes.py:319,421,461,493,517,541`):** 6 эндпоинтов использовали `require_admin`, блокируя управление собственными мероприятиями.
  - **Подтверждено (`app/web/dependencies.py:181-182`):** `is_admin = is_super_admin or is_organizer or bool(managed_channel_ids)` → для обычного пользователя всегда `False`.
  - **Подтверждено (QA-агент, эксперимент):** временный возврат `require_admin` на `GET /admin/events` → контрактный тест `test_contract_user_role_must_have_create_access` падает с диагностическим сообщением.
- **Исправление (применено, 5 файлов, +243/−35):**
  - `app/web/routes.py`: 6 эндпоинтов переведены с `require_admin` на `get_current_user`. Доступ к мероприятию — через `_can_manage_event` (проверка владения). `GET /admin/events` возвращает owner-мероприятия для обычных пользователей.
  - `app/web/static/app.js`: вкладка «Панель» видна всем ролям. Обычные пользователи видят «Мероприятия», организаторы — ещё «Проверку билета», super-admin — всё.
  - `tests/test_role_contract.py`: +7 контрактных тестов, включая ключевой `test_contract_user_role_must_have_create_access` — проверяет что role='user' → доступ к созданию и списку мероприятий. Ловит регресс: если бэкенд закроет эндпоинт, но роль останется 'user', тест падает с инструкцией что чинить.
  - `tests/test_organizer_e2e.py`: обновлён `test_buyer_cannot_admin` под новое поведение.
  - `tests/test_web_api.py`: `test_admin_events_forbidden_for_user` → `test_admin_events_open_for_regular_user`.
- **Коммит:** `fc3a115`
- **Тесты:** 277 (было 274, +3)
- **Связанные ошибки:** 058 (открытое создание мероприятий — неполное: создание открыто, но управление и UI остались закрыты)

---

## 060 — Гейт `is_admin` в POST /api/me/channels блокировал обычных пользователей

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** Пользователь с подпиской не мог добавить свой канал в кабинет — `POST /api/me/channels` возвращал 403 «Нет доступа к управлению каналами». Причина: эндпоинт требовал `current.is_admin` (подписка или канал в управлении), но добавление **своего** канала не требует роли организатора — это регистрация канала для последующей публикации.
- **Анализ:**
  - **Подтверждено (`app/web/routes.py:361-365`):** гейт `if not current.is_admin` → 403. Обычный пользователь (`role='user'`) не проходил проверку.
  - **Избыточность гейта:** защита уже есть — уникальность `telegram_channel_id` (UNIQUE), анти-захват (409 если канал принадлежит другому), DM-fallback при публикации (бот без прав админа в канале отправляет анонс в личку).
- **Исправление (применено):**
  - `app/web/routes.py`: удалён гейт `is_admin` (5 строк).
  - `tests/test_web_api.py`: `test_post_me_channels_requires_organizer` → `test_post_me_channels_allows_regular_user` (201 вместо 403).
  - `tests/test_organizer_e2e.py`: +2 теста — обычный пользователь добавляет канал (201), защита от захвата чужого канала (409).
- **Коммит:** `da58353`
- **Тесты:** 285 (было 283, +2)
- **Связанные ошибки:** 059 (расхождение frontend/backend ролей — та же категория: гейт роли избыточен)

---

## 061 — `_can_manage_event` не проверял владельца при наличии channel_id

- **Дата:** 2026-08-07
- **Статус:** ✅ Исправлено
- **Описание:** Пользователь получал 403 на publish своих мероприятий. Деплой-скрипт назначает все мероприятия на legacy-канал (`UPDATE events SET channel_id = ... WHERE channel_id IS NULL`). `_can_manage_event` проверял `can_manage(channel_id)` раньше `owner_user_id` → владелец не проходил проверку канала → 403.
- **Анализ:**
  - **Подтверждено (логи VDS):** `POST /api/admin/events/.../publish → 403 Forbidden`, пользователь `6934798265`.
  - **Подтверждено (routes.py `_can_manage_event`):** порядок проверок: channel → owner. При наличии channel_id владелец не проверялся.
  - **Подтверждено (deploy.yml):** SQL назначает всем мероприятиям legacy-канал.
- **Исправление:** `_can_manage_event`: сначала `owner_user_id == current.user_id`, потом `can_manage(channel_id)`.
- **Коммит:** `b0ba541`
- **Тесты:** 285
- **Связанные ошибки:** 050, 053

---

## 062 — Пользователь без подписки не видел кнопку «Создать мероприятие»

- **Дата:** 2026-08-08
- **Статус:** ✅ Исправлено
- **Описание:** Пользователь без подписки (`role='user'`) на вкладке «Мероприятия» попадал в публичную ленту (`showEvents` → `renderEvents`), где не было кнопки создания мероприятия. Кнопка была только в админской панели (`renderAdminEvents`), доступной через карточку дашборда или для организаторов.
- **Анализ:**
  - **Подтверждено (`app.js:495-507`):** `renderEvents` — публичная лента. Нет кнопки создания ни в пустом состоянии, ни над списком.
  - **Подтверждено (`app.js:874-888`):** `renderAdminEvents` — кнопка есть и в пустом, и в заполненном состоянии.
- **Исправление:** Добавлена кнопка «+ Создать мероприятие» в `renderEvents`:
  - Пустой список: `<button ...>+ Создать мероприятие</button>` вместо параграфа «Следите за анонсами»
  - Список с мероприятиями: кнопка над `<div class="events-list">`
- **Коммит:** `1a3e14a`
- **Тесты:** 290
- **Связанные ошибки:** 059 (расхождение frontend/backend — та же категория)

---

## 063 — VK Mini App показывает «Откройте кабинет в Telegram» на /vk-app

- **Дата:** 2026-08-10
- **Статус:** ✅ Исправлено
- **Описание:** VK Mini App (https://pochtibot.online/vk-app) открывается, но вместо кабинета показывает заглушку «Откройте кабинет в Telegram» — как будто initData не сформирован.
- **Анализ:**
  - **Подтверждено (vk-bridge 3.0.2, unpkg `@vkontakte/vk-bridge/dist/browser.min.js` + `dist/types/data.d.ts`):** `bridge.send("VKWebAppGetLaunchParams")` возвращает объект НАПРЯМУЮ — `{ vk_app_id, vk_user_id, vk_ts, sign, ... }` (тип `GetLaunchParamsResponse`, `sign` на верхнем уровне). Обёртки `launch_params` нет.
  - **Подтверждено (`app.js:114`):** `const lp = (res && res.launch_params) || {};` — `res.launch_params` всегда `undefined` → `lp.sign` не проверяется → `state.initData` остаётся пустым → `showNoInitData()` (`app.js:152`) рисует заглушку.
  - **Подтверждено (VDS):** на `/vk-app` отдаётся правильный `vk-app.html` (vk-bridge подключён), deployed `app.js` идентичен локальному (md5 совпадает).
- **Исправление:** `app/web/static/app.js` — `initVKAuth()` теперь нормализует launch params через `normalizeVKLaunchParams()`:
  - объект vk-bridge 3.x напрямую (sign на верхнем уровне) — главный фикс;
  - legacy-обёртка `{ launch_params: "vk_user_id=...&sign=..." }`;
  - fallback на `window.location.search` (`/vk-app?vk_user_id=...&vk_ts=...&sign=...`).
- **Коммит:** `8fdac65` (PR #2)
- **Тесты:** 7 (test_frontend.py) + полный прогон (362 passed)

---

## 064 — web-контейнер без VK_APP_ID/VK_SECRET_KEY — VK Mini App отвечал 500

- **Дата:** 2026-08-11
- **Статус:** ✅ Исправлено
- **Описание:** После фикса #063 фронт стал собирать `initData`, но API возвращал `500 "VK App ID / secret key not configured"` при любом входе в VK Mini App.
- **Анализ:**
  - **Подтверждено (VDS):** `docker exec ticketbot-web python3 -c "from app.config import settings; ..."` → `vk_app_id = None`, `vk_secret_key = None`.
  - **Подтверждено (`docker-compose.yml:86`):** сервис `web` читает `env_file: .env.telegram`, где VK-переменных не было.
  - **Подтверждено (`deploy.yml`):** при деплое `.env.telegram` пересоздаётся из секретов без `VK_APP_ID`/`VK_SECRET_KEY`.
  - **Подтверждено (`vk_auth.py:84-90`):** без `settings.vk_app_id`/`vk_secret_key` → `HTTPException 500`.
- **Исправление:** `deploy.yml` — писать `VK_APP_ID`/`VK_SECRET_KEY` из GitHub Secrets в `.env.telegram`; шаблон `.env.telegram` дополнен (в т.ч. `WEBAPP_URL`).
- **Коммит:** `d859fe9` (PR #3)
- **Тесты:** 362 passed (логика не менялась)
- **Связанные ошибки:** 063 (продолжение цепочки VK Mini App)

---

## 065 — X-Skip-Auth обходил аутентификацию на проде (бэкдор)

- **Дата:** 2026-08-11
- **Статус:** ✅ Исправлено
- **Описание:** Заголовок `X-Skip-Auth: 1` на прод-сервере возвращал 200 и аккаунт `Dev` (id `12345`), полностью обходя аутентификацию. Найден в ходе аудита безопасности VK Mini App.
- **Анализ:**
  - **Подтверждено (прод, curl):** `curl -s -H 'X-Skip-Auth: 1' https://pochtibot.online/api/me` → `HTTP 200` + `{"id":"...","telegram_user_id":"12345","name":"Dev",...}` (без заголовка — 401).
  - **Подтверждено (`dependencies.py:93`):** `if x_skip_auth: return dev-user` — без проверки окружения. Заголовок клиент шлёт сам → любой может действовать от имени id `12345`.
  - **Подтверждено (`app.js:463`):** фронт шлёт `X-Skip-Auth` только на `localhost`, но это не защита — запрос можно отправить напрямую.
- **Исправление:** добавлен флаг `settings.allow_skip_auth` (по умолчанию `False`):
  - `config.py` — `allow_skip_auth: bool = False` (на проде не задан → бэкдор закрыт);
  - `dependencies.py` — `if x_skip_auth and settings.allow_skip_auth:`;
  - `tests/conftest.py` — `ALLOW_SKIP_AUTH=true` для тестовой среды (существующие тесты не сломаны).
- **Коммит:** — (см. PR)
- **Тесты:** 3 skip-auth теста + полный прогон
- **Связанные ошибки:** — (обнаружена при аудите VK; TG-ветка тоже закрыта, т.к. гейт общий)

---

## 066 — Черновик мероприятия можно было купить по ID (is_published не проверялся)

- **Дата:** 2026-08-11
- **Статус:** ✅ Исправлено
- **Описание:** `buy_ticket` и `buy_ticket_webapp` не проверяли `is_published` → черновик (is_published=False) можно было купить напрямую по ID через `/api/events/{id}/buy`. Публичная лента `list_upcoming` фильтрует is_published, но прямая покупка — нет.
- **Анализ:**
  - **Подтверждено (`services.py buy_ticket`/`buy_ticket_webapp`):** проверки только is_active / date / available_tickets; is_published не проверяется.
  - **Подтверждено (`models.py:259`):** `is_published` default=False (создание = черновик); publish ставит True.
  - **Переоценка `paid_events`:** гейт есть в `event.create` (подписка организатору), при покупке не нужен (покупатель не обязан быть Pro) — и бот-версия `buy_ticket` так работает. Это НЕ баг.
- **Исправление:** в `buy_ticket` и `buy_ticket_webapp` добавлена проверка `if not event.is_published: raise ValueError("Мероприятие не опубликовано")`. Обновлены тесты: публикация события перед покупкой (e2e, sold_out и др.).
- **Коммит:** — (см. PR)
- **Тесты:** +2 TDD (draft_rejected), полный прогон

---

## 067 — VK-покупатель привязывался к telegram-identity (хардкод платформы)

- **Дата:** 2026-08-11
- **Статус:** ✅ Исправлено
- **Описание:** Покупка, список билетов и отмена в web хардкодили `PlatformType.telegram` → VK-пользователь, купивший в Mini App, привязывался к telegram-identity (потенциальные пересечения ID, билет «не виден» в VK-кабинете, DM-уведомление уходит в Telegram).
- **Анализ:**
  - **Подтверждено (`routes.py:160/200/226`):** `get_or_create(platform=PlatformType.telegram, ...)` в `buy_ticket`, `list_tickets`, `cancel_ticket`.
  - **Подтверждено (`vk_auth.py`):** VK-аутентификация возвращает `platform='vk'` в auth_data — но route её игнорировал.
  - **Подтверждено (`_send_ticket_dm`):** уведомление шлётся только в Telegram (`bot.send_message`); для VK-покупателя — пусто (функциональный gap, не безопасность).
- **Исправление:** `buy_ticket`, `list_tickets`, `cancel_ticket` — `platform=PlatformType(auth_data.get("platform", "telegram"))`. VK-покупатель теперь работает со своей платформой.
- **Коммит:** — (см. PR)
- **Тесты:** +3 TDD (vk_platform), полный прогон
- **Связанные ошибки:** 066 (обе — аудит VK-ветки web)

---

## 068 — Реальные env-файлы (.env.telegram/.env.vk/.env.max) трекались в git

- **Дата:** 2026-08-11
- **Статус:** ✅ Исправлено
- **Описание:** Файлы с секретами платформ были в git (`.env.telegram`, `.env.vk`, `.env.max`). Сейчас в них плейсхолдеры, но риск — случайно закоммитить реальные значения.
- **Анализ:**
  - **Подтверждено (git ls-files):** `.env.telegram`, `.env.vk`, `.env.max` отслеживались; `.gitignore` игнорировал только `.env`.
  - **Подтверждено (git log по всем коммитам):** реальных токенов в истории нет — ротация не нужна, но структура рискованная.
- **Исправление:** `git rm --cached` трёх файлов; шаблоны `.env.telegram.example`/`.env.vk.example`/`.env.max.example`; `.gitignore`: `.env.*` + `!.env.*.example`. Документация (CLAUDE.md) обновлена на шаблоны.
- **Коммит:** — (см. PR)
- **Тесты:** — (инфраструктура, тестов нет)

---

## 069 — Нет rate limiting (brute-force кода привязки / скрейпинг)

- **Дата:** 2026-08-11
- **Статус:** ✅ Исправлено
- **Описание:** Ни на один эндпоинт не было ограничения частоты запросов. Особенно опасно для `POST /me/link-code`/`/me/link` (код 8 hex-символов, TTL 10 мин).
- **Анализ:**
  - **Подтверждено (grep app/):** `ratelimit|slowapi|throttl` — пусто.
- **Исправление:** лёгкий per-IP middleware `app/web/rate_limit.py` (без новых зависимостей, один uvicorn-воркер): скользящее окно 60с, лимит `settings.rate_limit_per_minute` (по умолчанию 120/мин), whitelist `/health` `/metrics` `/static`. При превышении → 429.
- **Коммит:** — (см. PR)
- **Тесты:** +1 TDD (test_rate_limit_returns_429)

---

## 070 — Community token VK-группы регистрировался без проверки владения

- **Дата:** 2026-08-11
- **Статус:** ✅ Исправлено
- **Описание:** `POST /me/vk-groups` принимал `community_token` как есть — организатор мог привязать токен чужой группы и постить на её стену.
- **Анализ:**
  - **Подтверждено (`routes.py register_my_vk_group`):** токен принимался без верификации.
  - **Подтверждено (план vk-mini-app-plan.md, вопрос №8):** `VKWebAppGetCommunityToken` во фронтенде — TODO.
- **Исправление:** `app/web/vk_api.py` — `verify_group_token()` (VK API `groups.getById` + сверка id); в роуте при токене → 400 «Не удалось подтвердить токен для этой группы». Регистрация без токена разрешена (placeholder).
- **Коммит:** — (см. PR)
- **Тесты:** +2 TDD (rejected/accepted), обновлены существующие (мок verify)

---

## 071 — Устаревшая VK API версия 5.131 (2022)

- **Дата:** 2026-08-11
- **Статус:** ✅ Исправлено
- **Описание:** `_API_VERSION = "5.131"` — версия от 2022 года.
- **Исправление:** `app/web/vk_api.py` — `5.131` → `5.199` (современная известная-good версия).
- **Коммит:** — (см. PR)
- **Тесты:** — (константа; поведение не меняется)

---

## 072 — Покупатель не получал билет: UUID вместо кода, QR только админу

- **Дата:** 2026-08-12
- **Статус:** ✅ Исправлено
- **Описание:** После покупки в VK/TG web покупатель видел UUID билета, а не код для входа; QR был доступен только организатору (pro). На входе принимается `validation_code` (XXXX-XXXX), а покупатель его не видел — билет фактически нельзя было предъявить.
- **Анализ:**
  - **Подтверждено (`get_user_tickets`):** API отдаёт `validation_code`, но фронт показывал `t.id` (UUID).
  - **Подтверждено (`routes.py admin_ticket_qr`):** QR — только `/admin/...` с pro-гейтом.
- **Исправление:**
  - `routes.py` — новый `GET /tickets/{id}/qr` (владелец билета, без pro, чужой → 403).
  - `app.js` — «Мои билеты» показывают код для входа + кнопку «📱 Показать QR» (заголовки VK/TG через `authHeaders`).
- **Коммит:** — (см. PR)
- **Тесты:** +2 (buyer_ticket_qr, foreign_403), +1 frontend smoke

---

## 073 — VK-покупатель не получал билет в ЛС VK (только в Telegram)

- **Дата:** 2026-08-12
- **Статус:** ✅ Исправлено
- **Описание:** `_send_ticket_dm` слал билет только в Telegram; для VK-покупателя (купил в Mini App) билет в ЛС VK не уходил.
- **Анализ:**
  - **Подтверждено (`routes.py _send_ticket_dm`):** `bot.send_message` — только Telegram.
  - **Подтверждено (VK API):** `messages.send` от имени группы требует community token + разрешение пользователя (`VKWebAppAllowMessagesFromGroup`).
- **Исправление:**
  - `vk_api.py` — `send_vk_ticket_dm()` (messages.send от группы, best-effort).
  - `routes.py` — `buy_ticket` возвращает `vk_group_id`; новый `POST /tickets/{id}/send-vk` (владелец → группа → messages.send).
  - `app.js` — после покупки мягкий запрос «получить в ЛС?» → `VKWebAppAllowMessagesFromGroup` → `POST send-vk`. Отказ — билет остаётся в кабинете.
- **Коммит:** — (см. PR)
- **Тесты:** +3 (send_vk_ticket_dm unit: success/no-token/api-error), +2 web (send-vk, foreign_403), +1 frontend

---

## 074 — validate_ticket не находил пригласительные (INNER JOIN на User)

- **Дата:** 2026-08-12
- **Статус:** ✅ Исправлено
- **Описание:** Организатор не мог проверить пригласительное по коду на входе: `validate_ticket` использовал `INNER JOIN` на `users`, а пригласительное имеет `user_id=None` → строка не находилась, возвращалось `found=False`.
- **Анализ:**
  - **Подтверждено (`services.py validate_ticket`):** `select(Ticket, User.name, Event.title).join(User, Ticket.user_id == User.id)` — INNER JOIN отбрасывает пригласительные (user_id=None).
  - **Подтверждено (модель `Ticket`):** `user_id nullable`, пригласительные (`is_invite=True`) не привязаны к пользователю.
- **Исправление:** `validate_ticket` — `join` → `outerjoin` (LEFT JOIN) по User; пригласительные теперь находятся по коду, `user_name` = «—».
- **Коммит:** — (см. PR)
- **Тесты:** +1 e2e (`test_invite_claimed_by_guest_via_link` шаг 4: validate пригласительного → found=True)

---

## 075 — EventService.update не проверял paid_events (обход через PATCH price)

- **Дата:** 2026-08-12
- **Статус:** ✅ Исправлено
- **Описание:** Гейт платных мероприятий есть только в `EventService.create` (price>0 требует pro). `EventService.update` (services.py:1204) НЕ проверял `paid_events` при смене `price` — basic-юзер может создать `price=0`, затем `PATCH price=500` и обойти гейт.
- **Анализ:**
  - **Подтверждено (`services.py:1204-1244`):** `update` применяет setattr для всех полей, включая `price`, без проверки `require_feature("paid_events")`.
  - **Подтверждено (маршрут `PATCH /admin/events/{id}`):** routes.py admin_update_event вызывает `update` без гейта цены.
- **Исправление:** в `EventService.update` добавлен гейт: при `price>0` проверяется `has_event_pro_feature(event_id, "paid_events")` (подписка ИЛИ per-event премиум); при переносе даты обновляется `expires_at` премиума события.
- **Коммит:** — (см. PR)
- **Тесты:** +1 сервис (`test_update_price_gate_fixed`), +1 e2e (`test_event_premium_unlocks_paid_features`)

## 076 — GET /admin/tickets/validate — проверка доступа мёртвая (информационный слив)

- **Дата:** 2026-08-13
- **Статус:** ✅ Исправлено
- **Описание:** `GET /api/admin/tickets/validate?code=` должен был отдавать 403 проверяющему, который не управляет мероприятием билета. На деле проверка `_can_manage_event` никогда не срабатывала — любой авторизованный пользователь мог провалидировать билет любого мероприятия (раскрытие имени покупателя, названия и статуса события по коду).
- **Анализ:**
  - **Подтверждено (`services.py:2156-2163`):** `validate_ticket` не возвращал `event_id` в ответе.
  - **Подтверждено (маршрут `routes.py`):** `if result.get("found") and result.get("event_id")` — `event_id` всегда `None`, ветка 403 недостижима (мёртвый код). Нашлось при разработке QR-сканера: авто-проверка камерой делает валидацию без ручного ввода, дыра стала эксплуатируемой.
- **Исправление:**
  - `validate_ticket` теперь возвращает `event_id` и `ticket_id` в found-ветке (`services.py`).
  - Проверка `_can_manage_event` в GET /validate ожила: чужое событие → 403; несуществующий код → 200 `found:false` (без 403 — не светим существование события).
  - Нормализация кода вынесена в `_normalize_ticket_code` (`routes.py`), используется в validate и checkin (убрано дублирование).
- **Коммит:** — (см. PR #15, ветка `feature/qr-scanner`)
- **Тесты:** +1 сервис (`test_validate_ticket_found_exposes_event_and_ticket_id`), +5 API (`TestValidateAccess`: owner/channel-admin allowed, чужой орг 403, супер-админ, not-found без 403), +1 e2e-шаг (`test_full_cabinet_flow` 3c: validate → event_id), +5 структурных smoke `test_frontend.py`.

### Известное ограничение (не чинили): POST /checkin для чужого использованного билета

В `POST /admin/tickets/checkin` доступ проверяется ПОСЛЕ `check_in_by_code` (с `rollback`): для активного чужого билета → 403, но для уже использованного чужого → 409 («Билет уже использован») — проверка доступа не дошла, т.к. ошибка статуса приходит раньше. Раскрытие факта существования/статуса по 8-символьному hex-коду (неугадываемое пространство) — приоритет низкий. Опциональный фоллоу-ап: перед checkin вызывать read-only `validate_ticket` (теперь отдаёт event_id) для проверки доступа до мутации.

## 077 — Деплой промокодов: payments без новых колонок (миграция заштампована, но не применена)

- **Дата:** 2026-08-18
- **Статус:** ✅ Исправлено
- **Описание:** После деплоя фичи промокодов таблица `promo_codes` существует, но `payments` НЕ получила колонки `base_amount`/`discount_amount`/`promo_code`. При покупке билета сервис создаёт `Payment` с этими полями → INSERT упадёт (неизвестная колонка) → промокоды на проде не работали.
- **Анализ:**
  - **Подтверждено (`deploy.yml:187-200`, шаг «Накатить миграции»):** используется `command.stamp(cfg, 'head')` — Alembic **только ставит версию** в `alembic_version`, но НЕ выполняет `upgrade()` миграций.
  - **Подтверждено (`deploy.yml:202-261`, шаг «Синхронизировать схему БД»):** идемпотентный SQL знает про колонки событий/тикетов, но НЕ про новые колонки payments — они не добавлены.
  - **Подтверждено (прод, 2026-08-18):** `alembic_version=0012` (заштампована), таблица `promo_codes` есть (создана `create_all` при старте приложения), `information_schema.columns` для payments — только id/ticket_id/amount/status/created_at.
  - **Корень:** `create_all` создаёт новые таблицы, но не добавляет колонки в существующие; stamp не выполняет миграции.
- **Исправление:**
  - Ручной `ALTER TABLE payments ADD COLUMN IF NOT EXISTS base_amount/discount_amount/promo_code` + backfill `base_amount=amount, discount_amount=0` на проде.
  - Обновлён шаг «Синхронизировать схему БД» в `deploy.yml`: добавлены 3 колонки payments (идемпотентно) — чтобы будущие деплои чинили схему.
- **Коммит:** — (см. PR, ветка feature/promo-codes + фикс deploy)
- **Тесты:** e2e `test_promo_e2e.py` не ловит (в тестах БД создаётся `create_all` → колонки есть; проблема только на проде с реальной миграцией). Пройден ручной e2e на проде после фикса.

---

## 078 — Публичная утечка черновиков + отсутствие валидации age_restriction (найдено e2e на проде)

- **Дата:** 2026-08-24
- **Статус:** ✅ Исправлено
- **Описание:** E2E-тест на реальном проде (`https://pochtibot.online`) выявил два дефекта:
  - **Д1.** `GET /api/events/{id}` (публичный) отдавал 200 с полным телом для **черновика** (`is_published=False`). Любой, кто знает UUID, видел неопубликованное мероприятие.
  - **Д2.** `age_restriction` принимал произвольные значения (например `"21+"` → 200), хотя по ФЗ-436 допустимы только `0+/6+/12+/16+/18+`.
- **Анализ:**
  - **Д1 (подтверждено `routes.py` get_event):** публичный эндпоинт не проверял `is_published`/`is_active`/`deleted_at` — только `event is None`.
  - **Д1 (подтверждено `services.py` list_upcoming):** не фильтровал `deleted_at` — удалённое могло появиться в списке.
  - **Д2 (подтверждено `schemas.py`):** `age_restriction` имел только `max_length=4`, но не набор `AGE_RESTRICTIONS` (константа была объявлена, но не применена).
- **Исправление:**
  - `routes.py` get_event: добавлен гейт `if not event.is_published or not event.is_active or event.deleted_at is not None → 404`.
  - `services.py` list_upcoming: добавлен `Event.deleted_at.is_(None)` в WHERE.
  - `schemas.py`: `field_validator` в `EventCreate`/`EventUpdateIn` — значение должно быть в `AGE_RESTRICTIONS`.
- **Коммит:** `0c66b32` (ветка `bugfix/event-public-gate-age-validation`)
- **Тесты:** +6 web (draft/inactive/deleted → 404; `"21+"`/`""` → 422 на create и update). Полный прогон 522 passed.

---

## 079 — DELETE /api/me (удаление аккаунта, #213) отсутствовал на проде

- **Дата:** 2026-08-24
- **Статус:** ✅ Исправлено
- **Описание:** При e2e на проде `DELETE /api/me` вернул 405. Удаление аккаунта (п.1.1.10 Правил VK) не работало.
- **Анализ:**
  - **Подтверждено (`git merge-base --is-ancestor a3bb516 dev` → false):** ветка `feature/vk-account-delete` (коммит `a3bb516`) **не была смержена в dev** — фича существовала только в отдельной ветке, деплой на прод шёл без неё.
- **Исправление:**
  - Смержен `feature/vk-account-delete` в `dev` (мерж-коммит `41b7ae3`): `UserService.delete_account`, `DELETE /api/me`, кнопка «Удалить аккаунт» в UI.
- **Коммит:** `41b7ae3` (Merge feature/vk-account-delete)
- **Тесты:** 7 сервисных + 1 web из ветки; полный прогон после мержа 529 passed.

---

## 080 — Белый экран VK Mini App (инлайн display:none на онбординге)

- **Дата:** 2026-08-24
- **Статус:** ✅ Исправлено
- **Описание:** При открытии VK Mini App (`/vk-app`) — белый экран, не отрисовываются иконки. Прод-логи (nginx): браузер загрузил `vk-app.html` → `styles.css` → `vk-bridge.min.js` → `app.js` (все 200), но **ни одного `/api/*` запроса** — JS не выполнился.
- **Анализ (пошагово, доказано кодом + Playwright на проде):**
  1. **Подтверждено (`vk-app.html:25`, `index.html:25`):** `<div class="onboarding-overlay" id="onboardingOverlay" style="display:none">` — **инлайн `style="display:none"`** на онбординге. Инлайн-стиль имеет приоритет над CSS-правилом `.onboarding-overlay.active { display:flex }`.
  2. **Подтверждено (`app.js` showOnboarding):** при первом запуске `hasAcceptedTerms()` false → `showOnboarding()` добавляет класс `active`, но инлайн `display:none` держит оверлей скрытым. Кнопка «Принять условия» существует, но **не видна** (`btn.getBoundingClientRect().height === 0`).
  3. **Подтверждено (`app.js` DOMContentLoaded handler, строки 202-207):** онбординг «показан» (класс active), handler делает `return` и **ждёт клик по невидимой кнопке** → `runAppStart()` НЕ вызывается → нет `/api/me`, нет рендера → белый экран.
  4. **Доказано на проде (Playwright):** после убирания инлайн-стиля — `display:flex`, кнопка видна, клик → идут `/api/me`/`/api/events`/`/api/tickets`, кабинет отрисовывается.
  - Второй слой (тоже исправлен): внешний CDN `cdn.jsdelivr.net/jsqr` блокировал `app.js` в iframe VK — jsQR.js скачан локально.
  - Третий слой (попутно, не корень): `initVKAuth` мог зависнуть на bridge — приоритет launch params из URL, таймаут bridge, локальный vk-bridge.
  - **Четвёртый слой (заглушка VK «Проблема с инициализацией приложения»):** VK требует вызов `bridge.send("VKWebAppInit")` как сигнал инициализации приложения. При URL-приоритете (VK передаёт launch params в URL) код шёл в `if (!res && bridge)` — `res` уже был из URL, ветка пропускалась → `VKWebAppInit` НЕ вызывался → VK показывал заглушку ДО загрузки iframe.
- **Исправление:**
  - **Главное:** убран инлайн `style="display:none"` из `div#onboardingOverlay` в обоих entry. Скрытие по CSS `.onboarding-overlay`, показ — классом `.active`.
  - **VKWebAppInit** теперь вызывается ВСЕГДА в начале `initVKAuth` (если bridge есть), до URL-приоритета — иначе VK не инициализирует приложение.
  - Дополнительно: `jsQR.js` локально вместо CDN; vk-bridge локально; `isVK` по pathname; приоритет URL-params.
- **Коммит:** `caf8d41` (инлайн display) + `66695a5` (VKWebAppInit) + `ab375e7`/`408698f` (jsQR) + `95f1d32` (bridge)
- **Тесты:** frontend-тесты 28 passed; полный прогон 530 passed. Проверено на проде (VK): приложение открывается, показывается онбординг с принятием условий.

---

## 082 — VK Mini App использует браузерные диалоги вместо элементов интерфейса

- **Дата:** 2026-09-12
- **Статус:** ✅ Исправлено
- **Описание:** При просмотре пользовательского соглашения или политики на первом входе в VK Mini App открывается браузерный `alert`. Аналогично подтверждения и ввод данных могут использовать браузерные `confirm`/`prompt`, что не соответствует требованиям модерации VK об использовании элементов интерфейса самого сервиса.
- **Анализ (причина подтверждена чтением кода):**
  - `app/web/static/app.js:94-103` функция `tgAlert()` вызывает `window.alert()` при отсутствии Telegram WebApp SDK; в VK shell SDK намеренно не подключается.
  - `app/web/static/app.js:57-73` функция `tgConfirm()` в том же VK fallback вызывает `window.confirm()`.
  - `app/web/static/app.js:76-91` функция `tgPrompt()` и прикладные обработчики используют `window.prompt()`; в частности, `renderTerms()` на `app.js:447-461` вызывает `tgAlert()` при клике по документам onboarding.
  - В результате VK-контур показывает системные браузерные диалоги вместо DOM-элементов приложения.
- **Исправление (применено):**
  - `app/web/static/index.html` и `app/web/static/vk-app.html`: добавлен общий app-owned DOM-диалог для alert/confirm/prompt.
  - `app/web/static/styles.css`: добавлены themed overlay/card/input/actions с `.active`-паттерном.
  - `app/web/static/app.js`: добавлен singleton `openAppDialog()`/`closeAppDialog()`; `tgAlert()`/`tgConfirm()`/`tgPrompt()` используют его без браузерных dialog API; обработчики условий, имени, приглашений, подписок, линковки и каналов переведены на async helper.
  - `tests/test_frontend.py`: добавлены регрессионные проверки DOM-хоста, отсутствия native dialog API и стилей.
- **Проверка:** `pytest -q tests/test_frontend.py` — 40 passed; `node --check app/web/static/app.js` — успешно; поиск native calls нашёл только комментарий.
- **Связанные задачи:** #222.
- **Коммит:** — (изменения ожидают ревью)

---

## 081 — VK Mini App отклонён модерацией: ссылки и сведения о других площадках

- **Дата:** 2026-09-01
- **Статус:** ✅ Исправлено
- **Описание:** Модерация VK отклонила приложение по п. 4.1.8 Правил VK Mini Apps: «В сервисе присутствуют ссылки или информация о других площадках размещения сервиса».
- **Анализ (причина подтверждена чтением кода):**
  - `app/web/static/app.js:664` безусловно выводит в профиле `Telegram ID`, в том числе в `/vk-app`.
  - `app/web/static/app.js:676-677` безусловно выводит пользователю ссылки `https://t.me/aerovir` и `mailto:aerovir@mail.ru`, в том числе в VK.
  - `app/web/static/app.js:392-395` при отсутствии VK launch params показывает инструкцию «Откройте кабинет в Telegram».
  - `app/web/static/app.js:651-653` в VK-профиле предлагает «Привязать Telegram».
  - `app/web/static/app.js:452` в тексте политики для VK упоминает `VK/Telegram`.
  - `app/web/static/app.js:1851`, `1872`, `1948` используют `X-Init-Data` напрямую, поэтому VK admin QR/CSV не используют свой заголовок `X-VK-Init-Data`.
  - Telegram SDK подключён отдельно в `app/web/static/index.html:8`; `app/web/static/vk-app.html:7-8` использует локальные VK-ресурсы, поэтому Telegram SDK из VK shell не является причиной.
- **Исправление (применено):**
  - `app/web/static/app.js`: VK режим определяется до проверки авторизации; экран ошибки запуска не содержит Telegram-инструкций; политика и профиль используют platform-safe copy; в VK скрыты Telegram ID, Telegram/email support, Telegram-каналы и linking; super-admin dashboard закрыт для VK; admin QR/CSV используют `authHeaders()`.
  - `app/web/dependencies.py`: super-admin определяется только для `PlatformType.telegram`.
  - `app/web/routes.py`: `/api/me` использует authenticated platform, возвращает `platform`/`platform_user_id`, не отдаёт Telegram-labelled identity/channels для VK.
  - `tests/test_frontend.py`, `tests/test_web_api.py`: добавлены регрессионные проверки shell, VK branches, QR/CSV headers и API profile contract.
- **Проверка:** `tests/test_frontend.py` + `tests/test_web_api.py` — 188 passed; полный набор `pytest` — 540 passed, 3 warnings. `node --check app/web/static/app.js` и `py_compile` — успешно.
- **Связанные ошибки:** #063, #064, #080.
- **Коммит:** — (ветка `feature/vk-moderation-isolation`, ожидает ревью)
