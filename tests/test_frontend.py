"""
Фронтенд VK Mini App (#165): структурные smoke-тесты.

Проверяем, что vk-app.html переиспользует app.js (VK-детект), подключён
vk-bridge, и что общие страницы (index.html / vk-app.html) синхронны
по ключевым id.
"""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "web" / "static"

APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
VK_APP = (STATIC / "vk-app.html").read_text(encoding="utf-8")
STYLES = (STATIC / "styles.css").read_text(encoding="utf-8")


def test_vk_app_includes_bridge_and_appjs():
    assert "vk-bridge" in VK_APP
    assert "app.js" in VK_APP


def test_vk_app_has_common_page_ids():
    for pid in ["page-home", "page-events", "page-tickets", "page-profile",
                "page-admin", "page-my-vk-groups", "tabBar"]:
        assert pid in VK_APP


def test_index_has_vk_groups_page():
    # VK-группы доступны и из TG (организатор публикует в VK) — страница в обоих.
    assert 'id="page-my-vk-groups"' in INDEX
    assert 'id="page-my-vk-groups"' in VK_APP


def test_appjs_has_vk_auth():
    assert "initVKAuth" in APP_JS
    assert "VKWebAppGetLaunchParams" in APP_JS
    assert "X-VK-Init-Data" in APP_JS
    assert 'state.platform = "vk"' in APP_JS


def test_appjs_has_vk_groups_ui():
    assert "showMyVKGroups" in APP_JS
    assert "addMyVKGroup" in APP_JS
    assert "removeMyVKGroup" in APP_JS


def test_appjs_has_linking_ui():
    assert "createVKLinkCode" in APP_JS
    assert "linkVKByCode" in APP_JS
    assert "/api/me/link-code" in APP_JS
    assert "/api/me/link" in APP_JS


def test_appjs_initvk_handles_direct_object():
    # vk-bridge 3.x возвращает VKWebAppGetLaunchParams как объект НАПРЯМУЮ
    # (без обёртки launch_params): { vk_user_id, vk_ts, sign, ... }.
    # Раньше код читал только res.launch_params — из-за этого initData
    # оставался пустым и VK Mini App показывал «Откройте кабинет в Telegram».
    # После фикса белого экрана: приоритет — launch params из URL-query
    # (VK передаёт их в iframe), bridge — fallback с таймаутом.
    # 1. Приоритет URL (/vk-app?vk_user_id=...&sign=...) — sign берётся из query:
    assert 'qs.get("sign")' in APP_JS and 'qs.get("vk_user_id")' in APP_JS
    # 2. Прямой объект vk-bridge (sign на верхнем уровне) обрабатывается:
    assert "lp.sign && lp.vk_user_id" in APP_JS
    # 3. Объект/строка нормализуются в единый dict, затем — в initData:
    assert "normalizeVKLaunchParams" in APP_JS
    assert "Object.entries(lp)" in APP_JS
    # 4. initData кладётся в state (то, что уходит в X-VK-Init-Data):
    assert "state.initData = btoa(query)" in APP_JS


def test_appjs_buyer_ticket_qr_and_code():
    """Покупатель видит код для входа + может показать QR своего билета."""
    # Код для входа (validation_code) показывается в списке билетов
    assert "Код для входа" in APP_JS
    assert "showBuyerTicketQr" in APP_JS
    assert "downloadBuyerQr" in APP_JS
    # QR-эндпоинт покупателя (без pro)
    assert "/api/tickets/${ticketId}/qr" in APP_JS
    # VK-авторизация через authHeaders (X-VK-Init-Data / X-Init-Data)
    assert "authHeaders" in APP_JS
    assert "X-VK-Init-Data" in APP_JS


def test_appjs_vk_ticket_dm_flow():
    """VK: мягкий запрос → VKWebAppAllowMessagesFromGroup → POST send-vk."""
    assert "offerVkTicketDm" in APP_JS
    assert "VKWebAppAllowMessagesFromGroup" in APP_JS
    assert "vk_group_id" in APP_JS
    assert "/send-vk" in APP_JS
    assert "Билет отправлен в личные сообщения ВКонтакте" in APP_JS


