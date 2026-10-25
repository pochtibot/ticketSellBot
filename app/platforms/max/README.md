# MAX

В `app/platforms/max/bot.py` реализован адаптер MAX с пользовательскими командами `/start`, `/events`, `/event`, `/buy`, `/my_tickets`, `/cancel`. Он использует общие сервисы и данные TicketBot. Отдельной MAX Mini App и административных команд в адаптере нет.

Адаптер импортирует SDK `max-bot-api-client-py` как необязательную зависимость. Если SDK не установлен или токен отсутствует, запуск MAX завершается сообщением об ошибке.

## Настройка и запуск

В `.env.max` задайте `MAX_TOKEN` и `DATABASE_URL`, затем установите SDK в окружение и запустите:

```bash
pip install max-bot-api-client-py
python -m app.bot.max
```

В Docker Compose сервис входит в профиль `all`:

```bash
docker compose --profile all up -d max
```

См. также [`docs/how-it-works.md`](../../../docs/how-it-works.md) и `.env.max.example`.
