# Telegram

Адаптер бота использует aiogram 3 (`app/platforms/telegram/bot.py`). Отдельная точка запуска — `python -m app.bot.telegram`; бот работает с общими моделями и сервисами из `app/core/`.

## Интерфейсы

В проекте есть Telegram-бот и Telegram Mini App. Mini App обслуживается FastAPI (`python -m app.bot.web`), открывается с корневого URL и обращается к `/api`. Сейчас зарегистрированные команды бота — `/start`, `/menu` и `/admin` (псевдоним `/menu`); они ведут в кабинет. Deep links `/start buy_<event_id>` и `/start invite_<code>` обрабатываются отдельно. Обновления состава канала синхронизируют канал и администраторов. Публикации мероприятий могут содержать кнопки покупки/просмотра.

Старые обработчики пользовательских и админ-команд остаются в исходном файле, но в `_register_handlers()` сейчас не зарегистрированы; считать их доступными командами нельзя. Актуальные сценарии кабинета описаны в [`docs/user-manual.md`](../../../docs/user-manual.md). Сверяйте активные маршруты с `_register_handlers()` в `bot.py`.

## Конфигурация

```ini
DATABASE_URL=postgresql+asyncpg://tg_user:password@localhost:5432/ticketbot
TELEGRAM_TOKEN=...
ADMIN_TELEGRAM_IDS=123456,789012
WEBAPP_URL=https://example.org
```

В шаблоне `.env.telegram.example` также перечислены параметры VK Mini App и VK Pay, которые использует общий web-сервис.

## Запуск

```bash
python -m app.bot.telegram
docker compose up -d telegram
```

Для Telegram Mini App запустите также `web`. Каналы и доступы организатора хранятся в БД; детали публикаций и ролей — в [`docs/how-it-works.md`](../../../docs/how-it-works.md) и [`docs/roles-matrix.md`](../../../docs/roles-matrix.md).