def test_appjs_invite_links_flow():
    """Пригласительные как ссылки: выдача ссылки + активация гостем по ?invite=."""
    # Выдача: организатор получает ссылку на пригласительное
    assert "inviteLink" in APP_JS
    assert "?invite=" in APP_JS
    # Гость: deep-link ?invite=<код> → страница активации
    assert "showInvitePage" in APP_JS
    assert "params.get(\"invite\")" in APP_JS
    # Активация
    assert "claimInvite" in APP_JS
    assert "/api/invites/${encodeURIComponent(code)}/claim" in APP_JS
    assert "Активировать" in APP_JS


def test_appjs_ticket_presentation_free_vs_paid():
    """A: бесплатный билет → код, платный → QR (ветвление по is_free)."""
    assert "t.is_free" in APP_JS
    assert "const isFree = !!t.is_free" in APP_JS
    # free-ветка: код для входа
    assert "Код для входа" in APP_JS
    # paid-ветка: кнопка QR и подпись «платный билет»
    assert "Показать QR" in APP_JS
    assert "Платный билет" in APP_JS
    # ветвление: тернарник по isFree
    assert "isFree" in APP_JS


def test_appjs_event_premium():
    """C: per-event премиум — canPaid учитывает is_premium события."""
    assert "canPaid" in APP_JS
    assert "is_premium" in APP_JS
    assert "isPro() || !!(event && event.is_premium)" in APP_JS
    # эндпоинт покупки премиума
    assert "/premium" in APP_JS


# ─── QR-сканер для админа (#94, Feature Future #5) ────────────────────


def test_html_includes_jsqr():
    """jsQR подключён в оба entry (TG и VK) перед app.js.

    Локально (/static/jsQR.js), а не с внешнего CDN: в изолированном iframe VK
    внешние CDN недоступны, блокирующий скрипт jsqr не грузился → app.js не стартовал
    (белый экран). Все скрипты — с нашего домена.
    """
    assert "/static/jsQR.js" in INDEX
    assert "/static/jsQR.js" in VK_APP


def test_appjs_has_qr_scanner():
    """Сканер: живой поток камеры (getUserMedia + jsQR)."""
    assert "openQrScanner" in APP_JS
    assert "getUserMedia" in APP_JS
    assert "facingMode: \"environment\"" in APP_JS
    assert "jsQR(" in APP_JS
    assert "closeQrScanner" in APP_JS
    # stop потока при закрытии — освобождение камеры
    assert "getTracks().forEach(t => t.stop())" in APP_JS


def test_appjs_has_photo_fallback():
    """Фото-фоллбек: нативный capture (работает в Android WebView)."""
    assert "qrScanPhotoFallback" in APP_JS
    assert "scanFromPhoto" in APP_JS
    assert "accept=\"image/*\"" in APP_JS
    assert "capture=\"environment\"" in APP_JS


def test_appjs_checkin_has_scan_button():
    """На странице check-in есть кнопка «Сканировать QR»."""
    assert "Сканировать QR" in APP_JS


def test_appjs_scanner_format_gate():
    """Формат-гейт: авто-check-in только для кода билета XXXX-XXXX."""
    assert "QR_CODE_RE" in APP_JS
    assert "^[0-9A-F]{4}-[0-9A-F]{4}$" in APP_JS


# ─── Промокоды (скидки на билеты, pro) ──────────────────────────


def test_appjs_promo_buy_flow():
    """Поле «Промокод» на странице покупки и передача promo_code в body."""
    assert "Промокод" in APP_JS
    assert "promoInput_" in APP_JS
    assert "promo_code" in APP_JS
    assert "body: JSON.stringify(promo ? { promo_code: promo } : {})" in APP_JS


def test_appjs_admin_promo_section():
    """Секция «Промокоды» в админ-панели события."""
    assert "Промокоды" in APP_JS
    assert "/promo-codes" in APP_JS
    assert "adminCreatePromo" in APP_JS
    assert "adminTogglePromo" in APP_JS


# ─── Динамические цены по дате (early bird, pro) ────────────────


def test_appjs_dynamic_pricing():
    """Секция «Цены по дате» в форме/админке + CRUD-функции."""
    assert "Цены по дате" in APP_JS
    assert "/price-ranges" in APP_JS
    assert "addPriceRangeRow" in APP_JS
    assert "adminCreatePriceRange" in APP_JS
    assert "adminDeletePriceRange" in APP_JS


# ─── Admin-подписка организатора (суперадмин) ───────────────────


def test_appjs_admin_user_subscribe():
    """Суперадмин выдаёт подписку организатору в «Инфо о пользователе»."""
    assert "Подписать" in APP_JS
    assert "adminUserSubscribe" in APP_JS
    assert "/subscription" in APP_JS


