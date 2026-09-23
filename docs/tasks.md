# Учёт задач TicketBot

Формат записи:
- **Статус:** pending / in progress / done / cancelled
- **Описание:** что нужно сделать
- **Дата:** когда добавлена
- **Коммит / PR:** связанные изменения

---

## ✅ Выполненные

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 1 | Создать архитектуру проекта (core, platforms, config) | ✅ done | 2026-07-05 | — |
| 2 | Реализовать Telegram бота (aiogram, все команды) | ✅ done | 2026-07-05 | — |
| 3 | Реализовать VK бота (vkbottle, все команды) | ✅ done | 2026-07-05 | — |
| 4 | Реализовать MAX бота (заглушка) | ✅ done | 2026-07-05 | — |
| 5 | Настроить PostgreSQL (SQLAlchemy async + asyncpg) | ✅ done | 2026-07-05 | — |
| 6 | Создать seed-скрипт с тестовыми мероприятиями | ✅ done | 2026-07-05 | — |
| 7 | Обернуть проект в Docker (Dockerfile + docker-compose) | ✅ done | 2026-07-05 | — |
| 8 | Настроить GitHub Actions self-hosted runner | ✅ done | 2026-07-05 | `6d26730` |
| 9 | Настроить деплой на VPS (Beget/LightNode) | ✅ done | 2026-07-05 | — |
| 10 | Создать документацию проекта (CLAUDE.md, how-it-works.md) | ✅ done | 2026-07-05 | `e17488f` |
| 11 | Разделить entry point для всех платформ (run_*.py) | ✅ done | 2026-07-07 | `e17488f` |
| 12 | Починить init_db (таблицы не создавались при деплое) | ✅ done | 2026-07-07 | `29746ef` |
| 13 | Добавить retry при ошибках Telegram API | ✅ done | 2026-07-07 | `62752d3` |
| 14 | Создать реестр ошибок (docs/errors.md) | ✅ done | 2026-07-07 | — |
| 15 | Создать учёт задач (docs/tasks.md) | ✅ done | 2026-07-07 | — |

## 🔄 В работе

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 16 | Реальная оплата (YooKassa / ссылка на оплату) | 📌 pending | 2026-07-07 | — |
| 17 | VK группа — настройка и подключение | 📌 pending | 2026-07-07 | — |
| 18 | MAX — подключение после выхода SDK | 📌 pending | 2026-07-07 | — |
| 20 | Telegram канал: анонсы + channel_post хендлеры | ✅ done | 2026-07-07 | `99e177d` |
| 21 | Telegram канал: проверка /events и /event в работе | ✅ verified | 2026-07-07 | — |
| 22 | Добавить TELEGRAM_CHANNEL_ID в GitHub Secrets | ✅ done | 2026-07-07 | — |
| 23 | Админ-панель: управление мероприятиями через бота | ✅ done | 2026-07-07 | — |

