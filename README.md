# TicketBot

TicketBot — система для организации мероприятий и продажи билетов. В проекте есть Telegram- и VK-боты, бот MAX и веб-кабинет Mini App. Общая бизнес-логика и данные находятся в PostgreSQL.

## Возможности

- Публичная афиша, покупка и возврат билетов, список билетов с кодом/QR.
- Создание и публикация мероприятий, статистика продаж, управление входом и check-in.
- Организаторские подписки, приглашения, промокоды и динамические цены.
- Публикация анонсов в Telegram-каналы и VK-группы.
- Привязка идентичностей Telegram и VK для работы с единым аккаунтом организатора.
- Веб-кабинет для Telegram Mini App и VK Mini App; поддержка VK Pay на уровне web API при включённой конфигурации.
- Ролевой доступ: пользователь, организатор и супер-администратор.

Актуальные пользовательские сценарии описаны в [docs/user-flows.md](docs/user-flows.md), права — в [docs/roles-matrix.md](docs/roles-matrix.md), ручной интерфейс — в [docs/user-manual.md](docs/user-manual.md).

## Архитектура

```text
app/core/        SQLAlchemy-модели, БД, сервисы, схемы и общая бизнес-логика
app/web/         FastAPI REST API, авторизация Mini App, статические web-интерфейсы
app/platforms/   Telegram (aiogram), VK (vkbottle), MAX (max-bot-api-client-py)
app/bot/         Точки запуска платформ, Web API и seed-данных
migrations/      Alembic-миграции PostgreSQL
tests/           Unit, API и сценарные тесты
deploy/          Docker-конфигурации и инструменты развёртывания
```

Web API и платформенные боты — отдельные процессы. Они используют общую схему БД и общие сервисы, но интерфейсы и доступные функции различаются. Описание потоков данных: [docs/how-it-works.md](docs/how-it-works.md).

## Быстрый запуск через Docker Compose

Нужны Docker и Docker Compose. Скопируйте шаблоны окружения и задайте секреты:

```bash
cp .env.example .env
cp .env.telegram.example .env.telegram
cp .env.vk.example .env.vk
cp .env.max.example .env.max
```

Задайте сильный пароль PostgreSQL в корневом `.env` (`POSTGRES_PASSWORD`) и Telegram-токен в `.env.telegram`. Затем:

```bash
docker compose up -d db web telegram
```

VK и MAX запускаются отдельно и используют профиль `all`:

```bash
docker compose --profile all up -d
```

Тестовые мероприятия:

```bash
docker compose run --rm seed
```

Web-приложение доступно на `http://localhost:8080`; API имеет префикс `/api`, проверка состояния — `/health`. При первом запуске сервисы вызывают `init_db()` с `metadata.create_all`; в production изменения схемы следует накатывать Alembic-миграциями.

Остановка: `docker compose down`. Удаление volume базы вместе с данными: `docker compose down -v`.

> `docker-compose.yml` читает платформенные `.env.telegram`, `.env.vk`, `.env.max`; корневой `.env` используется для переменных самого Compose, включая `POSTGRES_PASSWORD`. Не добавляйте реальные секреты в git.

## Запуск отдельных компонентов

Python-код запускается как пакет `app`:

```bash
python -m app.bot.telegram
python -m app.bot.vk
python -m app.bot.max
python -m app.bot.web
python -m app.bot.seed
```

Зависимости приложения указаны в `pyproject.toml`; SDK MAX импортируется адаптером отдельно и должен быть доступен в окружении при запуске MAX. Для локального запуска задайте нужные переменные окружения (см. `.env.*.example`).

## Конфигурация

Основные переменные: `DATABASE_URL`, `TELEGRAM_TOKEN`, `VK_TOKEN`, `VK_GROUP_ID`, `MAX_TOKEN`, `ADMIN_TELEGRAM_IDS`, `WEBAPP_URL`, `VK_APP_ID`, `VK_SECRET_KEY`, `VK_TOKEN_ENCRYPTION_KEY` и параметры `VK_PAY_*`. Полный набор и примеры находятся в `.env.*.example`, настройки читаются через [app/config.py](app/config.py).

Для локальной разработки `X-Skip-Auth` разрешён только при `ALLOW_SKIP_AUTH=true`; по умолчанию отключён. В production Mini App аутентифицируется подписанными данными Telegram или VK.

## Разработка

- Python `>=3.12`.
- Зависимости: `pip install -e '.[dev]'`.
- Тесты требуют PostgreSQL и запускаются командой `pytest` в настроенном окружении.
- Код и сценарные тесты находятся в `app/` и `tests/`; схема развивается через `migrations/`.
- Проектные инструкции и ограничения разработки см. в [CLAUDE.md](CLAUDE.md).

## Развёртывание и поддержка

Docker-файлы и скрипты находятся в `deploy/`, диагностические скрипты — в `scripts/`. Основные документы: [деплой](docs/hosting-comparison.md), [VK Pay](docs/vk-pay-setup.md), [логирование](docs/logging.md), [ошибки](docs/errors.md) и [задачи](docs/tasks.md).
