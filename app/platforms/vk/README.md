# ВКонтакте

В проекте есть два VK-интерфейса:

- **VK-бот** на vkbottle (`app/platforms/vk/bot.py`) с командами `/start`, `/events`, `/event`, `/buy`, `/my_tickets`, `/cancel`.
- **VK Mini App** — web-интерфейс, обслуживаемый FastAPI (`/vk-app`), с авторизацией launch params и REST API.

VK-бот предоставляет базовые пользовательские сценарии. Управление мероприятиями, площадками и расширенными настройками реализовано в общем web API/Mini App; отдельного набора админ-команд VK-бота в `bot.py` нет. Покупка из этого бота вызывает общий `TicketService`; VK Pay доступен через web-поток при настроенных credentials.

## Настройка

```ini
DATABASE_URL=postgresql+asyncpg://vk_user:password@localhost:5432/ticketbot
VK_TOKEN=...
VK_GROUP_ID=...
VK_APP_ID=...
VK_SECRET_KEY=...
```

Для подключения VK-групп организатором предусмотрено отдельное хранение зашифрованного токена группы; задайте `VK_TOKEN_ENCRYPTION_KEY` согласно `.env.telegram.example`.

## Запуск

```bash
python -m app.bot.vk
docker compose --profile all up -d vk
```

VK Mini App использует web-сервис и его конфигурацию, поэтому для него также запустите `web`. Общая документация: [`docs/how-it-works.md`](../../../docs/how-it-works.md), [`docs/roles-matrix.md`](../../../docs/roles-matrix.md).