## 📌 Запланировано

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 19 | Добавить уведомления о скором мероприятии | 📌 pending | 2026-07-07 | — |
| 20 | Написать тесты (pytest для core/services.py) | ✅ done | 2026-07-09 | — |
| 24 | Разделить процессы платформ (убрать launcher.py) | ✅ done | 2026-07-09 | — |
| 25 | Раздельные PostgreSQL-роли для каждой платформы | ✅ done | 2026-07-09 | — |
| 26 | Раздельные .env файлы (.env.telegram, .env.vk, .env.max) | ✅ done | 2026-07-09 | — |
| 27 | Docker Compose: Telegram как дефолт, VK/MAX в profile: all | ✅ done | 2026-07-09 | — |
| 28 | Тесты: database, services, TG/VK/MAX bot handlers | ✅ done | 2026-07-09 | — |
| 29 | Документация: README для каждой платформы | ✅ done | 2026-07-09 | — |
| 30 | Multi-tenant: модель Channel, изоляция мероприятий по каналам | ✅ done | 2026-07-11 | `acc7953` |
| 31 | Multi-tenant: миграция 0002 (channels + channel_id в events) | ✅ done | 2026-07-11 | `acc7953` |
| 32 | Multi-tenant: ChannelService (подписки, каналы админа) | ✅ done | 2026-07-11 | `acc7953` |
| 33 | Подписка: /subscribe <channel_id> <days> для super-admin | ✅ done | 2026-07-11 | `913b104` |
| 34 | Подписка: /unsubscribe <channel_id> для super-admin | ✅ done | 2026-07-11 | `e937599` |
| 35 | Подписка: проверка `is_subscription_valid()` при каждом действии | ✅ done | 2026-07-11 | `acc7953` |
| 36 | Админ-меню: inline-кнопки вместо текстовых команд | ✅ done | 2026-07-11 | `3f6e66f` |
| 37 | Админ-меню: ролевое разделение (super-admin vs channel admin) | ✅ done | 2026-07-11 | `3f6e66f` |
| 38 | Super-admin команды: /stats_all, /list_channels, /channel_info, /user_info, /admin_cancel, /broadcast, /health | ✅ done | 2026-07-11 | `3f6e66f` |
| 39 | Super-admin: /check_expired, /change_admin | ✅ done | 2026-07-11 | `3f6e66f` |
| 40 | FSM: исправлен порядок callback.answer() в create_event (кнопка подтвердить) | ✅ done | 2026-07-11 | `a7f15ff` |
| 41 | Deploy: миграция через alembic stamp + ALTER TABLE ADD COLUMN IF NOT EXISTS | ✅ done | 2026-07-11 | `bed6e60`, `b5eb13a`, `1449908`, `a7d6e6a` |
| 42 | Deploy: очистка тестовых seed-данных при деплое | ✅ done | 2026-07-11 | `913b104` |
| 43 | Legacy канал: автопривязка к первому админу (_get_admin_channel fallback) | ✅ done | 2026-07-11 | `913b104` |
| 44 | Тесты: обновлены для multi-tenant (fixture sample_channel, channel_id) | ✅ done | 2026-07-11 | `f886a16`, `f8846be`, `18e56ed` |
| 45 | Удалён TELEGRAM_CHANNEL_ID из конфига и .env | ✅ done | 2026-07-11 | `acc7953` |
| 46 | Канал: my_chat_member хендлер (обнаружение добавления бота) | ✅ done | 2026-07-11 | `acc7953` |
| 47 | Fix: GRANT USAGE → GRANT USAGE, CREATE ON SCHEMA public для платформенных ролей БД | ✅ done | 2026-07-12 | `2a8c29b` |
| 48 | Fix: admin_menu кнопки выполняют действие вместо показа команды | ✅ done | 2026-07-12 | `e3b7167` |
| 49 | Fix: UnboundLocalError — select импортирован внутри блока, недоступен в других блоках | ✅ done | 2026-07-12 | `305691e` |
| 50 | Feat: FSM-ввод параметров для кнопок админ-меню группы Б (channel_info, subscribe, и др.) | ✅ done | 2026-07-12 | `e2fa6a8` |
| 51 | Fix: Missing imports — TicketStatus, PaymentStatus, User, Event, Ticket, Payment | ✅ done | 2026-07-12 | `e2fa6a8` |
| 52 | Feat: Кнопка «🎛 Управление» на анонсах в канале — гибридная админ-панель через ЛС | ✅ done | 2026-07-12 | `2560555` |
| 53 | Fix: Каналу при подписке назначается суперадмин вместо реального админа | ✅ done | 2026-07-13 | — |
| 54 | Fix: нейминг ролевой модели (_is_admin → _is_super_admin, _is_channel_admin → _has_admin_access) | ✅ done | 2026-07-16 | — |
| 55 | Fix: /my_channels проверял super-admin вместо channel admin (баг) | ✅ done | 2026-07-16 | — |
| 56 | Feat: верификация админа канала через Telegram API (get_chat_member) при каждом действии | ✅ done | 2026-07-16 | — |
| 57 | Fix: on_chat_member_update не обрабатывал статус "administrator" (каналы) | ✅ done | 2026-07-16 | — |
| 58 | Refactor: устранены двойные вызовы _get_admin_channel в хендлерах | ✅ done | 2026-07-16 | — |
| 59 | Fix: _verify_channel_admin теперь возвращает True/False/None (не деактивирует подписку при ошибке API) | ✅ done | 2026-07-16 | — |
| 60 | Fix: on_chat_member_update обновляет telegram_channel_id для голых чисел (без -100) | ✅ done | 2026-07-16 | — |
| 61 | Fix: _verify_channel_admin возвращает None → доверяем БД и возвращаем канал (вместо skip) | ✅ done | 2026-07-18 | — |
| 62 | Feat: multi-admin каналов — новая модель ChannelAdmin, getChatAdministrators, синхронизация всех админов | ✅ done | 2026-07-18 | — |
| 63 | Fix: /subscribe существующего канала не синхронизирует админов (прочерк в списке каналов) | ✅ done | 2026-07-18 | — |
| 64 | Feat: улучшение диалога создания мероприятия — _fsm_header, inline-кнопка Пропустить, новые формулировки | ✅ done | 2026-07-18 | — |
| 65 | Feat: _format_event_text — унифицированное форматирование мероприятий (Phase 2) | ✅ done | 2026-07-23 | `a0835d0` |
| 66 | Feat: структурированное JSON-логирование поведения пользователя (app/core/logging_config.py) | ✅ done | 2026-07-24 | — |
| 67 | Feat: логирование сервисного слоя — EventService, TicketService, ChannelService, UserService | ✅ done | 2026-07-24 | — |
| 68 | Feat: логирование ключевых хендлеров Telegram-бота (start, buy, callback, chat_member) | ✅ done | 2026-07-24 | — |
| 69 | Feat: управление публикацией мероприятия — поле is_published, черновики, кнопка Опубликовать | ✅ done | 2026-07-24 | — |
| 70 | Feat: афиша мероприятия (медиа-файлы через Telegram File Server) | ✅ done | 2026-07-24 | — |
| 71 | Feat: duration_ms на все read-методы сервисного слоя (14 методов) | ✅ done | 2026-07-26 | — |
| 72 | Feat: фоновый сбор метрик системы (app/core/system_metrics.py) | ✅ done | 2026-07-26 | — |
| 73 | Feat: скрипт самодиагностики с порогами + Telegram-алерт (scripts/self-diagnose.py) | ✅ done | 2026-07-26 | — |
| 74 | Feat: добавлены Makefile-цели diagnose, diagnose-alert, diagnose-verbose | ✅ done | 2026-07-26 | — |
| 75 | Feat: psutil как основная зависимость (перенесён из [monitoring] в core) | ✅ done | 2026-07-26 | — |
| 76 | Feat: автоматический алерт при превышении порогов (в metrics_loop бота) | ✅ done | 2026-07-26 | — |
| 77 | Feat: cron-диагностика раз в 5 мин (scripts/cron-diagnose.py) | ✅ done | 2026-07-26 | — |
| 78 | Feat: cron-задание устанавливается GitHub Actions при деплое (deploy.yml) | ✅ done | 2026-07-26 | — |
| 79 | Chore: удалён setup_cron_diagnose из setup-dev-env.sh (теперь в CI/CD) | ✅ done | 2026-07-26 | — |
| 80 | Chore: удалены install/remove-cron-diagnose из Makefile (теперь в CI/CD) | ✅ done | 2026-07-26 | — |
| 81 | Fix: self-diagnose.py — флаг --logs-from-stdin для пайпа логов в контейнер | ✅ done | 2026-07-26 | — |
| 82 | Fix: cron-diagnose.py — сбор логов на хосте, запуск diagnose внутри контейнера | ✅ done | 2026-07-26 | — |
| 83 | Fix: cron-diagnose.py — datetime.utcnow() → datetime.now(timezone.utc) | ✅ done | 2026-07-26 | — |
| 84 | Feat: SubscriptionTier (basic/pro) + Channel.subscription_tier | ✅ done | 2026-07-26 | — |
| 85 | Feat: TicketStatus.checked_in + validation_code, checked_in_at/by, is_free | ✅ done | 2026-07-26 | — |
| 86 | Feat: ChannelService.activate_subscription с tier, get_subscription_tier, require_feature | ✅ done | 2026-07-26 | — |
| 87 | Feat: EventService проверяет tier при создании (basic не может price>0) | ✅ done | 2026-07-26 | — |
| 88 | Feat: TicketService.generate_validation_code, validate_ticket, check_in, check_in_by_code | ✅ done | 2026-07-26 | — |
| 89 | Feat: /check <code> команда для админа на входе | ✅ done | 2026-07-26 | — |
| 90 | Feat: разные кнопки для бесплатных/платных мероприятий | ✅ done | 2026-07-26 | — |
| 91 | Feat: /subscribe с аргументом tier (basic/pro) | ✅ done | 2026-07-26 | — |
| 92 | Tests: 62 тестов сервисного слоя (все проходят) | ✅ done | 2026-07-26 | — |