def test_appjs_admin_user_list_subscribe():
    """В списке «Пользователи» суперадмин может подписать прямо из карточки."""
    assert "adminListUserSubscribe" in APP_JS
    assert "ul_sub_days_" in APP_JS
    assert "🟢 Подписать" in APP_JS


# ─── Редизайн: постеры, аватар, переходы ───────────────────────


def test_appjs_has_event_poster():
    """Постеры мероприятий на карточках/деталях."""
    assert "event-poster" in APP_JS
    assert "/media" in APP_JS


def test_appjs_profile_avatar_photo():
    """Аватар из фото Telegram (initDataUnsafe.user.photo_url)."""
    assert "photo_url" in APP_JS
    assert "profile-avatar-img" in APP_JS


def test_appjs_minimalism_classes():
    """Fade-переходы страниц (page-enter в JS + fadeIn в CSS)."""
    assert "page-enter" in APP_JS
    assert "fadeIn" in STYLES


def test_styles_minimalism():
    """Сдержанный минимализм: приглушённые бейджи, тени, утилиты."""
    assert "--badge-green-bg" in STYLES
    assert "--shadow-card" in STYLES
    assert ".profile-avatar-img" in STYLES
    assert ".event-poster" in STYLES


def test_profile_tab_has_navigation_handler():
    """Вкладка «Я» должна вести на профиль в обоих Mini App shell."""
    for shell in (INDEX, VK_APP):
        assert 'data-tab="me" onclick="showProfile()"' in shell
    assert "function showProfile" in APP_JS
    assert 'showPage("profile")' in APP_JS
    assert "renderProfile()" in APP_JS


# ─── Матрица ролей: ролевое меню и ЛК ──────────────────────────


def test_appjs_role_menu():
    """Ролевые карточки главной + минимальный ЛК пользователя."""
    assert "Стать организатором" in APP_JS
    assert "becomeOrganizer" in APP_JS
    assert "Мои мероприятия" in APP_JS
    assert "Мои площадки" in APP_JS
    assert "showAdminDashboard" in APP_JS


def test_appjs_has_group_flag():
    """Флаг площадки организатора (has_group / organizer_with_group)."""
    assert "has_group" in APP_JS
    assert "organizer_with_group" in APP_JS or "isOrganizerWithGroup" in APP_JS


# ─── Изоляция VK Mini App по п. 4.1.8 ─────────────────────────────


def test_vk_shell_has_no_external_platform_assets_or_links():
    """VK shell не должен загружать SDK или ссылки других площадок."""
    assert "https://" not in VK_APP
    assert "http://" not in VK_APP
    assert "mailto:" not in VK_APP
    assert "t.me" not in VK_APP.lower()
    assert "telegram.org" not in VK_APP.lower()
    assert "telegram.org/js/telegram-web-app.js" in INDEX


def test_vk_mode_has_platform_scoped_copy_and_profile():
    """VK-контур должен иметь отдельные platform-safe ветки."""
    assert "isVKMode" in APP_JS
    assert "внутри VK" in APP_JS
    assert "ID пользователя" in APP_JS
    assert "https://vk.ru/club241015257" in APP_JS
    assert "createVKLinkCode" in APP_JS
    assert "Telegram ID" in APP_JS
    assert "https://t.me/aerovir" in APP_JS
    assert "mailto:aerovir@mail.ru" in APP_JS

    profile_start = APP_JS.index("function renderProfile")
    profile_end = APP_JS.index("\nasync function editName", profile_start)
    profile_body = APP_JS[profile_start:profile_end]
    assert "isVKMode()" in profile_body
    organizer_start = APP_JS.index("function renderOrganizerSections")
    organizer_end = APP_JS.index("\nfunction renderProfile", organizer_start)
    vk_branch = APP_JS[organizer_start:organizer_end]
    vk_branch = vk_branch[vk_branch.index("if (isVKMode())"):vk_branch.index("return {")]
    assert "Привязать Telegram" not in vk_branch


def test_vk_no_auth_does_not_expose_telegram_copy():
    """Telegram fallback copy remains outside the VK-specific branch."""
    no_auth_start = APP_JS.index("function showNoInitData")
    no_auth_end = APP_JS.index("\n// ═", no_auth_start + 1)
    no_auth_body = APP_JS[no_auth_start:no_auth_end]
    assert "Откройте кабинет в Telegram" in no_auth_body
    assert "внутри VK" in no_auth_body
    assert "isVKMode()" in APP_JS
    assert "window.location.href" not in no_auth_body


