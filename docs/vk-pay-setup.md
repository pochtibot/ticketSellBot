# VK Pay для покупки билетов

## Текущий статус

Backend/frontend поддерживают заказ билета через VK Pay, но обработка выключена по умолчанию (`VK_PAY_ENABLED=false`). Владелец проекта сообщил, что merchant-настройки пока не подключены. Без merchant account и уведомлений VK Pay платёжный путь отвечает «Оплата VK Pay пока не подключена»; он не переключается на stub-покупку. Telegram-контур остаётся прежним.

## Что подключить у VK

1. В кабинете разработчика открыть приложение VK Mini App `54698875` → **Платежи** и пройти onboarding VK Pay как продавец товаров/услуг.
2. Получить данные для платёжной формы: `merchant_id`, `client_id`, приватный ключ продавца (`merchant_private_key`) и защищённый ключ приложения (`app_secure_key`). Это отдельные реквизиты; `VK_SECRET_KEY` для подписи launch params не подходит.
3. Подать VK Pay URL уведомлений:
   `https://pochtibot.online/api/vk-pay/notifications`
   VK Pay требует регистрации URL через поддержку/merchant onboarding. Получить у VK публичный ключ системы для проверки callback.
4. Проверить тип операции `pay-to-service` и доступность приема средств для статуса продавца. Тарифы и комиссию проверить в merchant-кабинете/договоре.
5. До публичного включения добавить тестировщиков и провести sandbox-покупку. VK Pay docs предупреждают: результат `VKWebAppOpenPayForm` сам по себе не подтверждает оплату; билет создаёт только проверенный callback.
6. **Проверить подпись продавца и ACK на sandbox.** В текущих страницах документации есть несоответствие: текст формулы говорит SHA-256, но опубликованные merchant-sign и notification-response примеры дают 40-символьные SHA-1 значения. Реализация следует воспроизводимым опубликованным примерам SHA-1. Не устанавливать `VK_PAY_ENABLED=true`, пока sandbox-проверка или поддержка VK Pay не подтвердит ожидаемый вариант для merchant account.

## GitHub Secrets

После получения реквизитов добавить отдельными GitHub Secrets (не присылать их в issue/чат и не коммитить):

- `VK_PAY_ENABLED` — пока `false`; установить `true` только после успешных sandbox-проверок;
- `VK_PAY_MERCHANT_ID`;
- `VK_PAY_CLIENT_ID`;
- `VK_PAY_APP_SECURE_KEY`;
- `VK_PAY_MERCHANT_PRIVATE_KEY`;
- `VK_PAY_NOTIFICATION_PUBLIC_KEY` — PEM в одной строке с `\n` вместо реальных переносов строк.

Workflow помещает их в `.env.telegram`, используемый web-контейнером. Приватные ключи не должны попадать в браузер или логи. Все секреты VK Pay независимы от VK Mini App launch secret и Fernet key сообщества.

## Реализованный поток

- VK Mini App вызывает `POST /api/events/{event_id}/vk-pay-order` с проверенной VK-аутентификацией. Backend сам рассчитывает цену/промокод, создаёт ограниченную по времени резервацию и возвращает подпись параметров для `VKWebAppOpenPayForm`.
- Минимальная сумма VK Pay — 1 ₽. Бесплатные билеты остаются в существующем бесплатном потоке. Положительная сумма после скидки менее 1 ₽ отклоняется, без округления.
- В `POST /api/vk-pay/notifications` проверяется RSA-подпись VK Pay; сверяются версия, `client_id`, `merchant_id`, внутренний `issuer_id`, сумма и RUB. Уведомления обрабатываются идемпотентно. Активный билет и `Payment(completed)` создаются только после `status=paid` и успешной проверки callback.
- Незавершённая резервация действует 15 минут. После истечения она перестаёт удерживать место/лимит промокода. Если затем приходит подписанное `paid` уведомление, система выдаёт билет, если место осталось; если оно уже занято (или событие отсутствует), заказ переходит в `paid_unfulfilled`, VK Pay получает `payment_declined`, а покупателю показывается обращение в поддержку. Этот редкий поздний/перепроданный сценарий и поведение возврата нужно проверить в sandbox до включения.
- Клиентский результат VK Bridge используется только для UX; frontend ждёт авторитетный статус API.

## Проверки до `VK_PAY_ENABLED=true`

1. Запустить все backend/frontend тесты и проверить отсутствие регрессий Telegram.
2. Sandbox: успешный платёж → ровно один билет и одна Payment; повтор callback → тот же результат без второго билета.
3. Sandbox: закрытие формы/отказ → активного билета нет; пропущенный callback → статус остаётся ожидающим, билет не виден как купленный.
4. Отклонить неверные подпись, версию, `client_id`, `merchant_id`, `issuer_id`, amount и currency; проверить, что ни один такой callback не создаёт билет.
5. Проверить одновременные покупки последнего места и промокода, истечение заказа, статус-опрос и повторную отправку одной формы.
6. В интерфейсе и callback проверить VK-контур: без Telegram SDK, Telegram-ссылок и browser dialogs.
7. Проверить cancel: оплаченный VK Pay билет нельзя пометить возвращённым до добавления настоящего refund flow. Не включать обработчик платных покупок, если согласованная политика возвратов требует автоматического VK Pay возврата.

## Официальная документация

- [VK Mini Apps: платежи и монетизация](https://dev.vk.com/ru/mini-apps/monetization/payments)
- [VK Bridge: VKWebAppOpenPayForm](https://dev.vk.com/ru/bridge/VKWebAppOpenPayForm)
- [Создание платежа VK Pay](https://dev.vk.com/ru/pay/payment-form/payment-form-for-developers/payment-create)
- [Подпись приложения](https://dev.vk.com/ru/pay/payment-form/payment-form-for-developers/app-sign-calculation)
- [Подпись продавца](https://dev.vk.com/ru/pay/payment-form/payment-form-for-developers/merchant-sign-calculation)
- [Платёжные уведомления продавца](https://dev.vk.com/ru/pay/seller/notifications)