## 📌 Запланировано

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 93 | QR-генерация для Pro-мероприятий | 📌 pending | 2026-07-26 | — |
| 94 | Telegram Mini App сканер QR для админа | ✅ done (код) | 2026-07-26 | 2026-08-13 (feature/qr-scanner, PR #15) |
| 95 | Экспорт списка участников (CSV) | 📌 pending | 2026-07-26 | — |
| 96 | Fix: синхронизация админов при активации подписки на новый канал | ✅ done | 2026-07-26 | — |
| 97 | Docs: описание бага #040 в errors.md + тесты | ✅ done | 2026-07-26 | — |
| 98 | Feat: кнопка «🔍 Проверить билет» в админ-меню (для всех админов) | ✅ done | 2026-07-26 | — |
| 99 | Feat: /check в Menu Button бота | ✅ done | 2026-07-26 | — |
| 100 | Feat: отправка кода билета в ЛС после покупки | ✅ done | 2026-07-26 | — |
| 101 | Fix: try/except в cmd_check и FSM check_ticket (обработка ошибок) | ✅ done | 2026-07-26 | — |
| 102 | Chore: убран /check из Menu Button (оставлен в админ-меню) | ✅ done | 2026-07-26 | — |
| 103 | Feat: личный кабинет + админка в Telegram Mini App (роли user/channel-admin/super-admin) | ✅ done (код) | 2026-08-05 | — |
| 104 | Feat: веб-API админки — мероприятия CRUD, publish/repost/toggle/delete, статистика, билеты+check-in, CSV, каналы+подписки, общая статистика | ✅ done (код) | 2026-08-05 | — |
| 105 | Feat: перенос покупок/админки из inline-кнопок в web (MenuButtonWebApp, WebApp-кнопка в анонсах, /start и /menu → кабинет) | ✅ done (код) | 2026-08-05 | — |
| 106 | Feat: WEBAPP_URL в CI (.env.telegram) для работы WebApp-кнопок на проде | ✅ done | 2026-08-05 | — |
| 107 | Тесты: 28 новых (admin API 16, сервисы 9, бот 1) — всего 168 | ✅ done | 2026-08-05 | — |
| 108 | Деплой веб-кабинета на прод (pochtibot.online) и живой тест | 📌 pending | 2026-08-05 | — |
| 109 | Feat: закрытие пробелов супер-админа в web — создать канал + подписка (POST /api/admin/channels), инфо о пользователе, рассылка, здоровье | ✅ done (код) | 2026-08-05 | — |
| 110 | Тесты: +13 (админ-эндпоинты 9, сервисы 2, бот 2) — всего 181 | ✅ done (код) | 2026-08-05 | — |
| 111 | Feat: система пригласительных для pro (invite tickets) — квота на мероприятии, выдача 1/2/3 чел., отмена, статистика (выдано/использовано/свободно) | ✅ done (код) | 2026-08-05 | — |
| 112 | Feat: QR-коды для всех билетов и пригласительных (фича qr_codes: pro), показ/скачивание в кабинете, deep-link invite_<code> в боте | ✅ done (код) | 2026-08-05 | — |
| 113 | TDD: сначала тесты, потом код (правило в CLAUDE.md + memory) | ✅ done | 2026-08-05 | — |
| 114 | Тесты: +23 (пригласительные 15, web-api 8) — всего 204 | ✅ done (код) | 2026-08-05 | — |
| 115 | Деплой пригласительных + QR на прод и живой тест | 📌 pending | 2026-08-05 | — |
| 116 | Feat: полный контур тестирования — харнесс имитации пользователя (Подход A) покрывает весь user-flow | ✅ done (код) | 2026-08-06 | — |
| 117 | Харнесс: Update-билдеры (callback/channel_post/my_chat_member), FakeSession GetChatAdministrators/DeleteMessage | ✅ done | 2026-08-06 | — |
| 118 | Бот-сценарии: inline buy legacy, channel_buy redirect, FSM create, publish, /check, invite deep-link, my_tickets+cancel, анонс с WebApp | ✅ done | 2026-08-06 | — |
| 119 | Web-сквозной flow на реальной БД (db_client + httpx ASGITransport): browse→buy→tickets→admin→invite→checkin→stats | ✅ done | 2026-08-06 | — |
| 120 | Покрытие непокрытых эндпоинтов (toggle/delete/repost/update/… +14 тестов) | ✅ done | 2026-08-06 | — |
| 121 | Тесты: всего 230 (206 → +24) | ✅ done | 2026-08-06 | — |
| 122 | Deploy: полный контур тестирования на CI | 📌 pending | 2026-08-06 | — |
| 123 | Feat: управление подпиской канала для super-admin — смена типа + срока (дни/месяцы/годы), POST /admin/channels/{id}/subscription и /tier | ✅ done | 2026-08-06 | — |
| 124 | Тесты: +9 (сервисы 5, web-api 4) — всего 239 | ✅ done | 2026-08-06 | — |
| 125 | QA-верификация фичи через агента (8 сквозных сценариев, 403/404/400, срок от now) | ✅ done | 2026-08-06 | — |
| 126 | Feat: роль «Организатор» + подписка на пользователя (без канала, Mini App) — User.subscription_*, Event.owner_user_id, role organizer | ✅ done (код) | 2026-08-07 | — |
| 127 | Fix: #050 owner-мероприятия недоступны организатору на админ-действиях (403) — хелпер _can_manage_event | ✅ done | 2026-08-07 | — |
| 128 | Тесты: +6 (organizer e2e) +4 (owner access) — всего 258 | ✅ done | 2026-08-07 | — |
| 129 | Deploy: роль организатора на прод + миграция (users.subscription_*, events.owner_user_id) | 📌 pending | 2026-08-07 | — |
| 130 | QA: полное сквозное тестирование организатора (11 функций, реальная БД) — 266 тестов | ✅ done | 2026-08-07 | — |
| 131 | Fix: #052 организатор без канала выдаёт пригласительные (про-гейт по подписке пользователя) | ✅ done | 2026-08-07 | — |
| 132 | Fix: #053 checkin проверяет доступ организатора (403 для чужого) + SAWarning на owner-событиях | ✅ done | 2026-08-07 | — |
| 133 | Dev-копия на VDS для «живого» теста — изолированный compose-проект (db/web/telegram), БД ticketbot_dev, порт 8081 | ✅ done | 2026-08-07 | — |
| 134 | Живой тест организатора против dev-копии — 12 функций PASS | ✅ done | 2026-08-07 | — |
| 135 | QA-агент дополнен разделом «Живой тест против dev-копии» (чек-лист организатора) | ✅ done | 2026-08-07 | — |
| 136 | UI: добавлено редактирование имени (PATCH /api/me) в профиле + инфо о канале (GET /api/admin/channels/{id}) | ✅ done | 2026-08-07 | — |
| 137 | Feat: сопоставление UI с подписками — QR-гейт (pro) + UI-адаптация (isPro, скрытие pro-функций для basic) | ✅ done | 2026-08-07 | — |
| 138 | Feat: режим «только web» — убраны все команды бота кроме входа (start/menu/admin), сохранены my_chat_member + deep-links | ✅ done | 2026-08-07 | — |
| 139 | Feat: открытое создание мероприятий (любой пользователь) + эндпоинт покупки подписки POST /api/me/subscription | ✅ done | 2026-08-07 | — |
| 140 | Feat: тестирование от лица пользователя — E2E реального пути, контрактные тесты ролей, QA-агент, smoke-скрипт | ✅ done | 2026-08-07 | — |
| 141 | Fix: расхождение frontend/backend — открыто создание мероприятий для обычных пользователей (UI + API), +7 контрактных тестов, QA-верификация | ✅ done | 2026-08-07 | `fc3a115` |
| 142 | Feat: самообслуживание каналов — POST/GET /api/me/channels, UI «Мои каналы» (список + форма добавления), DM-fallback при публикации (бот не в канале → анонс в личку), форма мероприятия — все каналы без фильтра подписки | ✅ done | 2026-08-07 | `1523514` |
| 143 | Fix: убрать гейт is_admin из POST /api/me/channels — любой пользователь может добавить СВОЙ канал. Защита: уникальность telegram_channel_id + анти-захват (409) + DM-fallback | ✅ done | 2026-08-07 | `da58353` |
| 144 | Refactor: убрать статусы подписок из /api/me/channels — только id, telegram_channel_id, title. Подписка только у пользователя | ✅ done | 2026-08-07 | `30a2275` |
| 145 | Feat: публикация с выбором канала на странице мероприятия. POST /admin/events/{id}/publish принимает channel_id. Публикация многократная в разные каналы | ✅ done | 2026-08-07 | `fc5f43b` |
| 146 | Fix: `_can_manage_event` сначала owner_user_id, потом channel_id — владелец не теряет доступ при наличии канала | ✅ done | 2026-08-07 | `b0ba541` |
| 147 | Perf: pool конфигурация — pool_recycle=1800, pool_pre_ping=True, pool_size=5, max_overflow=5. Предотвращает мёртвые соединения после OOM-kill | ✅ done | 2026-08-08 | `48e18ef` |
| 148 | Arch: get_current_user → Depends(get_session) + test patch. Pool config: pool_recycle=1800 + pool_pre_ping=True (48e18ef). Routes пока на async with (конвертация 40 эндпоинтов — отдельная задача) | ✅ done (частично) | 2026-08-08 | `3c85935` |
| 150 | Arch: конвертация routes.py на Depends(get_session) | ❌ cancelled | 2026-08-08 | — |
| 151 | Docs: инструкция пользователя (user-manual.md) — для человека без IT-навыков | ✅ done | 2026-08-08 | `3987279` |
| 152 | Feat: DM-доставка билета при покупке через Mini App — билет + код в личку Telegram | ✅ done | 2026-08-08 | `82538e9` |
| 153 | Feat: soft-delete пользователей — User.deleted_at, DELETE /admin/users/{id}, GET /admin/users | ✅ done | 2026-08-08 | `8bc88e5` |
| 149 | Feat: Prometheus + postgres_exporter + FastAPI instrumentator — /metrics, метрики БД, Grafana datasource | ✅ done | 2026-08-08 | `44c68c4` |
| 154 | Fix: postgres:16-alpine → postgres:16 (Debian/glibc) — убирает зомби-бэкенды от musl bug | ✅ done | 2026-08-08 | `a825f66` |
| 155 | Feat: редизайн интерфейса — вариант Б (4 вкладки + дашборд, мобильный-first CSS) | ✅ done | 2026-08-08 | `bcfc60f` |
| 156 | Fix: no-cache headers для index.html и static (десктоп Telegram кешировал) + tab bar на всю ширину | ✅ done | 2026-08-08 | `e9221b5` |
| 157 | Infra: swap 2 GB на VDS — предотвращает OOM-kill PostgreSQL | ✅ done | 2026-08-08 | — |
| 158 | Feat: DM-уведомление о возврате билета | ✅ done | 2026-08-08 | `9fa1d8c` |
| 159 | Fix: кнопка «Создать мероприятие» на публичной ленте — пользователь без подписки не видел кнопку | ✅ done | 2026-08-08 | `1a3e14a` |

### VK Mini App — кросс-платформенные продажи (план 2026-08-08)

Детальный план: `docs/vk-mini-app-plan.md`. Киллер-фича: одно мероприятие на всех площадках, продажи ведёт организатор. Принципы: организатор = создатель мероприятия (не владелец канала/группы), каналы/группы = цели публикации, линковка только организаторов, TG-код не меняется.

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 160 | VK: каноническая идентичность организатора — `user_identities` + линковка по одноразовому коду | ✅ done (код) | 2026-08-08 | `feature/vk-identity` |
| 161 | VK: аутентификация Mini App — launch params + `sign` (HMAC-SHA256), резолв канона | ✅ done (код) | 2026-08-08 | `feature/vk-auth` |
| 162 | VK: соработники мероприятия — `event_managers` M2M, расширение `_can_manage_event` | ✅ done (код) | 2026-08-08 | `feature/vk-managers` |
| 163 | VK: модель `vk_groups` + self-service групп + `VKWebAppGetCommunityToken` (зашифр. токен) | ✅ done (код) | 2026-08-08 | `feature/vk-groups` |
| 164 | VK: публикации `event_publications` + постинг `wall.post` / `messages.send` | ✅ done (код) | 2026-08-08 | `feature/vk-publications` |
| 165 | VK: фронтенд Mini App (переиспользовать `app.js` или отдельный entry — decision №2) | ✅ done (код) | 2026-08-08 | `feature/vk-frontend` |
| 166 | VK: TDD-контур для VK-фич (аналог harness для Telegram) | ✅ done (код) | 2026-08-08 | `feature/vk-testing` |
| 167 | Fix: VK Mini App показывал «Откройте кабинет в Telegram» — vk-bridge 3.x возвращает launch params объектом напрямую, а код читал `res.launch_params` | ✅ done | 2026-08-10 | `8fdac65` (PR #2, errors #063) |
| 168 | Fix: web-контейнер без `VK_APP_ID`/`VK_SECRET_KEY` — VK Mini App отвечал 500. Вшить VK env в `.env.telegram` через `deploy.yml` | ✅ done | 2026-08-11 | `d859fe9` (PR #3, errors #064) |
| 169 | Fix: `X-Skip-Auth` обходил аутентификацию на проде — флаг `allow_skip_auth` (False по умолчанию), гейт в `dependencies.py`, включён только в тестах | ✅ done | 2026-08-11 | — (PR #5, errors #065) |
| 170 | Fix: черновик мероприятия можно было купить по ID — проверка `is_published` в `buy_ticket`/`buy_ticket_webapp` | ✅ done | 2026-08-11 | — (PR #6, errors #066) |
| 171 | Fix: VK-покупатель привязывался к telegram-identity — платформа из `auth_data` в `buy_ticket`/`list_tickets`/`cancel_ticket` | ✅ done | 2026-08-11 | — (PR #6, errors #067) |
| 172 | Fix: env-файлы с секретами в git — `git rm --cached`, шаблоны `*.example`, `.gitignore` `.env.*` | ✅ done | 2026-08-11 | — (PR #7, errors #068) |
| 173 | Fix: нет rate limiting — per-IP middleware (120/мин), whitelist /health /metrics /static | ✅ done | 2026-08-11 | — (PR #7, errors #069) |
| 174 | Fix: community token VK-группы без проверки — `verify_group_token` (groups.getById) | ✅ done | 2026-08-11 | — (PR #7, errors #070) |
| 175 | Fix: VK API 5.131 → 5.199 | ✅ done | 2026-08-11 | — (PR #7, errors #071) |
| 176 | Feat: билет покупателю — код для входа + QR в «Моих билетах» (GET /tickets/{id}/qr, владелец) | ✅ done | 2026-08-12 | — (PR #8, errors #072) |
| 177 | Feat: VK-покупатель получает билет в ЛС VK — messages.send от группы, soft-ask + VKWebAppAllowMessagesFromGroup | ✅ done | 2026-08-12 | — (PR #8, errors #073) |
| 178 | Docs: канонические user flows для всех ролей (docs/user-flows.md) — основа для e2e и новых фич | ✅ done | 2026-08-12 | — (см. docs/user-flows.md) |

### Подготовка VK Mini App к публикации (2026-08-22)

Доведение VK Mini App (App ID `54698875`) до публичной доступности: адрес приложения в кабинете VK, community token для анонсов на стену группы, прокидывание секрета шифрования на прод. Код VK-фич (#160-166) уже в `dev`.

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 204 | Env: `VK_TOKEN_ENCRYPTION_KEY` (Fernet) — добавлен в GitHub Secrets и прокинут в `.env.telegram` через `deploy.yml` (для шифрования community token VK-групп) | ✅ done | 2026-08-22 | — (готово к коммиту) |
| 205 | Feat: `VKWebAppGetCommunityToken` во фронтенде — при добавлении VK-группы в VK-контексте запрашивается community token (scope wall,messages,manage,photos,app_widget) и передаётся в `POST /api/me/vk-groups`; обработка ошибок (нет прав админа / приложение не установлено) | ✅ done | 2026-08-22 | — (готово к коммиту) |
| 206 | Infra: CORS — добавлен `https://m.vk.com` (мобильный веб-клиент VK Mini App) | ✅ done | 2026-08-22 | — (готово к коммиту) |
| 207 | Perf: кэш-бюст `app.js?v=3 → ?v=4` в `index.html` и `vk-app.html` (после правок JS) | ✅ done | 2026-08-22 | — (готово к коммиту) |
| 208 | Ручные шаги в кабинете VK: тип приложения «Встраиваемое → VK App», адрес `https://pochtibot.online/vk-app`, установка приложения в группу, публикация/модерация | ⏳ ожидает владельца | 2026-08-22 | — |

### Соответствие Правилам размещения приложений VK Mini Apps (2026-08-23)

Аудит по редакции Правил от 17.03.2026. Критичные нарушения: нет политики конфиденциальности/соглашения (п.1.1.4), удаления аккаунта (1.1.10), возрастного гейта (2.1.5), поддержки (2.4.1), онбординга (1.1.2). Оплата — заглушка (4.1.6.2).

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 209 | Docs: политика конфиденциальности (`docs/privacy-policy.md`) — оператор ИП/самозанятый, данные (VK/ТГ ID, имя, username, билеты, шифрованный community token), цели, права пользователя; дополнена по типовой структуре VK (меры защиты, возраст, уведомления, ответственность) | ✅ done | 2026-08-23 | — |
| 210 | Docs: пользовательское соглашение (`docs/user-agreement.md`) — условия, аккаунт, билеты, организаторы, ответственность; дополнено по типовой структуре VK (обязанности/запреты, ограничения доступа, форс-мажор, споры) | ✅ done | 2026-08-23 | — |
| 211 | Модерация VK: выбран вариант «Стандартные (типовые) документы VK» в разделе «Юридические документы» — свои документы не обязательны для публикации; `docs/*.md` остаются как справка | ✅ done | 2026-08-23 | — |
| 212 | UI: показ политики/соглашения при первом запуске (онбординг, п.1.1.4) — экран онбординга при первом входе (localStorage `ticketbot_terms_accepted`), кнопка «Принять условия», ссылки «Пользовательское соглашение»/«Политика конфиденциальности» с показом краткого текста; блок до принятия; проверено в Playwright (появление, принятие, повторный вход) | ✅ done | 2026-08-23 | — |
| 213 | Feat: удаление аккаунта и данных пользователем (п.1.1.10) — `UserService.delete_account` (анонимизация name/username, деактивация подписки, билеты сохраняются, идемпотентно), `get_or_create` создаёт чистый аккаунт после удаления (identity переназначается), `DELETE /api/me`, кнопка «Удалить аккаунт» в профиле; тесты: 7 сервисных + 1 web (521 passed всего) | ✅ done | 2026-08-23 | — |
| 214 | Feat: возрастные ограничения при первом запуске (п.2.1.5) — бейдж «0+» на онбординге, строка «🔞 Возраст» на странице мероприятия; проверено в Playwright | ✅ done | 2026-08-23 | `6872e95` (feature/vk-onboarding) |
| 215 | Feat: контакты поддержки в UI и описании приложения (п.2.4.1) — раздел «Поддержка» в профиле (все роли): Telegram @aerovir (t.me/aerovir) + email aerovir@mail.ru; проверено рендером в Playwright | ✅ done | 2026-08-24 | `e817877` (feature/vk-support-contacts) |
| 221 | UI: в VK-версии кнопка поддержки ведёт в сообщество VK `https://vk.ru/club241015257`; Telegram-контакт остаётся только в Telegram-версии; добавлены frontend-контракты | ✅ done | 2026-09-12 | — |
| 222 | Fix: VK Mini App не должен использовать браузерные alert/confirm/prompt — заменить их на DOM-диалоги приложения, включая просмотр условий первого входа | ✅ done | 2026-09-12 | errors #082; `tests/test_frontend.py` — 40 passed |
| 216 | Feat: возрастное ограничение мероприятия (ФЗ-436) — `Event.age_restriction` (0+/6+/12+/16+/18+), миграция 0015, схемы, сервис, API (admin + public), select в форме организатора, знак на билете, ответственность организатора за маркировку в соглашении; тесты: 3 сервисных + 4 web API (314 passed) | ✅ done | 2026-08-23 | `5a3881a` (feature/vk-event-age-restriction) |
| 217 | Fix (errors #078): публичный гейт деталей мероприятия — черновик/неактивное/удалённое → 404 (была утечка по прямой ссылке), `list_upcoming` фильтрует `deleted_at`; валидация `age_restriction` по `AGE_RESTRICTIONS` в `EventCreate`/`EventUpdateIn` (принимал "21+"); найдено e2e на проде | ✅ done | 2026-08-24 | `0c66b32` (bugfix/event-public-gate-age-validation) |
| 218 | Fix (errors #079): мерж `feature/vk-account-delete` (#213) — `DELETE /api/me` отсутствовал на проде (405), ветка не была смержена в dev | ✅ done | 2026-08-24 | `41b7ae3` (Merge feature/vk-account-delete) |
| 219 | Fix (errors #080): VK Mini App не открывался. Причины по слоям: (1) **`VKWebAppInit` не вызывался** при URL-приоритете → заглушка VK «Проблема с инициализацией» (фикс: вызывать всегда); (2) инлайн `style="display:none"` на онбординге скрывал кнопку → runAppStart не вызывался; (3) jsQR с внешнего CDN блокировал app.js в iframe VK. Дополнительно: vk-bridge локально, isVK по pathname, приоритет URL-params. Проверено на проде: VK открывает, онбординг с принятием условий | ✅ done | 2026-08-24 | `66695a5` + `caf8d41` + `ab375e7` |
| 220 | Чек-лист модерации VK Mini Apps: (✅) тема VK dark/light через VKWebAppGetConfig — `applyVKTheme()`, класс vk-theme-dark/light, CSS-переменные (проверено Playwright); (✅) оффлайн-обработка — api() ловит сетевую ошибку, понятное сообщение; (✅) заглушка для планшетов — «Откройте на телефоне» с кнопкой продолжить (isTablet: тач+≥700px, проверено Playwright); (✅) deeplink — `?invite=` для неавторизованного (онбординг → экран приглашения → активация → «Билет получен»), `?event_id=` для авторизованного (детали события), проверено Playwright; (⏳) ручные проверки: онбординг-свайп, iOS Swipe Back, ODR, маленький экран, платформы | 🔄 in progress | 2026-08-24 | `a15271e` + `fd3b565` + deeplink-проверка |


### E2E-покрытие канонических flows (2026-08-12, `feature/e2e-coverage-flows`)

Закрывает пробелы e2e по `docs/user-flows.md`: F2 QR, F3 возврат, F5/F7 TG-сим, F13-F17 супер-админ, F1 send-vk. Все на реальной БД (`db_client`/`sf`); роль супер-админа — из конфига, БД/сервисы реальные.

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 179 | Feat: e2e супер-админ F13-F17 — `tests/test_super_admin_e2e.py` (канал+подписка, смена админа, глоб. статистика, рассылка, здоровье) | ✅ done | 2026-08-12 | — (ветка `feature/e2e-coverage-flows`) |
| 180 | Feat: e2e QR покупателя F2 — шаг в `TestCabinetFlow` (GET /tickets/{id}/qr → реальный PNG) | ✅ done | 2026-08-12 | — (там же) |
| 181 | Feat: e2e возврат билета F3 — `TestCabinetFlow.test_buyer_refund_releases_seat` (status=refunded, available+1) | ✅ done | 2026-08-12 | — (там же) |
| 182 | Feat: e2e send-vk F1 — шаг в `test_vk_e2e.test_full_vk_organizer_cycle` (билет в ЛС VK от реальной группы) | ✅ done | 2026-08-12 | — (там же) |
| 183 | Feat: TG-сим F7 check-in + F5 анонс — `tests/test_telegram_sim.py` (/check → checked_in; репост анонсов в канал) | ✅ done | 2026-08-12 | — (там же) |
| 184 | TechDeBT: `/check` и `/repost_events` не зарегистрированы в режиме «только web» — TG-sim вызывает хендлеры напрямую; при возврате команд — перевести на полный конвейер `feed_update` | ⏳ pending | 2026-08-12 | — |

### E2E синхронизация билетов между платформами (2026-08-12, `feature/e2e-crossplatform-sync`)

Дополняет киллер-фичу (одно мероприятие на всех площадках) e2e-контуром синхронизации в обе стороны. Все на реальной БД (`db_client`/`db_session`); VK-аутентификация — реальные launch params (X-VK-Init-Data), роль — реальная линковка/подписка, без моков ролей.

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 185 | Feat: e2e синхронизация VK→TG — покупка/возврат в VK (X-VK-Init-Data) отражаются в статистике TG (`sold`/`refunded`/`available`) — `tests/test_vk_e2e.py::test_vk_buy_refund_sync_to_tg` | ✅ done | 2026-08-12 | — (ветка `feature/e2e-crossplatform-sync`) |
| 186 | Feat: e2e симметрия TG→VK — билет, купленный в TG (X-Skip-Auth), проверяется VK-организатором (линкованный канон) validate/checkin → 200 — `tests/test_vk_e2e.py::test_tg_buy_vk_checkin_symmetry` | ✅ done | 2026-08-12 | — (там же) |
| 187 | Feat: e2e пригласительные как ссылки (F18, owner-путь) — не считаются в `sold`, вычитаются из `available`, отмена возвращает места — `tests/test_organizer_e2e.py::test_invites_not_counted_in_sold` | ✅ done | 2026-08-12 | — (там же) |
| 188 | Feat: пригласительные как ссылки — `claim_invite` (активация гостем по `?invite=<код>`), эндпоинт `POST /api/invites/{code}/claim`, UI выдачи (ссылка + QR), deep-link `?invite=` | ✅ done | 2026-08-12 | — (PR #11, errors #074) |
| 189 | Fix: `validate_ticket` INNER JOIN не находил пригласительные (user_id=None) → LEFT JOIN | ✅ done | 2026-08-12 | — (PR #11, errors #074) |
| 190 | Feat: предъявление билета — бесплатный → код, платный → QR (renderTickets по is_free; is_free в buy_ticket_webapp dict; код в DM только для free) | ✅ done | 2026-08-12 | — (PR #12) |
| 191 | Feat: лимит «1 опубликованное будущее» для free-организатора (EventService.count_published_future/ensure_free_slot, гейт в admin_publish_event, 409 при превышении) | ✅ done | 2026-08-12 | — (PR #13) |
| 192 | Feat: per-event премиум — `EventUpgrade` + миграция 0011, `purchase_event_premium`, `has_event_pro_feature`, `POST /api/me/events/{id}/premium`, is_premium в admin/events, гейты invite/QR, кнопка в UI | ✅ done | 2026-08-12 | — (PR #14, errors #075) |
| 193 | Fix: баг #075 — `EventService.update` не проверял paid_events (обход через PATCH price) — гейт с учётом премиума события | ✅ done | 2026-08-12 | — (PR #14, errors #075) |
| 194 | Feat: QR-сканер для админа — jsQR (CDN), живой поток камеры + фото-фоллбек (`capture="environment"` для Android TG WebView), авто-check-in через `doCheckin`, формат-гейт `^[0-9A-F]{4}-[0-9A-F]{4}$`, `stopQrScanner` (освобождение камеры) | ✅ done (код) | 2026-08-13 | — (feature/qr-scanner, PR #15) |
| 195 | Fix: `GET /admin/tickets/validate` — проверка доступа была мёртвой (validate_ticket не возвращал event_id) → теперь 403 при чужом событии; нормализация кода вынесена в `_normalize_ticket_code` (validate + checkin) | ✅ done | 2026-08-13 | — (PR #15, errors #076) |
| 196 | Feat: система промокодов (скидки на билеты, pro) — модель `PromoCode` (event_id, code, discount_type percent/fixed, discount_value, starts_at/ends_at, max_uses, used_count, is_active) + миграция 0012 + поля Payment (base_amount/discount_amount/promo_code) | ✅ done (код) | 2026-08-17 | — (feature/promo-codes) |
| 197 | Feat: промокоды — сервис (create/list/toggle/_validate/_compute), применение в buy_ticket/buy_ticket_webapp (Decimal, amount со скидкой, used_count+1), гейты promo_codes (FEATURES ×2 + has_event_pro_feature) | ✅ done (код) | 2026-08-17 | — (feature/promo-codes) |
| 198 | Feat: промокоды — эндпоинты (POST/GET /admin/events/{id}/promo-codes, POST /admin/promo-codes/{id}/toggle, body promo_code в POST /events/{id}/buy), схемы PromoCodeCreate/BuyIn | ✅ done (код) | 2026-08-17 | — (feature/promo-codes) |
| 199 | Feat: промокоды — frontend (поле «Промокод» в подтверждении, скидка/итого на странице успеха, секция «Промокоды» в админке: форма + список + вкл/выкл) | ✅ done (код) | 2026-08-17 | — (feature/promo-codes) |
| 200 | Тесты: промокоды — 22 сервисных (TestPromoCodes), 9 web API (TestPromoCodesAPI), 2 frontend smoke, 1 e2e (test_promo_e2e.py) — всего 465 | ✅ done (код) | 2026-08-17 | — (feature/promo-codes) |
| 201 | Feat: динамические цены по дате (early bird, pro) — модель EventPriceRange + миграция 0013 + published_at + PUT/GET /price-ranges + effective_price_at + статистика по факту + анонсы с актуальной ценой | ✅ done (код) | 2026-08-18 | — (feature/dynamic-pricing) |
| 202 | Feat: admin-подписка организатора без канала — POST /admin/users/{telegram_user_id}/subscription (суперадмин, по Telegram ID, дни+tier), подписка в /admin/users/{id}, UI блок «Подписать» в инфо о пользователе | ✅ done (код) | 2026-08-18 | — (feature/admin-user-subscription) |
| 203 | Feat: редизайн интерфейса — сдержанный минимализм (приглушённые бейджи, тени карточек, fade-переходы, аватар из фото TG) + постеры мероприятий (media в API, прокси /media через Telegram Bot) | ✅ done (код) | 2026-08-19 | — (feature/ui-redesign) |

## Feature Future — что не реализовано по действиям пользователей (2026-08-13)

> Справочник нереализованных фич. К этой таблице обращаемся по мере работы —
> при выборе новой фичи, планировании спринта, обсуждении приоритетов.

Сводка «что не реализовано» с точки зрения действий пользователей (с подпиской и без),
по итогам сверки `docs/user-flows.md`, `docs/tasks.md` и кода.

| # | Действие | Free (без подписки) | Pro (с подпиской) | Ссылки |
|---|----------|--------------------:|-------------------:|--------|
| 1 | Продавать платные билеты | ⚠️ доступно через per-event premium #192, оплата — заглушка | ⚠️ VK Pay path реализован, но выключен до merchant onboarding; Telegram — заглушка | `docs/vk-pay-setup.md`, миграция 0016 |
| 2 | Получать деньги / платить онлайн | ❌ нет | ⚠️ VK Pay для билетов закодирован, реальная оплата/проверка sandbox ожидают merchant setup и `VK_PAY_ENABLED=true` | подписка и event premium остаются заглушками; VK Pay не применяется к ним |
| 3 | Напомнить покупателям о мероприятии | ❌ нет | ❌ нет | задача #19 (pending), планировщика нет |
| 4 | Рассылать промо подписчикам (F6) | ❌ нет | ❌ нет | `docs/user-flows.md` F6 «📌 Фаза 2 (не реализовано)» |
| 5 | Сканировать QR на входе | ✅ есть (ручной ввод + сканер камеры/фото, авто-check-in) | ✅ есть | задача #94 (done 2026-08-13); jsQR по CDN, фото-фоллбек для Android TG WebView; фикс доступа GET /validate (errors #076). Камера — мануальный тест |
| 6 | `/check` и `/repost_events` в режиме «только web» | ⚠️ работают только через прямой вызов хендлеров в тестах | ⚠️ то же | техдолг #184 |

### Подготовка VK Mini App к повторной модерации (п. 4.1.8, 2026-09-01)

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 221 | Изолировать VK Mini App от ссылок и информации о других площадках; сохранить Telegram-режим, исправить VK auth headers QR/CSV и покрыть регрессионными тестами | ✅ done | 2026-09-01 | feature/vk-moderation-isolation (ожидает ревью) |

Ожидаемый результат: пользовательский VK-контур не показывает Telegram/TG/MAX, email или внешние ссылки; поддерживаются покупки, билеты, организаторские функции и VK-группы. См. ошибку #081 в `docs/errors.md`.

> Примечание: записи до #203 относятся к историческому трекеру; текущая задача продолжает актуальный контур модерации VK после #220.

### VK Pay — билеты (2026-09-23)

| # | Задача | Статус | Дата | Коммит |
|---|--------|--------|------|--------|
| 223 | VK Pay для VK Mini App: защищённый заказ/резервация, VKWebAppOpenPayForm, проверка merchant callback и выдача билета после подтверждения; Telegram остаётся без изменений | 🔄 in progress — merchant setup отсутствует, feature выключен | 2026-09-23 | — |

До включения нужны merchant onboarding VK Pay, merchant/app keys, регистрация HTTPS callback и sandbox-проверка подписи продавца/ACK (в документации VK есть расхождение SHA-256 vs SHA-1 примеры). Инструкция: `docs/vk-pay-setup.md`. Возврат оплаченного VK Pay билета не реализован: локальная отмена заблокирована, пока нет provider refund flow.