def test_vk_terms_use_neutral_platform_copy():
    """VK policy summary does not mention another platform."""
    terms_start = APP_JS.index("function renderTerms")
    terms_end = APP_JS.index("\nfunction platformUserId", terms_start)
    terms_body = APP_JS[terms_start:terms_end]
    assert "isVKMode()" in terms_body
    assert '"VK/Telegram"' not in terms_body
    assert "идентификатор пользователя" in terms_body


def test_vk_profile_does_not_unconditionally_render_external_support():
    """External contacts are rendered by a platform-aware helper."""
    support_start = APP_JS.index("function renderSupport")
    support_end = APP_JS.index("\nfunction renderOrganizerSections", support_start)
    support_body = APP_JS[support_start:support_end]
    assert "isVKMode()" in support_body
    assert "https://vk.ru/club241015257" in support_body
    assert "https://t.me/aerovir" in support_body
    assert "mailto:aerovir@mail.ru" in support_body

    vk_start = support_body.index("if (isVKMode())")
    vk_body = support_body[vk_start:support_body.index("    }\n    return `", vk_start)]
    assert "https://vk.ru/club241015257" in vk_body
    assert "https://t.me/aerovir" not in vk_body
    assert "mailto:aerovir@mail.ru" not in vk_body


def test_admin_binary_downloads_use_platform_auth_headers():
    """QR/CSV админа должны работать с VK launch params."""
    for function_name in ("showTicketQr", "downloadQr", "downloadCsv"):
        start = APP_JS.index(f"async function {function_name}")
        end = APP_JS.find("\nasync function ", start + 1)
        body = APP_JS[start:] if end == -1 else APP_JS[start:end]
        assert "authHeaders()" in body, function_name
        assert '"X-Init-Data": state.initData' not in body, function_name


def test_vk_super_admin_ui_is_platform_gated():
    """Global super-admin tools are not available from VK."""
    assert "if (isVKMode() || state.role !== \"super_admin\") return;" in APP_JS
    assert 'state.platform = "vk"' in APP_JS


def test_telegram_profile_contract_is_preserved():
    """Telegram support and linking remain available in the shared app."""
    assert "Telegram ID" in APP_JS
    assert "https://t.me/aerovir" in APP_JS
    assert "mailto:aerovir@mail.ru" in APP_JS
    assert "createVKLinkCode" in APP_JS


def test_both_shells_have_app_dialog_host():
    """All platform dialogs must be rendered by the app, not the browser."""
    for shell in (INDEX, VK_APP):
        for element_id in (
            'id="appDialogOverlay"', 'id="appDialogTitle"',
            'id="appDialogMessage"', 'id="appDialogInput"',
            'id="appDialogPrimary"', 'id="appDialogSecondary"',
            'id="appDialogClose"',
        ):
            assert element_id in shell


def test_vk_dialog_helpers_use_dom_and_no_direct_prompts_remain():
    """VK must use app dialogs for alert/confirm/prompt interactions."""
    assert "openAppDialog" in APP_JS
    assert "isVKMode()" in APP_JS
    assert "window.alert(" not in APP_JS
    assert "window.confirm(" not in APP_JS
    assert "window.prompt(" not in APP_JS
    # Application handlers must use the shared async prompt helper.
    assert "await tgPrompt(" in APP_JS
    assert "const newName = await tgPrompt" in APP_JS
    assert "const seats = await tgPrompt" in APP_JS


def test_vk_terms_open_in_app_dialog():
    """Viewing onboarding terms must not invoke a browser alert."""
    terms_start = APP_JS.index("function renderTerms")
    terms_end = APP_JS.index("\nfunction platformUserId", terms_start)
    terms_body = APP_JS[terms_start:terms_end]
    assert "await tgAlert(text)" in terms_body
    assert "openAppDialog" in APP_JS
    assert "window.alert" not in terms_body


def test_dialog_styles_cover_vk_themes():
    """App dialog has themed overlay/card/actions and active state."""
    for selector in (".app-dialog-overlay", ".app-dialog-overlay.active",
                      ".app-dialog-card", ".app-dialog-message",
                      ".app-dialog-actions"):
        assert selector in STYLES
    assert "vk-theme-dark" in STYLES
    assert "vk-theme-light" in STYLES
