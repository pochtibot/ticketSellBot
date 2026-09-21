/**
 * TicketBot Mini App — main application logic.
 *
 * Telegram WebApp SDK integration:
 * - window.Telegram.WebApp.initData — raw initData for API auth
 * - window.Telegram.WebApp.HapticFeedback — haptic feedback
 * - window.Telegram.WebApp.MainButton — native main button
 * - CSS variables: --tg-theme-bg-color, --tg-theme-button-color, etc.
 */

// ─── State ──────────────────────────────────────────────────────
const state = {
    initData: "",
    platform: "telegram", // "telegram" | "vk"
    vkAppId: "",          // VK App ID из launch params (для VKWebAppGetCommunityToken)
    events: [],
    currentEvent: null,
    tickets: [],
    lastAction: null, // function name for retry
    // Личный кабинет / админка
    me: null,
    role: "user",
    adminEvents: [],
    currentAdminEvent: null,
    adminChannels: [],
    globalStats: null,
    adminTab: "events",
};

// ═══════════════════════════════════════════════════════════════
// App-owned dialogs (VK must not use browser alert/confirm/prompt)
// ═══════════════════════════════════════════════════════════════

let appDialogState = null;

function closeAppDialog(result) {
    if (!appDialogState || appDialogState.done) return;
    const dialog = appDialogState;
    dialog.done = true;
    if (dialog.cleanup) dialog.cleanup();
    const overlay = document.getElementById("appDialogOverlay");
    if (overlay) overlay.classList.remove("active");
    appDialogState = null;
    if (dialog.previousFocus && dialog.previousFocus.focus) dialog.previousFocus.focus();
    dialog.resolve(result);
}

function openAppDialog({ mode = "alert", title = "TicketBot", message = "", defaultValue = "", primaryText = "ОК", secondaryText = "Отмена" }) {
    if (appDialogState) closeAppDialog(mode === "confirm" || mode === "prompt" ? null : undefined);
    const overlay = document.getElementById("appDialogOverlay");
    const titleEl = document.getElementById("appDialogTitle");
    const messageEl = document.getElementById("appDialogMessage");
    const inputWrap = document.getElementById("appDialogInputWrap");
    const input = document.getElementById("appDialogInput");
    const primary = document.getElementById("appDialogPrimary");
    const secondary = document.getElementById("appDialogSecondary");
    const close = document.getElementById("appDialogClose");
    if (!overlay || !titleEl || !messageEl || !inputWrap || !input || !primary || !secondary || !close) {
        return Promise.resolve(mode === "confirm" ? false : mode === "prompt" ? null : undefined);
    }

    titleEl.textContent = title;
    messageEl.textContent = message;
    inputWrap.classList.toggle("active", mode === "prompt");
    input.value = defaultValue || "";
    input.setAttribute("aria-hidden", mode === "prompt" ? "false" : "true");
    primary.textContent = primaryText;
    secondary.textContent = secondaryText;
    primary.className = "btn btn-primary" + (mode === "confirm" ? " app-dialog-destructive" : "");
    secondary.classList.toggle("active", mode !== "alert");
    close.classList.toggle("active", mode === "alert");
    overlay.classList.add("active");

    return new Promise(resolve => {
        const previousFocus = document.activeElement;
        const dialog = { resolve, previousFocus, done: false, cleanup: null };
        const finish = value => closeAppDialog(value);
        const onKeydown = event => {
            if (event.key === "Escape") finish(mode === "confirm" || mode === "prompt" ? null : undefined);
            else if (event.key === "Enter" && mode === "prompt" && document.activeElement === input) finish(input.value);
        };
        const onBackdrop = event => {
            if (event.target === overlay && mode !== "prompt") finish(mode === "confirm" ? false : undefined);
        };
        primary.onclick = () => finish(mode === "prompt" ? input.value : mode === "confirm" ? true : undefined);
        secondary.onclick = () => finish(mode === "confirm" || mode === "prompt" ? (mode === "confirm" ? false : null) : undefined);
        close.onclick = () => finish(undefined);
        overlay.addEventListener("click", onBackdrop);
        document.addEventListener("keydown", onKeydown);
        dialog.cleanup = () => {
            overlay.removeEventListener("click", onBackdrop);
            document.removeEventListener("keydown", onKeydown);
            primary.onclick = null;
            secondary.onclick = null;
            close.onclick = null;
        };
        appDialogState = dialog;
        if (mode === "prompt") setTimeout(() => input.focus(), 0);
        else primary.focus();
    });
}

function tgShowPopup(title, message, buttons) {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.showPopup) {
        return new Promise(resolve => tg.showPopup({
            title: title || "TicketBot", message: message || "",
            buttons: buttons || [{ type: "close" }],
        }, resolve));
    }
    const isConfirm = (buttons || []).some(button => button.type === "cancel");
    return openAppDialog({ mode: isConfirm ? "confirm" : "alert", title, message });
}

function tgConfirm(message, okText = "OK", cancelText = "Отмена") {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.showPopup) {
        return new Promise(resolve => tg.showPopup({
            title: "Подтверждение", message,
            buttons: [
                { id: "cancel", type: "cancel", text: cancelText },
                { id: "ok", type: "ok", text: okText },
            ],
        }, buttonId => resolve(buttonId === "ok")));
    }
    return openAppDialog({ mode: "confirm", title: "Подтверждение", message, primaryText: okText, secondaryText: cancelText });
}

function tgPrompt(message, defaultValue = "") {
    // Telegram Popup не поддерживает ввод текста; app-dialog работает одинаково на всех платформах.
    return openAppDialog({ mode: "prompt", title: "Ввод", message, defaultValue, primaryText: "Готово" });
}

function tgAlert(message) {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.showAlert) {
        return new Promise(resolve => tg.showAlert({ message }, resolve));
    }
    return openAppDialog({ mode: "alert", title: "TicketBot", message });
}


// ─── Init ───────────────────────────────────────────────────────

// VK Mini App: получить launch params через VK Bridge и подготовить
// X-VK-Init-Data (base64 query string: vk_user_id=...&sign=...).
// Нормализовать VK launch params в объект { vk_user_id, vk_ts, sign, ... }.
// vk-bridge 3.x возвращает объект НАПРЯМУЮ (без обёртки launch_params);
// старые версии — строку query в launch_params. Запасной вариант — URL (/vk-app?vk_*).
function normalizeVKLaunchParams(res) {
    if (!res) return {};
    if (res.launch_params) {
        // Старый формат: launch_params = "vk_user_id=...&sign=..."
        if (typeof res.launch_params === "object") return res.launch_params;
        try {
            const params = new URLSearchParams(res.launch_params);
            const out = {};
            params.forEach((v, k) => { out[k] = v; });
            return out;
        } catch (e) {
            console.warn("VK launch_params parse failed", e);
            return {};
        }
    }
    // vk-bridge 3.x: sign/vk_user_id на верхнем уровне объекта.
    return res;
}

async function initVKAuth() {
    const bridge = window.vkBridge;
    try {
        // VKWebAppInit — ОБЯЗАТЕЛЬНЫЙ сигнал VK, что приложение инициализируется.
        // Без него VK показывает заглушку «Проблема с инициализацией приложения»
        // (даже если launch params получены из URL). Вызываем ВСЕГДА, если bridge есть.
        if (bridge) {
            try { bridge.send("VKWebAppInit"); } catch (e) {}
        }
        // Приоритет — launch params из URL-query: VK всегда передаёт их в iframe
        // (/vk-app?vk_user_id=...&vk_ts=...&sign=...). Это работает даже если
        // bridge ещё не готов или не ответил (на десктопе VKWebAppGetLaunchParams
        // может зависнуть — promise не резолвится вне VK-окружения).
        const qs = new URLSearchParams(window.location.search);
        let res = null;
        if (qs.get("sign") && qs.get("vk_user_id")) {
            const fromUrl = {};
            qs.forEach((v, k) => { fromUrl[k] = v; });
            res = fromUrl;
        }
        // Если в URL нет — пробуем через bridge (с таймаутом, чтобы не зависнуть).
        if (!res && bridge) {
            res = await Promise.race([
                bridge.send("VKWebAppGetLaunchParams"),
                new Promise(r => setTimeout(() => r(null), 800)),
            ]);
        }
        const lp = normalizeVKLaunchParams(res);
        state.vkAppId = String(lp.vk_app_id || "");
        if (lp.sign && lp.vk_user_id) {
            const query = Object.entries(lp)
                .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
                .join("&");
            state.initData = btoa(query);
            state.platform = "vk";
        } else {
            console.warn("VK launch params отсутствуют (sign/vk_user_id)");
        }
    } catch (e) {
        console.warn("VK init failed", e);
    }
}

// VK Mini App: применить тему (dark/light) из VKWebAppGetConfig.
// В VK нет --tg-theme-* переменных (их отдаёт только Telegram WebView),
// поэтому получаем appearance/scheme через bridge и ставим класс на <html>.
async function applyVKTheme() {
    try {
        const bridge = window.vkBridge;
        if (!bridge) return;
        const config = await Promise.race([
            bridge.send("VKWebAppGetConfig"),
            new Promise(r => setTimeout(() => r(null), 800)),
        ]);
        let dark = false;
        if (config) {
            const appearance = config.appearance; // 'dark' | 'light' (iOS/Android)
            const scheme = config.scheme;          // 'space_gray' | 'vkcom_dark' → тёмная
            dark = appearance === "dark" || scheme === "space_gray" || scheme === "vkcom_dark";
        }
        document.documentElement.classList.add(dark ? "vk-theme-dark" : "vk-theme-light");
    } catch (e) {
        // Тема не критична — по умолчанию светлая
        document.documentElement.classList.add("vk-theme-light");
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    // VK Mini App (открывается на /vk-app). Определяем по пути, а не по vkBridge:
    // vk-bridge мог не загрузиться (CDN недоступен в изолированном iframe VK),
    // но launch params всё равно приходят в URL-query (/vk-app?vk_*&sign=...).
    const isVK = window.location.pathname.startsWith("/vk-app");
    if (isVK) {
        // Mark the platform before auth so an invalid VK launch never falls
        // through to Telegram-specific fallback copy.
        state.platform = "vk";
        await initVKAuth();
        await applyVKTheme();
    } else if (window.Telegram && window.Telegram.WebApp) {
        // Init Telegram WebApp
        const tg = window.Telegram.WebApp;
        tg.ready();
        tg.expand(); // Expand to full height
        state.initData = tg.initData || "";
    } else {
        // Telegram Desktop (и некоторые клиенты) открывают Mini App как
        // обычную страницу и передают данные в URL-хэше: #tgWebAppData=...
        // SDK (window.Telegram) в этом случае не внедряется, но данные есть.
        state.initData = extractInitDataFromUrl() || "";
        if (!state.initData) {
            console.warn("Telegram WebApp SDK not found — running in dev mode");
        }
    }

    // Если initData пуст — кабинет открыт вне Telegram (не как Mini App).
    // Показываем понятное сообщение вместо пустого списка.
    if (!state.initData && window.location.hostname !== "localhost") {
        showNoInitData();
        return;
    }

    // Онбординг (п.1.1.4 Правил VK): при первом запуске требуем принятия условий.
    // Пока не приняты — блокируем доступ к кабинету (показываем только онбординг).
    if (!hasAcceptedTerms()) {
        if (showOnboarding()) {
            // Ждём нажатия «Принять» → acceptTerms() вызовет runAppStart()
            return;
        }
    }

    await runAppStart();
});

// Основной запуск кабинета (после инициализации auth и принятия условий).
async function runAppStart() {
    // Заглушка для планшетов (чек-лист VK): широкий тач-экран → рекомендуем телефон.
    if (isTablet()) {
        showTabletStub();
        return;
    }

    // Загружаем профиль/роль и строим таб-бар (best-effort: не блокируем покупку)
    try {
        await loadMe();
        renderTabBar();
    } catch (e) {
        console.warn("loadMe failed", e);
    }

    // Check if opened with specific event_id
    const params = new URLSearchParams(window.location.search);
    const eventId = params.get("event_id");
    const inviteCode = params.get("invite");

    if (inviteCode) {
        // Пригласительное-ссылка: гость активирует место.
        await showInvitePage(inviteCode);
    } else if (eventId) {
        await showEventDetail(eventId);
    } else {
        await showHome();
    }
}

// Планшет: широкий тач-экран (iPad и т.п.). Заглушка по чек-листу VK.
function isTablet() {
    try {
        const touch = ("ontouchstart" in window) || (navigator.maxTouchPoints > 0);
        return touch && window.innerWidth >= 700 && window.innerHeight >= 700;
    } catch (e) {
        return false;
    }
}

function showTabletStub() {
    updateHeader("TicketBot");
    showPage("tablet");
    document.getElementById("tabBar").style.display = "none";
}

function dismissTabletStub() {
    // Продолжаем обычный запуск (как после онбординга)
    runAppStart();
}

// ═══════════════════════════════════════════════════════════════
// Пригласительное по ссылке (?invite=<код>)
// ═══════════════════════════════════════════════════════════════

async function showInvitePage(code) {
    updateToolbar("Приглашение", false, false);
    showPage("events");
    const container = document.getElementById("eventsContent");

    try {
        // Получить информацию о пригласительном, чтобы показать название/дату.
        // Для этого коды билетов не имеют публичного GET — используем validate
        // как админ? Нет. Показываем просто приглашение с кнопкой активации.
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🎟</div>
                <h3>Вас пригласили на мероприятие</h3>
                <p>Нажмите «Активировать», чтобы получить билет.
                Оно появится в разделе «Мои билеты».</p>
                <button class="btn btn-primary btn-lg" onclick="claimInvite('${escapeHtml(code)}')">✅ Активировать</button>
            </div>
        `;
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><p>Ошибка загрузки приглашения</p></div>`;
    }
}

async function claimInvite(code) {
    const btn = document.querySelector('#eventsContent .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = "⏳ Активирую..."; }
    try {
        const res = await api(`/api/invites/${encodeURIComponent(code)}/claim`, { method: "POST" });
        showToast(`✅ Билет получен: ${res.event_title}`);
        await showMyTickets();
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = "✅ Активировать"; }
        showError(e.message || "Ошибка активации");
    }
}

// ═══════════════════════════════════════════════════════════════
// Личный кабинет / роль
// ═══════════════════════════════════════════════════════════════

async function loadMe() {
    const me = await api("/api/me");
    state.me = me;
    state.role = me.role || "user";
    // Матрица ролей: derived-флаги для ролевого меню
    state.isOrganizer = state.role === "organizer";
    state.isOrganizerWithGroup = state.role === "organizer" && !!me.has_group;
    state.isSuper = state.role === "super_admin";
    return me;
}

// Есть ли у организатора pro-подписка (пользователь или хотя бы один канал pro)?
function isPro() {
    if (!state.me) return false;
    if (state.me.subscription_tier === "pro") return true;
    // организатор с pro-каналом
    if ((state.me.channels || []).some(c => c.subscription_tier === "pro")) return true;
    return false;
}

function extractInitDataFromUrl() {
    // Telegram Desktop передаёт initData в фрагменте URL: #tgWebAppData=...
    // Это тот же initData, что mobile отдаёт через window.Telegram.WebApp.initData.
    try {
        const fragment = window.location.hash || "";
        if (!fragment.includes("tgWebAppData=")) return "";
        const params = new URLSearchParams(fragment.startsWith("#") ? fragment.slice(1) : fragment);
        return params.get("tgWebAppData") || "";
    } catch (e) {
        console.warn("extractInitDataFromUrl failed", e);
        return "";
    }
}

function isVKMode() {
    return state.platform === "vk";
}

function showNoInitData() {
    // Кабинет открыт без данных запуска — показываем только platform-safe copy.
    updateToolbar("TicketBot", false, false);
    showPage("events");

    const content = isVKMode()
        ? `
            <h3>Не удалось открыть приложение</h3>
            <p>Не удалось получить данные запуска. Закройте приложение и откройте его снова внутри VK.</p>
          `
        : `
            <h3>Откройте кабинет в Telegram</h3>
            <p>Личный кабинет работает только внутри Telegram Mini App.<br>
            Откройте чат с ботом и нажмите кнопку <b>«Мероприятия»</b> внизу,
            либо кнопку <b>«🎫 Открыть кабинет»</b> в анонсах канала.</p>
          `;

    document.getElementById("eventsContent").innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">⚠️</div>
            ${content}
        </div>
    `;
}

// ═══════════════════════════════════════════════════════════════
// Онбординг: принятие условий (п.1.1.4 Правил VK Mini Apps)
//
// При первом запуске (localStorage['terms_accepted'] отсутствует) показываем
// экран с условиями. До принятия — доступ к кабинету заблокирован. После
// принятия сохраняем флаг и продолжаем обычный запуск.
// ═══════════════════════════════════════════════════════════════
const TERMS_STORAGE_KEY = "ticketbot_terms_accepted";

function hasAcceptedTerms() {
    try {
        return localStorage.getItem(TERMS_STORAGE_KEY) === "1";
    } catch (e) {
        // localStorage может быть недоступен (приватный режим) — не блокируем
        return true;
    }
}

function showOnboarding() {
    const el = document.getElementById("onboardingOverlay");
    if (!el) return false;
    el.classList.add("active");
    renderTerms();
    return true;
}

function hideOnboarding() {
    const el = document.getElementById("onboardingOverlay");
    if (el) el.classList.remove("active");
}

function acceptTerms() {
    try {
        localStorage.setItem(TERMS_STORAGE_KEY, "1");
    } catch (e) { /* не критично */ }
    hideOnboarding();
    // Продолжаем обычный запуск (повторно инициализируем стартовую логику)
    runAppStart();
}

// Показать условия (соглашение/политику) по клику на ссылку.
// В режиме «типовые документы VK» условия принимаются на платформе VK;
// здесь показываем краткую выжимку + ссылки на полные тексты (когда размещены).
function renderTerms() {
    const links = document.querySelectorAll(".terms-link");
    links.forEach(link => {
        link.onclick = async (e) => {
            e.preventDefault();
            const type = link.getAttribute("data-terms");
            const text = type === "user_agreement"
                ? "Пользовательское соглашение: использование сервиса «Билетёр» означает согласие с условиями предоставления услуг, правилами покупки и возврата билетов, обязанностями организаторов и ответственностью сторон. Организатор самостоятельно определяет и несёт ответственность за возрастное ограничение мероприятия (0+, 6+, 12+, 16+, 18+), корректность этой маркировки и допуск лиц до 18 лет на 18+ мероприятия (ФЗ-436)."
                : (isVKMode()
                    ? "Политика конфиденциальности: сервис обрабатывает идентификатор пользователя, имя и данные билетов для продажи билетов и работы функций организатора. Данные, необходимые для публикации в сообществах VK, хранятся в защищённом виде. Вы можете удалить аккаунт в любое время."
                    : "Политика конфиденциальности: сервис обрабатывает данные (идентификатор VK/Telegram, имя, билеты) для продажи билетов и работы функций организатора. Токены доступа VK-групп хранятся в зашифрованном виде. Вы можете удалить аккаунт в любое время.");
            await tgAlert(text);
        };
        link.removeAttribute("href");
        link.setAttribute("role", "button");
        link.setAttribute("tabindex", "0");
        link.onkeydown = (e) => {
            if (e.key === "Enter" || e.key === " ") link.click();
        };
        link.replaceWith(link.cloneNode(true));
        const freshLink = document.querySelector(`.terms-link[data-terms="${link.getAttribute("data-terms")}"]`);
        freshLink.onclick = link.onclick;
        freshLink.onkeydown = link.onkeydown;
    });
}

function platformUserId(me) {
    return me.platform_user_id || me.vk_user_id || me.telegram_user_id || me.id || "—";
}

function renderSupport() {
    if (isVKMode()) {
        return `
            <div class="support-block">
                <h3 style="margin:20px 0 8px">🛟 Поддержка</h3>
                <p class="hint" style="margin:0 0 8px">Вопросы, замечания, помощь с покупкой билетов:</p>
                <a class="btn btn-secondary" href="https://vk.ru/club241015257" target="_blank" rel="noopener">💬 Сообщество VK</a>
            </div>`;
    }
    return `
        <div class="support-block">
            <h3 style="margin:20px 0 8px">🛟 Поддержка</h3>
            <p class="hint" style="margin:0 0 8px">Вопросы, замечания, помощь с покупкой билетов:</p>
            <a class="btn btn-secondary" href="https://t.me/aerovir" target="_blank" rel="noopener">✈️ Telegram: @aerovir</a>
            <a class="btn btn-secondary" href="mailto:aerovir@mail.ru">📧 aerovir@mail.ru</a>
        </div>`;
}

function renderOrganizerSections(me, channels) {
    if (isVKMode()) {
        return {
            extra: `
                <h3 style="margin:20px 0 10px">Мои VK-группы</h3>
                <p class="hint">Управление группами доступно в приложении.</p>`,
            actions: `
                <button class="btn btn-secondary" onclick="showMyTickets()">🎫 Мои билеты</button>
                <button class="btn btn-secondary" onclick="showMyVKGroups()">📢 VK-группы</button>`
        };
    }
    return {
        extra: `
            <h3 style="margin:20px 0 10px">Мои площадки</h3>
            ${channels.length === 0
                ? '<p class="hint">Нет каналов</p>'
                : `<div class="admin-list">
                    ${channels.map(ch => `
                        <div class="admin-list-item">
                            <div><b>${escapeHtml(ch.title || ch.telegram_channel_id)}</b>
                                <span class="badge ${ch.subscription_tier === 'pro' ? 'badge-tier-pro' : 'badge-tier-basic'}">${ch.subscription_tier}</span>
                            </div>
                            <div class="hint">${ch.is_subscription_active ? '🟢 Активна' : '🔴 Неактивна'}${ch.subscription_until ? ' до ' + formatDate(ch.subscription_until) : ''}</div>
                        </div>`).join('')}
                </div>`}`,
        actions: `
            <button class="btn btn-secondary" onclick="showMyTickets()">🎫 Мои билеты</button>
            <button class="btn btn-secondary" onclick="showMyChannels()">📢 Мои каналы</button>
            <button class="btn btn-secondary" onclick="showMyVKGroups()">📢 VK-группы</button>
            <button class="btn btn-secondary" onclick="createVKLinkCode()">🔗 Привязать VK (получить код)</button>`
    };
}

function renderProfile() {
    const me = state.me;
    const roleNames = { user: "Покупатель", organizer: "Организатор", super_admin: "Супер-админ" };
    const roleText = roleNames[me.role] || me.role;
    const channels = me.channels || [];

    // Telegram avatar is read only in the Telegram Mini App.
    const tgUser = !isVKMode() && window.Telegram && window.Telegram.WebApp
        && window.Telegram.WebApp.initDataUnsafe
        ? window.Telegram.WebApp.initDataUnsafe.user : null;
    const avatarHtml = tgUser && tgUser.photo_url
        ? `<img class="profile-avatar-img" src="${tgUser.photo_url}" alt="">`
        : '<div class="profile-avatar">👤</div>';

    let extraSections = "";
    let actionButtons = "";
    if (me.role === "user") {
        actionButtons = `
            <button class="btn btn-secondary" onclick="showMyTickets()">🎫 Мои билеты</button>
            <button class="btn btn-primary" onclick="becomeOrganizer()">🚀 Стать организатором</button>`;
    } else if (me.role === "organizer") {
        const sections = renderOrganizerSections(me, channels);
        extraSections = sections.extra;
        actionButtons = sections.actions;
    } else if (!isVKMode()) {
        actionButtons = `
            <button class="btn btn-primary" onclick="showHome()">🛠 Инструменты (главная)</button>`;
    }

    const identifierLabel = "ID пользователя";
    document.getElementById("profileContent").innerHTML = `
        <div class="profile-card">
            ${avatarHtml}
            <h2>${escapeHtml(me.name || "Пользователь")}</h2>
            <p class="hint">${identifierLabel}: <code>${escapeHtml(platformUserId(me))}</code></p>
            <span class="badge badge-role">${roleText}</span>
            <button class="btn btn-sm btn-secondary mt-12" onclick="editName()">✏️ Изменить имя</button>
        </div>
        ${extraSections}
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:16px">
            ${actionButtons}
            <button class="btn btn-danger" style="margin-top:8px" onclick="deleteAccount()">🗑 Удалить аккаунт</button>
        </div>
        ${renderSupport()}
    `;
}

async function showProfile() {
    state.lastAction = "showProfile";
    setActiveTab("me");
    updateToolbar("Я", false, false);
    showPage("profile");

    if (!state.me) {
        showLoading();
        try {
            await loadMe();
        } catch (e) {
            hideLoading();
            showError("Не удалось загрузить профиль");
            return;
        }
        hideLoading();
    }

    renderProfile();
}

function renderTabBar() {
    const bar = document.getElementById("tabBar");
    if (!bar || !state.me) {
        if (bar) bar.style.display = "none";
        return;
    }
    bar.style.display = "flex";
}

function setActiveTab(tabId) {
    document.querySelectorAll("#tabBar .tab").forEach(b => {
        b.classList.toggle("active", b.dataset.tab === tabId);
    });
}

// ═══════════════════════════════════════════════════════════════
// PAGE: Home Dashboard
// ═══════════════════════════════════════════════════════════════

async function showHome() {
    if (!state.me) { try { await loadMe(); } catch (e) { showError("Не удалось загрузить профиль"); return; } }
    setActiveTab("home");
    updateHeader("TicketBot");
    showPage("home");
    showLoading();

    try {
        const [events, tickets] = await Promise.all([
            api("/api/events").catch(() => []),
            api("/api/tickets").catch(() => []),
        ]);
        state.events = events;
        state.tickets = tickets;
    } catch (e) { /* не критично */ }

    hideLoading();
    renderHomeDashboard();
}

function renderHomeDashboard() {
    const container = document.getElementById("homeContent");
    const me = state.me || {};

    const events = state.events || [];
    const tickets = state.tickets || [];
    const channels = me.channels || [];

    // Матрица ролей: ролевые карточки главной
    let cards = [];
    if (state.role === "user") {
        // Пользователь: минимальный ЛК — лента + билеты + стать организатором
        cards.push({ icon: "🎫", value: events.length, label: "Мероприятия", onclick: "showEvents()" });
        cards.push({ icon: "🎟", value: tickets.length, label: "Мои билеты", onclick: "showMyTickets()" });
        cards.push({ icon: "🚀", value: "—", label: "Стать организатором", onclick: "becomeOrganizer()" });
    } else if (state.role === "organizer") {
        // Организатор: свои мероприятия + продажи/вход + VK-площадки.
        cards.push({ icon: "🎫", value: events.length, label: "Мои мероприятия", onclick: "showAdminEvents()" });
        cards.push({ icon: "🔍", value: "Вход", label: "Продажи / Вход", onclick: "showCheckin()" });
        if (isVKMode()) {
            cards.push({ icon: "📢", value: (me.vk_group_ids || []).length, label: "VK-группы", onclick: "showMyVKGroups()" });
        } else {
            cards.push({ icon: "📢", value: channels.length, label: "Мои площадки", onclick: "showMyChannels()" });
        }
    } else if (!isVKMode()) {
        // Супер-админские инструменты доступны только в Telegram-контуре.
        cards.push({ icon: "🔍", value: "—", label: "Поиск по коду", onclick: "showCheckin()" });
        cards.push({ icon: "📊", value: "—", label: "Статистика", onclick: "showAdminStats()" });
        cards.push({ icon: "📣", value: "—", label: "Рассылка", onclick: "showBroadcast()" });
        cards.push({ icon: "👥", value: "—", label: "Подписки", onclick: "showUserInfo()" });
        cards.push({ icon: "🩺", value: "—", label: "Здоровье", onclick: "showAdminHealth()" });
    }

    // A VK user with a stale role response must never see global admin tools.
    if (isVKMode() && state.role === "super_admin") {
        state.role = "user";
        cards = [
            { icon: "🎫", value: events.length, label: "Мероприятия", onclick: "showEvents()" },
            { icon: "🎟", value: tickets.length, label: "Мои билеты", onclick: "showMyTickets()" },
            { icon: "🚀", value: "—", label: "Стать организатором", onclick: "becomeOrganizer()" },
        ];
    }

    let html = `<h2 style="padding:16px 16px 0">Привет, ${escapeHtml(me.name || 'Гость')}!</h2>`;
    html += `<p style="padding:0 16px 12px;color:var(--tg-hint)">${roleLabel(state.role)}</p>`;
    html += `<div class="dashboard-grid">`;
    for (const c of cards) {
        html += `
            <div class="dashboard-card" onclick="${c.onclick}">
                <div class="dashboard-card-icon">${c.icon}</div>
                <div class="dashboard-card-value">${c.value}</div>
                <div class="dashboard-card-label">${c.label}</div>
            </div>`;
    }
    html += `</div>`;

    // Список мероприятий на главной
    if (events.length > 0) {
        html += `<div class="section-header">📋 Ближайшие мероприятия</div>`;
        for (const e of events.slice(0, 3)) {
            html += `
                <div class="event-card" style="margin:0 16px 8px" onclick="showEventDetail('${e.id}')">
                    <h3>${escapeHtml(e.title)}</h3>
                    <div class="hint">📅 ${formatDate(e.date)} · ${e.price > 0 ? formatPrice(e.price) : 'Бесплатно'} · ${e.available_tickets}/${e.total_tickets}</div>
                </div>`;
        }
    }

    container.innerHTML = html;
}

function roleLabel(role) {
    const labels = { user: "Покупатель", organizer: "Организатор", super_admin: "Супер-админ" };
    return labels[role] || role;
}

function updateHeader(title) {
    document.getElementById("headerTitle").textContent = title;
}

async function showAdminUserList() {
    setActiveTab("home");
    updateHeader("Пользователи");
    try {
        const users = await api("/api/admin/users");
        let html = `<h2 style="padding:16px">👥 Пользователи (${users.length})</h2>`;
        for (const u of users) {
            const active = u.is_subscription_active;
            html += `<div class="event-card" style="margin:0 16px 8px">
                <div><b>${escapeHtml(u.name || u.telegram_user_id)}</b>
                    <span class="badge ${u.subscription_tier === 'pro' ? 'badge-tier-pro' : 'badge-tier-basic'}">${escapeHtml(u.subscription_tier || '—')}</span>
                    ${active ? '<span class="badge badge-published">🟢</span>' : '<span class="badge badge-off">🔴</span>'}
                </div>
                <div class="hint">ID: ${escapeHtml(u.telegram_user_id)}</div>
                <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
                    <input class="form-input" id="ul_sub_days_${escapeHtml(u.telegram_user_id)}" type="number" min="1" value="30" style="width:64px" title="Дни">
                    <select class="form-input" id="ul_sub_tier_${escapeHtml(u.telegram_user_id)}" style="width:90px">
                        <option value="basic">basic</option>
                        <option value="pro">pro</option>
                    </select>
                    <button class="btn btn-sm btn-primary" onclick="adminListUserSubscribe('${escapeHtml(u.telegram_user_id)}')">🟢 Подписать</button>
                </div>
            </div>`;
        }
        document.getElementById("homeContent").innerHTML = html;
    } catch (e) { showToast(e.message, true); }
}

async function adminListUserSubscribe(userId) {
    const days = parseInt(document.getElementById(`ul_sub_days_${userId}`).value, 10) || 30;
    const tier = document.getElementById(`ul_sub_tier_${userId}`).value;
    try {
        await api(`/api/admin/users/${encodeURIComponent(userId)}/subscription`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ duration_days: days, tier }),
        });
        showToast("✅ Подписка выдана");
        await showAdminUserList();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function editName() {
    const me = state.me;
    const newName = await tgPrompt("Ваше имя:", me.name || "");
    if (newName === null) return;  // отмена
    const name = newName.trim();
    if (!name) { showToast("Имя не может быть пустым", true); return; }
    try {
        await api("/api/me", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name }),
        });
        showToast("✅ Имя обновлено");
        await loadMe();
        renderProfile();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// Удаление аккаунта (п.1.1.10 Правил VK) — self-service, анонимизация данных.
// После удаления пользователь может вернуться — создастся чистый аккаунт.
async function deleteAccount() {
    const ok = await tgConfirm(
        "Удалить аккаунт и данные? Это действие необратимо: профиль, имя и подписка будут удалены. Купленные билеты останутся действительными для входа.",
        "Удалить",
        "Отмена"
    );
    if (!ok) return;
    showLoading();
    try {
        await api("/api/me", { method: "DELETE" });
        hideLoading();
        // Очистить локальное состояние и показать «удалено»
        state.me = null;
        state.role = "user";
        showToast("✅ Аккаунт удалён");
        showHome();
    } catch (e) {
        hideLoading();
        showToast(e.message || "Ошибка удаления", true);
    }
}

// ─── API helper ─────────────────────────────────────────────────

async function api(path, options = {}) {
    // VK Mini App — launch params в X-VK-Init-Data; Telegram — X-Init-Data.
    const authHeader = state.platform === "vk" ? "X-VK-Init-Data" : "X-Init-Data";
    const headers = {
        [authHeader]: state.initData,
        ...options.headers,
    };

    // If X-Skip-Auth header works in dev, allow it
    if (!state.initData && window.location.hostname === "localhost") {
        headers["X-Skip-Auth"] = "1";
    }

    let resp;
    try {
        resp = await fetch(path, {
            ...options,
            headers,
        });
    } catch (e) {
        // Сетевая ошибка (нет интернета / сервер недоступен) — понятное сообщение
        if (!navigator.onLine) {
            throw new Error("Нет соединения с интернетом. Проверьте подключение и попробуйте снова.");
        }
        throw new Error("Сервер временно недоступен. Попробуйте позже.");
    }

    if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
            const err = await resp.json();
            detail = err.detail || detail;
        } catch {}
        throw new Error(detail);
    }

    return resp.json();
}

// ─── Navigation ─────────────────────────────────────────────────

function showPage(pageId) {
    document.querySelectorAll(".page").forEach(p => { p.classList.remove("active"); p.classList.remove("page-enter"); });
    const page = document.getElementById(`page-${pageId}`);
    if (page) {
        page.classList.add("active");
        // Лёгкий fade-переход между страницами (минимализм)
        page.classList.add("page-enter");
    }
    document.getElementById("loadingOverlay").classList.remove("active");
}

function updateToolbar(title, showBack = false, showTickets = true) {
    updateHeader(title);
    // Legacy toolbar elements — скрыты в новом дизайне
    const tb = document.getElementById("toolbarTitle");
    if (tb) tb.textContent = title;
}

// Back stack for simple navigation
const navStack = [];

function pushNav(page, data) {
    navStack.push({ page, data });
}

function goBack() {
    if (navStack.length > 0) {
        const prev = navStack.pop();
        if (prev.page === "events") showEvents();
        else if (prev.page === "event") showEventDetail(prev.data);
        else if (prev.page === "tickets") showMyTickets();
        else showEvents();
    } else {
        showEvents();
    }
}

// ─── Toast ──────────────────────────────────────────────────────

function showToast(message, isError = false) {
    const toast = document.getElementById("toast");
    toast.textContent = message;
    toast.className = "toast active" + (isError ? " toast-error" : "");
    setTimeout(() => toast.classList.remove("active"), 3000);
}

// ─── Loading ────────────────────────────────────────────────────

function showLoading() {
    document.getElementById("loadingOverlay").classList.add("active");
}

function hideLoading() {
    document.getElementById("loadingOverlay").classList.remove("active");
}

// ═══════════════════════════════════════════════════════════════
// PAGE: Events List
// ═══════════════════════════════════════════════════════════════

async function showEvents() {
    state.lastAction = "showEvents";
    setActiveTab("events");
    updateToolbar("Мероприятия", false, false);
    // Матрица ролей: только организатор видит свои мероприятия; user и суперадмин — публичную ленту
    if (state.role === "organizer") {
        await showAdminEvents();
        return;
    }
    showPage("events");
    showLoading();

    try {
        const events = await api("/api/events");
        state.events = events;
        renderEvents(events);
    } catch (err) {
        hideLoading();
        showEmpty("eventsContent", "😔 Нет предстоящих мероприятий");
    }
}

function renderEvents(events) {
    hideLoading();
    const container = document.getElementById("eventsContent");

    if (!events || events.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🎫</div>
                <h3>Нет предстоящих мероприятий</h3>
                ${state.role === "organizer" ? '<button class="btn btn-primary" onclick="showAdminEventForm()">+ Создать мероприятие</button>' : ''}
            </div>
        `;
        return;
    }

    let html = state.role === "organizer" ? '<button class="btn btn-primary" onclick="showAdminEventForm()">+ Создать мероприятие</button>' : '';
    html += '<div class="events-list" style="margin-top:12px">';
    for (const e of events) {
        const dateStr = formatDate(e.date);
        const soldOut = e.available_tickets <= 0;
        const poster = e.media_file_id ? `<img class="event-poster" src="/api/events/${e.id}/media" alt="" loading="lazy">` : '';
        html += `
            <div class="event-card" onclick="showEventDetail('${e.id}')">
                ${poster}
                <div class="event-card-header">
                    <h3>${escapeHtml(e.title)}</h3>
                    ${soldOut ? '<span class="badge badge-soldout">Sold out</span>' : ''}
                </div>
                <div class="event-card-details">
                    <span class="event-info">📅 ${dateStr}</span>
                    ${e.location ? `<span class="event-info">📍 ${escapeHtml(e.location)}</span>` : ''}
                    <span class="event-info">💰 ${formatPrice(e.price)}</span>
                    <span class="event-info">🎟 ${e.available_tickets}/${e.total_tickets}</span>
                </div>
                ${!soldOut ? `<button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); showEventDetail('${e.id}')">🎟 Купить</button>` : ''}
            </div>
        `;
    }
    html += "</div>";
    container.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════════
// PAGE: Event Detail
// ═══════════════════════════════════════════════════════════════

async function showEventDetail(eventId) {
    state.lastAction = "showEventDetail";
    pushNav("events");
    showLoading();

    try {
        const event = await api(`/api/events/${eventId}`);
        state.currentEvent = event;
        renderEvent(event);
    } catch (err) {
        hideLoading();
        showError(err.message || "Мероприятие не найдено");
    }
}

function renderEvent(event) {
    hideLoading();
    updateToolbar(event.title, true, true);
    showPage("event");

    const container = document.getElementById("eventContent");
    const dateStr = formatDate(event.date);
    const soldOut = event.available_tickets <= 0;
    const passed = new Date(event.date) < new Date();

    let buyButton = "";
    if (passed) {
        buyButton = '<button class="btn btn-disabled" disabled>⏰ Мероприятие прошло</button>';
    } else if (soldOut) {
        buyButton = '<button class="btn btn-disabled" disabled>❌ Билеты закончились</button>';
    } else if (!event.is_active) {
        buyButton = '<button class="btn btn-disabled" disabled>🔴 Мероприятие отменено</button>';
    } else {
        buyButton = `<button class="btn btn-primary btn-lg" onclick="showConfirm('${event.id}')">🎟 Купить билет — ${formatPrice(event.price)}</button>`;
    }

    const poster = event.media_file_id ? `<img class="event-poster event-poster-detail" src="/api/events/${event.id}/media" alt="">` : '';

    container.innerHTML = `
        <div class="event-detail">
            ${poster}
            <h2>${escapeHtml(event.title)}</h2>
            ${event.description ? `<p class="event-description">${escapeHtml(event.description)}</p>` : ''}
            <div class="event-meta">
                <div class="meta-row"><span class="meta-label">📅 Дата</span><span>${dateStr}</span></div>
                <div class="meta-row"><span class="meta-label">📍 Место</span><span>${event.location || 'Не указано'}</span></div>
                <div class="meta-row"><span class="meta-label">💰 Цена</span><span>${formatPrice(event.price)}</span></div>
                <div class="meta-row"><span class="meta-label">🎟 Билетов</span><span>${event.available_tickets} из ${event.total_tickets}</span></div>
                <div class="meta-row"><span class="meta-label">🔞 Возраст</span><span>${escapeHtml(event.age_restriction || '0+')}</span></div>
            </div>
            <div class="buy-section">
                ${buyButton}
            </div>
        </div>
    `;
}

// ═══════════════════════════════════════════════════════════════
// PAGE: Confirm Purchase
// ═══════════════════════════════════════════════════════════════

function showConfirm(eventId) {
    const event = state.currentEvent;
    if (!event || event.id !== eventId) return;

    updateToolbar("Подтверждение", true, false);
    showPage("confirm");

    const container = document.getElementById("confirmContent");
    container.innerHTML = `
        <div class="confirm-card">
            <h3>Подтверждение покупки</h3>
            <div class="confirm-details">
                <div class="confirm-row">
                    <span>Мероприятие</span>
                    <span><b>${escapeHtml(event.title)}</b></span>
                </div>
                <div class="confirm-row">
                    <span>Дата</span>
                    <span>${formatDate(event.date)}</span>
                </div>
                <div class="confirm-row">
                    <span>Цена</span>
                    <span><b>${formatPrice(event.price)}</b></span>
                </div>
                <div class="confirm-row">
                    <span>Промокод</span>
                    <input class="form-input" id="promoInput_${eventId}" placeholder="Например, SUMMER10" autocomplete="off" style="text-align:right;max-width:180px;text-transform:uppercase">
                </div>
            </div>
            <button class="btn btn-primary btn-lg" onclick="confirmBuy('${eventId}')" id="confirmBtn">
                ✅ Подтвердить покупку
            </button>
            <button class="btn btn-secondary" onclick="showEventDetail('${eventId}')">
                ← Отмена
            </button>
        </div>
    `;
}

async function confirmBuy(eventId) {
    const btn = document.getElementById("confirmBtn");
    btn.disabled = true;
    btn.textContent = "⏳ Оформление...";

    try {
        const promoInput = document.getElementById(`promoInput_${eventId}`);
        const promo = promoInput ? promoInput.value.trim() : "";
        const result = await api(`/api/events/${eventId}/buy`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(promo ? { promo_code: promo } : {}),
        });

        // Haptic feedback
        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.notificationOccurred("success");
        }

        // VK: мягкий запрос на отправку билета в ЛС (best-effort, не блокирует успех).
        if (state.platform === "vk") {
            await offerVkTicketDm(eventId, result);
        }

        showSuccess(result);
    } catch (err) {
        btn.disabled = false;
        btn.textContent = "✅ Подтвердить покупку";
        showError(err.message || "Ошибка при покупке");
    }
}

// VK: после покупки предложить отправить билет в ЛС VK от группы организатора.
// Порядок: мягкий запрос → VKWebAppAllowMessagesFromGroup (диалог VK) →
// POST /tickets/{id}/send-vk (бэкенд шлёт messages.send). Отказ/неудача — тихо.
async function offerVkTicketDm(eventId, result) {
    try {
        const bridge = window.vkBridge;
        // Без группы организатора бэкенд не вернул vk_group_id — билет в кабинете.
        const vkGroupId = result && result.vk_group_id;
        if (!bridge || !vkGroupId) return;

        const want = await tgConfirm(
            "Получить билет в личные сообщения ВКонтакте?\n\n" +
            "Если откажетесь — билет всегда будет доступен в разделе «Мои билеты».",
            "Да, отправить",
            "Нет, смотреть в приложении",
        );
        if (!want) return;

        // Разрешить сообщения от группы (системный диалог VK).
        try {
            await bridge.send("VKWebAppAllowMessagesFromGroup", { group_id: Number(vkGroupId) });
        } catch (e) {
            console.warn("VK allow messages not granted", e);
            return;  // без разрешения messages.send не дойдёт
        }

        // Отправить билет в ЛС (бэкенд: messages.send от группы).
        try {
            const res = await api(`/api/tickets/${result.ticket_id}/send-vk`, { method: "POST" });
            if (res.sent) {
                tgAlert("🎫 Билет отправлен в личные сообщения ВКонтакте!");
            } else {
                showToast("Билет сохранён в «Моих билетах»", false);
            }
        } catch (e) {
            console.warn("send-vk failed", e);
        }
    } catch (e) {
        console.warn("offerVkTicketDm failed", e);
    }
}

// ═══════════════════════════════════════════════════════════════
// PAGE: Success
// ═══════════════════════════════════════════════════════════════

function showSuccess(result) {
    updateToolbar("Успешно!", false, true);
    showPage("success");
    document.getElementById("successTicketId").textContent = result.ticket_id || "—";
    // Скидка по промокоду (если была)
    const hint = document.getElementById("successHint");
    if (hint && result.discount_amount > 0) {
        hint.innerHTML = `🎟 Промокод ${escapeHtml(result.promo_code || "")} — скидка ${formatPrice(result.discount_amount)}. Итого: <b>${formatPrice(result.amount)}</b>`;
    }
}

function copyTicketId() {
    const id = document.getElementById("successTicketId").textContent;
    if (!id || id === "—") return;

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(id);
    }
    showToast("✅ Номер скопирован");
}

// ═══════════════════════════════════════════════════════════════
// PAGE: My Tickets
// ═══════════════════════════════════════════════════════════════

async function showMyTickets() {
    state.lastAction = "showMyTickets";
    setActiveTab("tickets");
    pushNav("tickets");
    updateToolbar("Мои билеты", true, false);
    showPage("tickets");
    showLoading();

    try {
        const tickets = await api("/api/tickets");
        state.tickets = tickets;
        renderTickets(tickets);
    } catch (err) {
        hideLoading();
        showError(err.message || "Ошибка загрузки билетов");
    }
}

function renderTickets(tickets) {
    hideLoading();
    const container = document.getElementById("ticketsContent");

    if (!tickets || tickets.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🎟</div>
                <h3>У вас нет билетов</h3>
                <button class="btn btn-primary" onclick="showEvents()">📋 К мероприятиям</button>
            </div>
        `;
        return;
    }

    let html = '<div class="tickets-list">';
    for (const t of tickets) {
        const dateStr = formatDate(t.purchase_date);
        const isActive = t.status === "active";
        const statusEmoji = isActive ? "✅" : "❌";
        const statusText = isActive ? "Активен" : "Возвращён";
        // A: бесплатный билет → код, платный → QR (разное предъявление).
        const isFree = !!t.is_free;
        const entryCode = t.validation_code || t.id;

        html += `
            <div class="ticket-card ${isActive ? '' : 'ticket-cancelled'}">
                <div class="ticket-header">
                    <span class="ticket-event">${escapeHtml(t.event_title)}</span>
                    <span class="ticket-status">${statusEmoji} ${statusText}</span>
                </div>
                <div class="ticket-meta">
                    ${isFree
                        ? `<span>🎟 <b>Код для входа:</b> <code>${escapeHtml(entryCode)}</code></span>`
                        : `<span>🔒 <b>Платный билет</b> — предъявите QR на входе</span>`}
                    <span>📅 Куплен: ${dateStr}</span>
                    <span>🔞 Возраст: ${escapeHtml(t.age_restriction || '0+')}</span>
                </div>
                ${isActive ? `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
                    ${isFree ? '' : `<button class="btn btn-primary btn-sm" onclick="showBuyerTicketQr('${t.id}')">📱 Показать QR</button>`}
                    <button class="btn btn-danger btn-sm" onclick="cancelTicket('${t.id}')">↩️ Отменить</button>
                </div>` : ''}
            </div>
        `;
    }
    html += "</div>";
    container.innerHTML = html;
}

// Показать QR своего билета (владелец, без подписки). Fallback: показ кода.
function authHeaders() {
    // Авторизация: VK — launch params, Telegram — initData (то же, что в api()).
    const header = state.platform === "vk" ? "X-VK-Init-Data" : "X-Init-Data";
    return { [header]: state.initData };
}

async function showBuyerTicketQr(ticketId) {
    try {
        const resp = await fetch(`/api/tickets/${ticketId}/qr`, { headers: authHeaders() });
        if (!resp.ok) { showToast("Ошибка загрузки QR", true); return; }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const overlay = document.createElement("div");
        overlay.className = "qr-modal";
        overlay.innerHTML = `
            <div class="qr-modal-card">
                <img src="${url}" alt="QR" style="width:220px;height:220px;border-radius:8px">
                <p class="hint" style="margin:8px 0 0">Покажите этот QR на входе организатору</p>
                <div style="display:flex;gap:8px;margin-top:12px">
                    <button class="btn btn-sm btn-primary" onclick="this.closest('.qr-modal').remove(); downloadBuyerQr('${ticketId}')">⬇️ Скачать</button>
                    <button class="btn btn-sm btn-secondary" onclick="this.closest('.qr-modal').remove()">Закрыть</button>
                </div>
            </div>`;
        overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
        document.body.appendChild(overlay);
    } catch (e) { showToast(e.message || "Ошибка QR", true); }
}

async function downloadBuyerQr(ticketId) {
    try {
        const resp = await fetch(`/api/tickets/${ticketId}/qr`, { headers: authHeaders() });
        if (!resp.ok) { showToast("Ошибка", true); return; }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `ticket-${ticketId}-qr.png`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function cancelTicket(ticketId) {
    if (!(await tgConfirm("Вы уверены, что хотите отменить билет?"))) return;

    try {
        const result = await api(`/api/tickets/${ticketId}/cancel`, { method: "POST" });
        showToast("✅ Билет возвращён");
        await showMyTickets(); // Refresh
    } catch (err) {
        showError(err.message || "Ошибка при отмене");
    }
}

// ═══════════════════════════════════════════════════════════════
// PAGE: Error
// ═══════════════════════════════════════════════════════════════

function showError(message) {
    document.getElementById("errorMessage").textContent = message || "Произошла неизвестная ошибка";
    updateToolbar("Ошибка", true, false);
    showPage("error");

    // Haptic error feedback
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
        window.Telegram.WebApp.HapticFeedback.notificationOccurred("error");
    }
}

function retryLastAction() {
    const action = state.lastAction;
    if (action && typeof window[action] === "function") {
        window[action]();
    } else {
        showEvents();
    }
}

// ═══════════════════════════════════════════════════════════════
// Utilities
// ═══════════════════════════════════════════════════════════════

function formatDate(isoStr) {
    if (!isoStr) return "—";
    const d = new Date(isoStr);
    const day = String(d.getDate()).padStart(2, "0");
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const year = d.getFullYear();
    const hours = String(d.getHours()).padStart(2, "0");
    const mins = String(d.getMinutes()).padStart(2, "0");
    return `${day}.${month}.${year} ${hours}:${mins}`;
}

function formatPrice(price) {
    if (price == null) return "—";
    return `${Math.round(Number(price))} ₽`;
}

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function showEmpty(containerId, message) {
    const container = document.getElementById(containerId);
    container.innerHTML = `
        <div class="empty-state">
            <p>${escapeHtml(message)}</p>
        </div>
    `;
}

// ═══════════════════════════════════════════════════════════════
// Админка
// ═══════════════════════════════════════════════════════════════

function showAdminDashboard() {
    // Global super-admin tools are available only in the Telegram contour.
    if (isVKMode() || state.role !== "super_admin") return;
    setActiveTab("admin");
    updateToolbar("Панель", false, false);
    showPage("admin");
    document.getElementById("adminContent").innerHTML = `
        <h2 style="margin-bottom:16px">Панель супер-админа</h2>
        <div class="admin-menu-grid">
            <button class="admin-menu-card" onclick="showCheckin()">
                <div class="admin-menu-icon">🔍</div>
                <div>Поиск по коду / QR</div>
            </button>
            <button class="admin-menu-card" onclick="showAdminStats()">
                <div class="admin-menu-icon">📊</div>
                <div>Статистика</div>
            </button>
            <button class="admin-menu-card" onclick="showBroadcast()">
                <div class="admin-menu-icon">📣</div>
                <div>Рассылка</div>
            </button>
            <button class="admin-menu-card" onclick="showUserInfo()">
                <div class="admin-menu-icon">👥</div>
                <div>Подписки организаторов</div>
            </button>
            <button class="admin-menu-card" onclick="showAdminHealth()">
                <div class="admin-menu-icon">🩺</div>
                <div>Здоровье</div>
            </button>
        </div>
    `;
}

// ─── Мероприятия (список + создание) ───────────────────────────

async function showAdminEvents() {
    setActiveTab("admin");
    state.lastAction = "showAdminEvents";
    updateToolbar("Мероприятия", true, false);
    showPage("admin-events");
    showLoading();
    try {
        const events = await api("/api/admin/events");
        state.adminEvents = events;
        renderAdminEvents(events);
    } catch (err) {
        hideLoading();
        showError(err.message || "Ошибка загрузки мероприятий");
    }
}

function renderAdminEvents(events) {
    hideLoading();
    const container = document.getElementById("adminEventsContent");
    if (!events || events.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🎫</div>
                <h3>Нет мероприятий</h3>
                <button class="btn btn-primary" onclick="showAdminEventForm()">+ Создать мероприятие</button>
            </div>
        `;
        return;
    }
    container.innerHTML = `
        <button class="btn btn-primary" onclick="showAdminEventForm()">+ Создать мероприятие</button>
        <div class="admin-list" style="margin-top:12px">
            ${events.map(e => `
                <div class="admin-list-item">
                    <div style="flex:1;cursor:pointer" onclick="showAdminEventDetail('${e.id}')">
                        <div><b>${escapeHtml(e.title)}</b>
                            ${e.is_published ? '<span class="badge badge-published">опубл.</span>' : '<span class="badge badge-draft">черновик</span>'}
                            ${e.is_active ? '' : '<span class="badge badge-off">выкл</span>'}
                        </div>
                        <div class="hint">${formatDate(e.date)}${e.channel_title ? ' · ' + escapeHtml(e.channel_title) : ''} · ${e.price > 0 ? formatPrice(e.price) : 'Бесплатно'} · ${e.available_tickets}/${e.total_tickets}</div>
                    </div>
                    <button class="btn btn-sm btn-secondary" onclick="showAdminEventDetail('${e.id}')">Открыть</button>
                </div>`).join('')}
        </div>
    `;
}

async function showAdminEventForm(eventId) {
    updateToolbar(eventId ? "Редактировать" : "Создать мероприятие", true, false);
    showPage("admin-event-form");
    const container = document.getElementById("adminEventFormContent");

    let event = null;
    if (eventId) {
        try { event = await api(`/api/admin/events/${eventId}`); }
        catch (e) { showError(e.message || "Ошибка загрузки"); return; }
    }

    const me = state.me || {};
    const myChannels = me.channels || [];
    // C: можно платное/инвайты, если pro-подписка ИЛИ у события куплен премиум.
    const canPaid = isPro() || !!(event && event.is_premium);
    const options = myChannels.map(c => {
        const hasSub = c.is_subscription_active;
        return `<option value="${c.id}">${escapeHtml(c.title || c.telegram_channel_id)}${hasSub ? '' : ' (нет подписки)'}</option>`;
    }).join('');

    const dateVal = event ? toLocalInputValue(event.date) : "";

    container.innerHTML = `
        <form onsubmit="event.preventDefault(); submitAdminEventForm('${eventId || ''}')">
            <div class="form-field">
                <label class="form-label">Название *</label>
                <input class="form-input" id="f_title" required value="${escapeHtml(event ? event.title : '')}">
            </div>
            <div class="form-field">
                <label class="form-label">Описание</label>
                <textarea class="form-input" id="f_description">${escapeHtml(event ? (event.description || '') : '')}</textarea>
            </div>
            <div class="form-field">
                <label class="form-label">Дата и время *</label>
                <input class="form-input" type="datetime-local" id="f_date" required value="${dateVal}">
            </div>
            <div class="form-field">
                <label class="form-label">Место</label>
                <input class="form-input" id="f_location" value="${escapeHtml(event ? (event.location || '') : '')}">
            </div>
            <div class="form-field">
                <label class="form-label">Возрастное ограничение (ФЗ-436)</label>
                <select class="form-input" id="f_age_restriction">
                    ${["0+", "6+", "12+", "16+", "18+"].map(v =>
                        `<option value="${v}" ${(event ? (event.age_restriction || '0+') : '0+') === v ? 'selected' : ''}>${v}</option>`
                    ).join('')}
                </select>
                <div class="hint" style="margin:4px 0 0">Организатор отвечает за корректность маркировки и допуск лиц до 18 лет</div>
            </div>
            <div class="form-field">
                <label class="form-label">Цена (₽, 0 = бесплатно)${canPaid ? '' : ' — только бесплатные на вашем тарифе'}</label>
                <input class="form-input" type="number" min="0" step="0.01" id="f_price" value="${event ? event.price : 0}" ${canPaid ? '' : 'disabled'}>
            </div>
            ${canPaid ? `
            <div class="form-field">
                <label class="form-label">Цены по дате (Pro)</label>
                <div id="priceRangesList_${event ? event.id : 'new'}"></div>
                <button type="button" class="btn btn-sm btn-secondary" onclick="addPriceRangeRow('${event ? event.id : 'new'}')">+ Добавить диапазон</button>
                <div class="hint" style="margin:4px 0 0">Диапазоны покрывают весь период от публикации до даты мероприятия. Цена фиксируется при покупке.</div>
            </div>` : ''}
            <div class="form-field">
                <label class="form-label">Количество билетов *</label>
                <input class="form-input" type="number" min="1" step="1" id="f_tickets" required value="${event ? event.total_tickets : 100}">
            </div>
            ${canPaid ? `
            <div class="form-field">
                <label class="form-label">Пригласительных (лимит, Pro)</label>
                <input class="form-input" type="number" min="0" step="1" id="f_invites" value="${event ? (event.invites_quota || 0) : 0}">
                <div class="hint" style="margin:4px 0 0">Сколько пригласительных можно выдать из непроданных мест</div>
            </div>` : ''}
            ${!event ? `
            <div class="form-field">
                <label class="form-label">Канал (необязательно, если нет — через Mini App)</label>
                <select class="form-input" id="f_channel">
                    <option value="">Без канала (Mini App)</option>
                    ${options}
                </select>
            </div>` : ''}
            <button class="btn btn-primary" type="submit">${eventId ? "💾 Сохранить" : "✅ Создать (черновик)"}</button>
            <button class="btn btn-secondary" type="button" onclick="showAdminEvents()">Отмена</button>
        </form>
    `;
    // Предзаполнение диапазонов при редактировании
    if (eventId && canPaid) {
        try {
            const pr = (await api(`/api/admin/events/${eventId}/price-ranges`)).price_ranges || [];
            const scope = `priceRangesList_${eventId}`;
            const list = document.getElementById(scope);
            if (list) {
                pr.forEach(r => {
                    const row = document.createElement("div");
                    row.className = "price-range-row";
                    row.style.cssText = "display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap";
                    row.innerHTML = `
                        <input class="form-input pr_start" type="datetime-local" style="width:150px" value="${toLocalInputValue(r.starts_at)}" title="С">
                        <input class="form-input pr_end" type="datetime-local" style="width:150px" value="${toLocalInputValue(r.ends_at)}" title="По">
                        <input class="form-input pr_price" type="number" min="0" step="0.01" placeholder="Цена ₽" style="width:80px" value="${r.price}">
                        <button type="button" class="btn btn-sm btn-secondary" onclick="adminDeletePriceRange(this)">✕</button>`;
                    list.appendChild(row);
                });
            }
        } catch (e) { /* нет доступа к диапазонам — игнор */ }
    }
}

function toLocalInputValue(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const pad = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function submitAdminEventForm(eventId) {
    const title = document.getElementById("f_title").value.trim();
    const description = document.getElementById("f_description").value.trim() || null;
    const dateStr = document.getElementById("f_date").value;
    const location = document.getElementById("f_location").value.trim() || null;
    const price = parseFloat(document.getElementById("f_price").value) || 0;
    const total_tickets = parseInt(document.getElementById("f_tickets").value, 10) || 0;

    if (!title || !dateStr) { showToast("Заполните название и дату", true); return; }
    if (total_tickets <= 0) { showToast("Билетов должно быть > 0", true); return; }

    const invitesQuotaEl = document.getElementById("f_invites");
    const invites_quota = invitesQuotaEl ? (parseInt(invitesQuotaEl.value, 10) || 0) : undefined;

    const ageRestrictionEl = document.getElementById("f_age_restriction");
    const age_restriction = ageRestrictionEl ? ageRestrictionEl.value : undefined;

    const payload = {
        title, description,
        date: new Date(dateStr).toISOString(),
        location, price, total_tickets,
    };
    if (invites_quota !== undefined) payload.invites_quota = invites_quota;
    if (age_restriction !== undefined) payload.age_restriction = age_restriction;
    if (!eventId) {
        const channelId = document.getElementById("f_channel").value;
        payload.channel_id = channelId || null;
        if (!channelId && state.me) payload.owner_user_id = state.me.id;
    }

    try {
        let savedId = eventId;
        if (eventId) {
            await api(`/api/admin/events/${eventId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
            showToast("✅ Мероприятие обновлено");
        } else {
            const created = await api("/api/admin/events", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
            savedId = created.id;
            showToast("✅ Мероприятие создано (черновик)");
        }
        // Сохранить динамические цены, если заполнены диапазоны
        if (price > 0 && savedId) {
            // Для нового события диапазоны лежали в priceRangesList_new
            const ranges = collectPriceRanges(eventId || "new");
            if (ranges.length > 0) {
                await api(`/api/admin/events/${savedId}/price-ranges`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ranges }),
                });
            }
        }
        await showAdminEvents();
    } catch (err) {
        showToast(err.message || "Ошибка сохранения", true);
    }
}

// ─── Мероприятие (детали + статистика + билеты) ─────────────────

async function showAdminEventDetail(eventId) {
    state.lastAction = "showAdminEvents";
    updateToolbar("Мероприятие", true, false);
    showPage("admin-event");
    showLoading();
    try {
        const event = await api(`/api/admin/events/${eventId}`);
        state.currentAdminEvent = event;
        let stats = null, tickets = null, invites = [], promos = [], priceRanges = [];
        try { stats = await api(`/api/admin/events/${eventId}/stats`); } catch (e) { /* нет доступа */ }
        try { tickets = await api(`/api/admin/events/${eventId}/tickets`); } catch (e) { /* нет доступа */ }
        try { invites = (await api(`/api/admin/events/${eventId}/invites`)).invites || []; } catch (e) { /* нет доступа */ }
        try { promos = (await api(`/api/admin/events/${eventId}/promo-codes`)).promo_codes || []; } catch (e) { /* нет доступа */ }
        try { priceRanges = (await api(`/api/admin/events/${eventId}/price-ranges`)).price_ranges || []; } catch (e) { /* нет доступа */ }
        renderAdminEventDetail(event, stats, tickets ? tickets.tickets : [], invites, promos, priceRanges);
    } catch (err) {
        hideLoading();
        showError(err.message || "Ошибка загрузки");
    }
}

function renderAdminEventDetail(event, stats, tickets, invites, promos, priceRanges) {
    hideLoading();
    const container = document.getElementById("adminEventContent");
    invites = invites || [];
    promos = promos || [];
    priceRanges = priceRanges || [];

    const statsHtml = stats ? `
        <div class="stat-grid">
            <div class="stat-card"><div class="stat-value">${stats.sold}</div><div class="stat-label">Продано</div></div>
            <div class="stat-card"><div class="stat-value">${stats.available}</div><div class="stat-label">Свободно</div></div>
            <div class="stat-card"><div class="stat-value">${stats.invites_issued != null ? stats.invites_issued + '/' + stats.invites_quota : '—'}</div><div class="stat-label">Пригласит. выдано</div></div>
            <div class="stat-card"><div class="stat-value">${stats.invites_used != null ? stats.invites_used : '—'}</div><div class="stat-label">Пригласит. использовано</div></div>
            <div class="stat-card"><div class="stat-value">${formatPrice(stats.revenue)}</div><div class="stat-label">Выручка</div></div>
        </div>` : '<p class="hint">Статистика недоступна</p>';

    const ticketsHtml = tickets.length === 0
        ? '<p class="hint">Билетов пока нет</p>'
        : `<div class="admin-list">${tickets.map(t => `
            <div class="admin-list-item">
                <div style="flex:1">
                    <div><b>${escapeHtml(t.user_name)}</b> <code>${t.validation_code || '—'}</code>
                        ${t.is_invite ? '<span class="badge badge-tier-pro">приглас.</span>' : ''}</div>
                    <div class="hint">${t.status}${t.checked_in_at ? ' · вход ' + formatDate(t.checked_in_at) : ''} · ${formatDate(t.purchase_date)}</div>
                </div>
                <button class="btn btn-sm btn-secondary" onclick="showTicketQr('${t.id}')">QR</button>
                ${t.status === 'active' && !t.is_invite ? `<button class="btn btn-sm btn-danger" onclick="adminCancelTicket('${t.id}')">Отменить</button>` : ''}
            </div>`).join('')}</div>`;

    // Блок пригласительных
    const invitesHtml = `
        <h3 style="margin:16px 0 8px">Пригласительные ${stats && stats.invites_quota != null ? `(лимит ${stats.invites_quota})` : ''}</h3>
        ${isPro() ? `<button class="btn btn-sm btn-primary" onclick="adminIssueInvitePrompt('${event.id}')">🎟 Выдать пригласительное</button>` : '<p class="hint">Пригласительные доступны на подписке Pro</p>'}
        ${invites.length === 0
            ? '<p class="hint">Пригласительных пока нет</p>'
            : `<div class="admin-list" style="margin-top:10px">${invites.map(iv => `
                <div class="admin-list-item">
                    <div style="flex:1">
                        <div><code>${iv.validation_code || '—'}</code>
                            <span class="badge badge-tier-pro">${iv.seats} чел.</span>
                            ${iv.status === 'checked_in' ? '<span class="badge badge-published">использован</span>' : ''}
                            ${iv.status === 'refunded' ? '<span class="badge badge-off">возвращён</span>' : ''}
                        </div>
                        <div class="hint">${formatDate(iv.purchase_date)}${iv.invited_by ? ' · выдал ' + escapeHtml(iv.invited_by) : ''}</div>
                    </div>
                    <button class="btn btn-sm btn-secondary" onclick="showTicketQr('${iv.id}')">QR</button>
                    ${iv.status === 'active' ? `<button class="btn btn-sm btn-secondary" onclick="copyInviteLink('${escapeHtml(iv.validation_code || '')}')">🔗</button>` : ''}
                    ${iv.status === 'active' ? `<button class="btn btn-sm btn-danger" onclick="adminCancelInvite('${event.id}','${iv.id}')">Отменить</button>` : ''}
                </div>`).join('')}</div>`}
    `;

    // Блок промокодов (pro-фича)
    const canPromo = isPro() || !!(event && event.is_premium);
    const promoFormHtml = canPromo ? `
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin:10px 0" id="promoForm_${event.id}">
            <input class="form-input" id="promo_code_${event.id}" placeholder="Код" style="width:110px;text-transform:uppercase">
            <select class="form-input" id="promo_type_${event.id}" style="width:110px">
                <option value="percent">% скидка</option>
                <option value="fixed">сумма ₽</option>
            </select>
            <input class="form-input" id="promo_value_${event.id}" placeholder="Скидка" type="number" min="1" style="width:90px">
            <input class="form-input" id="promo_start_${event.id}" type="datetime-local" style="width:160px" title="С (необязательно)">
            <input class="form-input" id="promo_end_${event.id}" type="datetime-local" style="width:160px" title="По (необязательно)">
            <input class="form-input" id="promo_limit_${event.id}" placeholder="Лимит (0=∞)" type="number" min="0" value="0" style="width:90px">
            <button class="btn btn-sm btn-primary" onclick="adminCreatePromo('${event.id}')">+ Создать</button>
        </div>` : '<p class="hint">Промокоды доступны на подписке Pro или премиуме события</p>';

    const promosHtml = `
        <h3 style="margin:16px 0 8px">Промокоды</h3>
        ${promoFormHtml}
        ${promos.length === 0
            ? '<p class="hint">Промокодов пока нет</p>'
            : `<div class="admin-list" style="margin-top:10px">${promos.map(p => `
                <div class="admin-list-item">
                    <div style="flex:1">
                        <div><code>${escapeHtml(p.code)}</code>
                            <span class="badge badge-tier-pro">${p.discount_type === 'percent' ? p.discount_value + '%' : formatPrice(p.discount_value)}</span>
                            ${p.is_active ? '<span class="badge badge-published">вкл</span>' : '<span class="badge badge-off">выкл</span>'}
                        </div>
                        <div class="hint">${p.starts_at || p.ends_at
                            ? (p.starts_at ? 'с ' + formatDate(p.starts_at) : '') + (p.ends_at ? ' по ' + formatDate(p.ends_at) : '')
                            : 'бессрочно'} · использовано ${p.used_count}/${p.max_uses || '∞'}</div>
                    </div>
                    <button class="btn btn-sm btn-secondary" onclick="adminTogglePromo('${event.id}','${p.id}')">${p.is_active ? 'Выключить' : 'Включить'}</button>
                </div>`).join('')}</div>`}
    `;

    // Секция «Цены по дате» (динамические цены, pro)
    const priceRangesHtml = `
        <h3 style="margin:16px 0 8px">Цены по дате</h3>
        <button class="btn btn-sm btn-secondary" onclick="showAdminEventForm('${event.id}')">✏️ Изменить</button>
        ${priceRanges.length === 0
            ? '<p class="hint">Динамические цены не настроены — действует базовая цена</p>'
            : `<div class="admin-list" style="margin-top:10px">${priceRanges.map(r => `
                <div class="admin-list-item">
                    <div style="flex:1">
                        <div><b>${formatPrice(r.price)}</b></div>
                        <div class="hint">с ${formatDate(r.starts_at)} по ${formatDate(r.ends_at)}</div>
                    </div>
                </div>`).join('')}</div>`}
    `;

    container.innerHTML = `
        <h2>${escapeHtml(event.title)}</h2>
        <div class="event-meta">
            <div class="meta-row"><span class="meta-label">📅 Дата</span><span>${formatDate(event.date)}</span></div>
            <div class="meta-row"><span class="meta-label">📍 Место</span><span>${escapeHtml(event.location || 'Не указано')}</span></div>
            <div class="meta-row"><span class="meta-label">💰 Цена</span><span>${event.price > 0 ? formatPrice(event.price) : 'Бесплатно'}</span></div>
            <div class="meta-row"><span class="meta-label">🎟 Билетов</span><span>${event.available_tickets}/${event.total_tickets}</span></div>
            <div class="meta-row"><span class="meta-label">Статус</span><span>${event.is_published ? '📢 Опубликовано' : '📝 Черновик'} · ${event.is_active ? '🟢 Активно' : '🔴 Отключено'}</span></div>
        </div>
        ${statsHtml}
        <h3 style="margin:16px 0 8px">Билеты</h3>
        <div style="margin-bottom:12px">
            <a class="btn btn-secondary" href="#" onclick="downloadCsv('${event.id}'); return false;">⬇️ Экспорт CSV</a>
        </div>
        ${ticketsHtml}
        ${invitesHtml}
        ${promosHtml}
        ${priceRangesHtml}
        <h3 style="margin:16px 0 8px">Действия</h3>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:24px">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                <select class="form-input" id="publish_channel_${event.id}" style="width:auto;min-width:180px">
                    <option value="">Без канала (DM)</option>
                    ${(state.me?.channels || []).map(c =>
                        `<option value="${c.id}">${escapeHtml(c.title || c.telegram_channel_id)}</option>`
                    ).join('')}
                </select>
                <button class="btn btn-sm btn-primary" onclick="adminPublish('${event.id}')">📢 Опубликовать</button>
            </div>
            <button class="btn btn-sm btn-secondary" onclick="adminToggle('${event.id}')">${event.is_active ? '⏸ Выключить' : '▶️ Включить'}</button>
            <button class="btn btn-sm btn-secondary" onclick="showAdminEventForm('${event.id}')">✏️ Редактировать</button>
            <button class="btn btn-sm btn-danger" onclick="adminDelete('${event.id}')">🗑 Удалить</button>
            ${!event.is_premium ? `<button class="btn btn-sm btn-primary" onclick="purchaseEventPremium('${event.id}')">💎 Премиум за событие</button>` : '<span class="badge badge-tier-pro">💎 Премиум</span>'}
        </div>
        <button class="btn btn-secondary" onclick="showAdminEvents()">← К списку</button>
    `;
}

// ─── Пригласительные: выдать / отменить / QR ──────────────────

// Ссылка на пригласительное (?invite=<код>) — универсально для TG и VK.
function inviteLink(code) {
    const base = window.location.origin;
    const path = state.platform === "vk" ? "/vk-app" : "";
    return `${base}${path}?invite=${encodeURIComponent(code)}`;
}

async function copyInviteLink(code) {
    if (!code) { showToast("Нет кода", true); return; }
    const link = inviteLink(code);
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(link);
            showToast("🔗 Ссылка скопирована");
        } else {
            tgAlert(`🔗 Ссылка на приглашение:\n\n${link}`);
        }
    } catch (e) {
        tgAlert(`🔗 Ссылка на приглашение:\n\n${link}`);
    }
}

async function adminIssueInvitePrompt(eventId) {
    const seats = await tgPrompt("Вместимость пригласительного (1/2/3 человека):", "1");
    if (!seats) return;
    const n = parseInt(seats, 10);
    if (n < 1 || n > 3) { showToast("Вместимость: 1, 2 или 3", true); return; }
    try {
        const res = await api(`/api/admin/events/${eventId}/invites`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ seats: n }),
        });
        // Показать ссылку + QR для передачи гостю (поделиться / отправить в ЛС).
        const link = inviteLink(res.validation_code);
        tgAlert(
            "✅ Пригласительное выдано!\n\n" +
            `🔗 Ссылка:\n${link}\n\n` +
            "Гость откроет её и активирует место сам.\n" +
            "QR-код этой ссылки — в списке пригласительных (кнопка QR)."
        );
        await showAdminEventDetail(eventId);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminCancelInvite(eventId, ticketId) {
    if (!(await tgConfirm("Отменить пригласительное?"))) return;
    try {
        await api(`/api/admin/events/${eventId}/invites/${ticketId}/cancel`, { method: "POST" });
        showToast("✅ Пригласительное отменено, места возвращены");
        await showAdminEventDetail(eventId);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ─── Промокоды: создать / вкл-выкл ─────────────────────────────

async function adminCreatePromo(eventId) {
    const code = (document.getElementById(`promo_code_${eventId}`)?.value || "").trim();
    const type = document.getElementById(`promo_type_${eventId}`)?.value;
    const value = parseFloat(document.getElementById(`promo_value_${eventId}`)?.value);
    const startEl = document.getElementById(`promo_start_${eventId}`);
    const endEl = document.getElementById(`promo_end_${eventId}`);
    const limit = parseInt(document.getElementById(`promo_limit_${eventId}`)?.value || "0", 10);

    if (!code) { showToast("Укажите код промокода", true); return; }
    if (!value || value <= 0) { showToast("Укажите скидку", true); return; }

    const payload = {
        code,
        discount_type: type,
        discount_value: value,
        max_uses: limit || 0,
    };
    if (startEl && startEl.value) payload.starts_at = new Date(startEl.value).toISOString();
    if (endEl && endEl.value) payload.ends_at = new Date(endEl.value).toISOString();

    try {
        await api(`/api/admin/events/${eventId}/promo-codes`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        showToast("✅ Промокод создан");
        await showAdminEventDetail(eventId);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminTogglePromo(eventId, promoId) {
    try {
        await api(`/api/admin/promo-codes/${promoId}/toggle`, { method: "POST" });
        showToast("✅ Статус обновлён");
        await showAdminEventDetail(eventId);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ─── Цены по дате: редактор диапазонов ─────────────────────────

function addPriceRangeRow(scopeId) {
    const list = document.getElementById(`priceRangesList_${scopeId}`);
    if (!list) return;
    const row = document.createElement("div");
    row.className = "price-range-row";
    row.style.cssText = "display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap";
    row.innerHTML = `
        <input class="form-input" type="datetime-local" class="pr_start" style="width:150px" title="С">
        <input class="form-input" type="datetime-local" class="pr_end" style="width:150px" title="По">
        <input class="form-input" type="number" min="0" step="0.01" class="pr_price" placeholder="Цена ₽" style="width:80px">
        <button type="button" class="btn btn-sm btn-secondary" onclick="adminDeletePriceRange(this)">✕</button>`;
    list.appendChild(row);
}

function adminDeletePriceRange(btn) {
    const row = btn.closest(".price-range-row");
    if (row) row.remove();
}

function collectPriceRanges(scopeId) {
    const list = document.getElementById(`priceRangesList_${scopeId}`);
    if (!list) return [];
    const ranges = [];
    list.querySelectorAll(".price-range-row").forEach(row => {
        const start = row.querySelector(".pr_start").value;
        const end = row.querySelector(".pr_end").value;
        const price = parseFloat(row.querySelector(".pr_price").value);
        if (start && end && !isNaN(price)) {
            ranges.push({ starts_at: new Date(start).toISOString(), ends_at: new Date(end).toISOString(), price });
        }
    });
    return ranges;
}

async function adminCreatePriceRange(eventId) {
    const ranges = collectPriceRanges(eventId);
    if (ranges.length === 0) { showToast("Добавьте хотя бы один диапазон", true); return; }
    try {
        await api(`/api/admin/events/${eventId}/price-ranges`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ranges }),
        });
        showToast("✅ Цены по дате сохранены");
        await showAdminEventDetail(eventId);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function showTicketQr(ticketId) {
    try {
        const resp = await fetch(`/api/admin/tickets/${ticketId}/qr`, { headers: authHeaders() });
        if (!resp.ok) { showToast("Ошибка загрузки QR", true); return; }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const overlay = document.createElement("div");
        overlay.className = "qr-modal";
        overlay.innerHTML = `
            <div class="qr-modal-card">
                <img src="${url}" alt="QR" style="width:220px;height:220px;border-radius:8px">
                <div style="display:flex;gap:8px;margin-top:12px">
                    <button class="btn btn-sm btn-primary" onclick="this.closest('.qr-modal').remove(); downloadQr('${ticketId}')">⬇️ Скачать</button>
                    <button class="btn btn-sm btn-secondary" onclick="this.closest('.qr-modal').remove()">Закрыть</button>
                </div>
            </div>`;
        overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
        document.body.appendChild(overlay);
    } catch (e) { showToast(e.message || "Ошибка QR", true); }
}

async function downloadQr(ticketId) {
    try {
        const resp = await fetch(`/api/admin/tickets/${ticketId}/qr`, { headers: authHeaders() });
        if (!resp.ok) { showToast("Ошибка", true); return; }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `ticket-${ticketId}-qr.png`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminPublish(eventId) {
    const channelSelect = document.getElementById(`publish_channel_${eventId}`);
    const channelId = channelSelect ? channelSelect.value : "";
    try {
        const body = channelId ? JSON.stringify({ channel_id: channelId }) : undefined;
        const res = await api(`/api/admin/events/${eventId}/publish`, {
            method: "POST",
            headers: body ? { "Content-Type": "application/json" } : undefined,
            body,
        });
        const where = channelId ? "" : " в DM";
        showToast(res.announced
            ? `✅ Опубликовано${where}, анонс отправлен`
            : `✅ Опубликовано${where} (анонс не отправлен)`);
    } catch (e) { showToast(e.message || "Ошибка", true); }
    await showAdminEventDetail(eventId);
}

async function adminRepost(eventId) {
    try {
        const res = await api(`/api/admin/events/${eventId}/repost`, { method: "POST" });
        showToast(res.announced ? "✅ Анонс отправлен" : "⚠️ Анонс не отправлен");
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminToggle(eventId) {
    try { await api(`/api/admin/events/${eventId}/toggle`, { method: "POST" }); showToast("✅ Статус изменён"); }
    catch (e) { showToast(e.message || "Ошибка", true); }
    await showAdminEventDetail(eventId);
}

// C: купить премиум на одно мероприятие (единовременная оплата, stub).
async function purchaseEventPremium(eventId) {
    if (!(await tgConfirm("Купить премиум на это мероприятие?\n\nДаст платные билеты, QR и пригласительные для этого события."))) return;
    try {
        const res = await api(`/api/me/events/${eventId}/premium`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
        });
        showToast("💎 Премиум активирован на событие");
        await showAdminEventDetail(eventId);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminDelete(eventId) {
    if (!(await tgConfirm("Удалить мероприятие?"))) return;
    try { await api(`/api/admin/events/${eventId}/delete`, { method: "POST" }); showToast("✅ Удалено"); await showAdminEvents(); }
    catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminCancelTicket(ticketId) {
    if (!(await tgConfirm("Отменить билет?"))) return;
    try {
        await api(`/api/admin/tickets/${ticketId}/cancel`, { method: "POST" });
        showToast("✅ Билет отменён");
        if (state.currentAdminEvent) await showAdminEventDetail(state.currentAdminEvent.id);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function downloadCsv(eventId) {
    try {
        const resp = await fetch(`/api/admin/events/${eventId}/tickets.csv`, { headers: authHeaders() });
        if (!resp.ok) { showToast("Ошибка загрузки CSV", true); return; }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `event-${eventId}-tickets.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (e) { showToast(e.message || "Ошибка CSV", true); }
}

// ─── Проверка билета ───────────────────────────────────────────

function showCheckin() {
    setActiveTab("checkin");
    updateToolbar("Проверка билета", true, false);
    showPage("checkin");
    document.getElementById("checkinContent").innerHTML = `
        <div class="checkin-box">
            <label class="form-label">Код билета</label>
            <input class="form-input" id="ci_code" placeholder="AB3X-K7M9" autocomplete="off" inputmode="text" style="text-transform:uppercase;font-size:20px;letter-spacing:2px;text-align:center">
            <button class="btn btn-primary" onclick="doCheckin()">🔍 Проверить и отметить вход</button>
            <button class="btn btn-secondary" onclick="openQrScanner()">📷 Сканировать QR</button>
        </div>
        <div id="checkinResult"></div>
    `;
    const input = document.getElementById("ci_code");
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") doCheckin(); });
    setTimeout(() => input.focus(), 100);
}

function normalizeTicketCode(raw) {
    let code = (raw || "").trim().toUpperCase();
    if (code.length === 8 && !code.includes("-")) code = code.slice(0, 4) + "-" + code.slice(4);
    return code;
}

async function doCheckin(prefilled) {
    const raw = prefilled !== undefined ? prefilled : document.getElementById("ci_code").value;
    let code = normalizeTicketCode(raw);
    if (!code) { showToast("Введите код", true); return; }

    const resultBox = document.getElementById("checkinResult");
    resultBox.innerHTML = '<p class="hint">Проверяю...</p>';

    try {
        // Сначала валидация для информативного ответа
        const info = await api(`/api/admin/tickets/validate?code=${encodeURIComponent(code)}`);
        if (!info.found) {
            resultBox.innerHTML = `<div class="checkin-result checkin-fail">❌ Билет с кодом ${escapeHtml(code)} не найден</div>`;
            return;
        }
        if (info.status === "checked_in") {
            resultBox.innerHTML = `<div class="checkin-result checkin-warn">🟡 Билет уже использован (вход: ${formatDate(info.checked_in_at)})</div>`;
            return;
        }
        if (info.status === "refunded") {
            resultBox.innerHTML = `<div class="checkin-result checkin-fail">❌ Билет возвращён</div>`;
            return;
        }
        // Активный — отмечаем вход
        const res = await api("/api/admin/tickets/checkin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
        resultBox.innerHTML = `
            <div class="checkin-result checkin-ok">
                ✅ Вход разрешён
                <div class="hint" style="color:inherit">${escapeHtml(info.user_name)} · ${escapeHtml(info.event_title)}</div>
            </div>`;
        document.getElementById("ci_code").value = "";
        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
            window.Telegram.WebApp.HapticFeedback.notificationOccurred("success");
        }
    } catch (err) {
        resultBox.innerHTML = `<div class="checkin-result checkin-fail">❌ ${escapeHtml(err.message || "Ошибка")}</div>`;
    }
}

// ─── QR-сканер (камера + фото-фоллбек) ──────────────────────────
// Формат кода билета подтверждён генератором (services.generate_validation_code):
// 8 hex-символов → XXXX-XXXX. Сканер авто-check-in только для такого формата.
const QR_CODE_RE = /^[0-9A-F]{4}-[0-9A-F]{4}$/;
let qrScannerStream = null;
let qrScanRaf = null;
let qrScanning = false;
let qrScannerOverlay = null;

function isTicketCode(raw) {
    return QR_CODE_RE.test(normalizeTicketCode(raw));
}

function openQrScanner() {
    if (typeof jsQR === "undefined") {
        showToast("Сканер не загрузился — введите код вручную", true);
        return;
    }
    if (qrScannerOverlay) return; // уже открыт

    qrScannerOverlay = document.createElement("div");
    qrScannerOverlay.className = "qr-modal";
    qrScannerOverlay.innerHTML = `
        <div class="qr-modal-card qr-scanner-card">
            <video id="qrScannerVideo" class="qr-scanner-video" playsinline muted autoplay></video>
            <canvas id="qrScannerCanvas" hidden></canvas>
            <p class="hint" style="margin:8px 0 0">Наведите камеру на QR билета</p>
            <div class="qr-scanner-actions">
                <button class="btn btn-sm btn-secondary" onclick="qrScanPhotoFallback()">📷 Сфотографировать QR</button>
                <button class="btn btn-sm btn-secondary" onclick="closeQrScanner()">Закрыть</button>
            </div>
            <input id="qrFileInput" type="file" accept="image/*" capture="environment" hidden onchange="scanFromPhoto(this)">
        </div>`;
    qrScannerOverlay.addEventListener("click", (e) => { if (e.target === qrScannerOverlay) closeQrScanner(); });
    document.body.appendChild(qrScannerOverlay);
    startQrCamera();
}

async function startQrCamera() {
    const video = document.getElementById("qrScannerVideo");
    const canvas = document.getElementById("qrScannerCanvas");
    if (!video || !canvas) return;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        showToast("Камера недоступна — используйте фото", true);
        qrScanPhotoFallback();
        return;
    }

    // Android Telegram WebView: getUserMedia может «виснуть» — таймаут → фото-фоллбек
    let timedOut = false;
    const timeoutId = setTimeout(() => { timedOut = true; qrScanPhotoFallback(); }, 3000);

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        clearTimeout(timeoutId);
        if (timedOut || !qrScannerOverlay) { stream.getTracks().forEach(t => t.stop()); return; }
        qrScannerStream = stream;
        video.srcObject = stream;

        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        const tick = () => {
            if (qrScanning) return;
            if (video.readyState === video.HAVE_ENOUGH_DATA) {
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
                const r = jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" });
                if (r && r.data && isTicketCode(r.data)) {
                    qrScanning = true;
                    const code = r.data;
                    closeQrScanner();
                    doCheckin(code);
                    return;
                }
            }
            qrScanRaf = requestAnimationFrame(tick);
        };
        qrScanRaf = requestAnimationFrame(tick);
    } catch (err) {
        clearTimeout(timeoutId);
        if (timedOut || !qrScannerOverlay) return;
        if (err && err.name === "NotAllowedError") {
            showToast("Доступ к камере запрещён. Разрешите или используйте фото", true);
        } else {
            showToast("Камера недоступна — используйте фото", true);
        }
        qrScanPhotoFallback();
    }
}

function qrScanPhotoFallback() {
    stopQrScanner(); // не удаляем оверлей — внутри него input
    const input = document.getElementById("qrFileInput");
    if (input) input.click();
}

async function scanFromPhoto(input) {
    const file = input && input.files && input.files[0];
    input.value = "";
    if (!file) return;
    if (typeof jsQR === "undefined") { showToast("Сканер не загрузился — введите код вручную", true); return; }

    try {
        const img = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const image = new Image();
                image.onload = () => resolve(image);
                image.onerror = reject;
                image.src = reader.result;
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
        const scale = Math.min(1, 600 / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const r = jsQR(imageData.data, imageData.width, imageData.height, { inversionAttempts: "dontInvert" });
        if (r && r.data && isTicketCode(r.data)) {
            closeQrScanner();
            doCheckin(r.data);
        } else {
            showToast("QR не распознан — попробуйте ещё раз", true);
        }
    } catch (e) {
        showToast("Не удалось прочитать фото", true);
    }
}

function stopQrScanner() {
    if (qrScanRaf) { cancelAnimationFrame(qrScanRaf); qrScanRaf = null; }
    if (qrScannerStream) { qrScannerStream.getTracks().forEach(t => t.stop()); qrScannerStream = null; }
    qrScanning = false;
}

function closeQrScanner() {
    stopQrScanner();
    if (qrScannerOverlay) { qrScannerOverlay.remove(); qrScannerOverlay = null; }
}

// ─── Каналы (super-admin) ──────────────────────────────────────

async function showAdminChannels() {
    setActiveTab("channels");
    state.lastAction = "showAdminChannels";
    updateToolbar("Каналы", true, false);
    showPage("admin-channels");
    showLoading();
    try {
        const channels = await api("/api/admin/channels");
        state.adminChannels = channels;
        renderAdminChannels(channels);
    } catch (err) {
        hideLoading();
        showError(err.message || "Ошибка загрузки каналов");
    }
}

function renderAdminChannels(channels) {
    hideLoading();
    const container = document.getElementById("adminChannelsContent");
    if (!channels || channels.length === 0) {
        container.innerHTML = `
            <button class="btn btn-primary" onclick="adminAddChannelPrompt()">➕ Добавить канал</button>
            <div class="empty-state"><div class="empty-icon">📢</div><h3>Нет каналов</h3></div>
        `;
        return;
    }
    container.innerHTML = `
        <button class="btn btn-primary" onclick="adminAddChannelPrompt()">➕ Добавить канал</button>
        <button class="btn btn-secondary" onclick="adminCheckExpired()">🔍 Проверить просроченные подписки</button>
        <div class="admin-list" style="margin-top:12px">
            ${channels.map(ch => `
                <div class="admin-list-item">
                    <div style="flex:1">
                        <div><b>${escapeHtml(ch.title || ch.telegram_channel_id)}</b>
                            <span class="badge ${ch.subscription_tier === 'pro' ? 'badge-tier-pro' : 'badge-tier-basic'}">${ch.subscription_tier}</span>
                            ${ch.is_subscription_active ? '<span class="badge badge-published">активна</span>' : '<span class="badge badge-off">нет подписки</span>'}
                        </div>
                        <div class="hint">${escapeHtml(ch.telegram_channel_id)} · ${ch.admins.length ? 'Админы: ' + ch.admins.map(escapeHtml).join(', ') : '—'}${ch.subscription_until ? ' · до ' + formatDate(ch.subscription_until) : ''}</div>
                    </div>
                    <div style="display:flex;flex-direction:column;gap:6px;min-width:120px">
                        ${ch.is_subscription_active
                            ? `<button class="btn btn-sm btn-secondary" onclick="adminUnsubscribe('${ch.id}')">Отписать</button>`
                            : `<button class="btn btn-sm btn-primary" onclick="adminSubscribePrompt('${ch.id}')">Подписать</button>`}
                        <button class="btn btn-sm btn-secondary" onclick="showAdminChannelSubscription('${ch.id}')">⚙️ Подписка</button>
                        <button class="btn btn-sm btn-secondary" onclick="showChannelInfo('${ch.id}')">ℹ️ Инфо</button>
                        <button class="btn btn-sm btn-secondary" onclick="adminChangeAdminPrompt('${ch.id}')">Сменить админа</button>
                    </div>
                </div>`).join('')}
        </div>
    `;
}

async function adminSubscribePrompt(channelId) {
    const days = await tgPrompt("Срок подписки (дней):", "30");
    if (!days) return;
    const tier = (await tgPrompt("Тариф (basic/pro):", "basic")) === "pro" ? "pro" : "basic";
    try {
        await api(`/api/admin/channels/${channelId}/subscribe`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ duration_days: parseInt(days, 10) || 30, tier }),
        });
        showToast("✅ Подписка активирована");
        await showAdminChannels();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminUnsubscribe(channelId) {
    if (!(await tgConfirm("Отключить подписку?"))) return;
    try {
        await api(`/api/admin/channels/${channelId}/unsubscribe`, { method: "POST" });
        showToast("✅ Подписка отключена");
        await showAdminChannels();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminChangeAdminPrompt(channelId) {
    const newId = await tgPrompt("Telegram ID нового админа:");
    if (!newId) return;
    try {
        await api(`/api/admin/channels/${channelId}/change_admin`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_admin_id: newId.trim() }),
        });
        showToast("✅ Админ сменён");
        await showAdminChannels();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ─── Мои каналы (самообслуживание для организаторов) ────────────

async function showMyChannels() {
    setActiveTab("admin");
    state.lastAction = "showMyChannels";
    updateToolbar("Мои каналы", false, false);
    showPage("my-channels");
    showLoading();
    try {
        const channels = await api("/api/me/channels");
        state.myChannels = channels;
        renderMyChannels(channels);
    } catch (err) {
        hideLoading();
        showError(err.message || "Ошибка загрузки каналов");
    }
}

function renderMyChannels(channels) {
    hideLoading();
    const container = document.getElementById("myChannelsContent");
    // Форма добавления: <input> вместо prompt() — prompt не работает в Telegram WebView
    let html = `
        <h2 style="margin-bottom:16px">Мои каналы</h2>
        <div class="form-inline" style="margin-bottom:16px;display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
            <div class="form-field" style="flex:1;min-width:150px">
                <label class="form-label">@username или ID канала</label>
                <input class="form-input" id="mc_telegram_id" placeholder="@channel">
            </div>
            <div class="form-field" style="flex:1;min-width:120px">
                <label class="form-label">Название (необязательно)</label>
                <input class="form-input" id="mc_title" placeholder="Мой канал">
            </div>
            <button class="btn btn-primary" onclick="addMyChannel()" style="margin-bottom:0">➕ Добавить</button>
        </div>
    `;
    // Enter key listener
    setTimeout(() => {
        const idInput = document.getElementById("mc_telegram_id");
        if (idInput) {
            idInput.addEventListener("keydown", (e) => { if (e.key === "Enter") addMyChannel(); });
        }
    }, 0);

    if (!channels || channels.length === 0) {
        html += `<div class="empty-state"><div class="empty-icon">📢</div><h3>Нет каналов</h3><p>Добавьте канал по @username — он появится здесь</p></div>`;
    } else {
        html += `<div class="admin-list" style="margin-top:12px">`;
        for (const ch of channels) {
            html += `
                <div class="admin-list-item">
                    <div style="flex:1">
                        <div><b>${escapeHtml(ch.title || ch.telegram_channel_id)}</b></div>
                        <div class="hint">${escapeHtml(ch.telegram_channel_id)}</div>
                    </div>
                </div>`;
        }
        html += `</div>`;
    }
    container.innerHTML = html + `
        <button class="btn btn-secondary" style="margin-top:16px" onclick="showMyVKGroups()">📢 VK-группы</button>
    `;
}

async function addMyChannel() {
    const telegramId = document.getElementById("mc_telegram_id").value.trim();
    if (!telegramId) { showToast("Введите @username или ID канала", true); return; }
    const title = document.getElementById("mc_title").value.trim() || null;
    try {
        await api("/api/me/channels", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ telegram_channel_id: telegramId, title }),
        });
        showToast("✅ Канал добавлен");
        // Освежить список каналов и профиль
        await loadMe();
        await showMyChannels();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ─── VK-группы (self-service целей публикации) ────────────────

async function showMyVKGroups() {
    setActiveTab("admin");
    state.lastAction = "showMyVKGroups";
    updateToolbar("VK-группы", true, false);
    showPage("my-vk-groups");
    showLoading();
    try {
        const groups = await api("/api/me/vk-groups");
        renderMyVKGroups(groups);
    } catch (err) {
        hideLoading();
        showError(err.message || "Ошибка загрузки VK-групп");
    }
}

function renderMyVKGroups(groups) {
    hideLoading();
    const container = document.getElementById("myVKGroupsContent");
    let html = `
        <h2 style="margin-bottom:16px">Мои VK-группы</h2>
        <div class="form-inline" style="margin-bottom:16px;display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
            <div class="form-field" style="flex:1;min-width:150px">
                <label class="form-label">ID VK-группы</label>
                <input class="form-input" id="vkg_id" placeholder="12345678">
            </div>
            <div class="form-field" style="flex:1;min-width:120px">
                <label class="form-label">Название (необязательно)</label>
                <input class="form-input" id="vkg_title" placeholder="Моя группа">
            </div>
            <button class="btn btn-primary" onclick="addMyVKGroup()" style="margin-bottom:0">➕ Добавить</button>
        </div>
    `;
    setTimeout(() => {
        const idInput = document.getElementById("vkg_id");
        if (idInput) idInput.addEventListener("keydown", (e) => { if (e.key === "Enter") addMyVKGroup(); });
    }, 0);

    if (!groups || groups.length === 0) {
        html += `<div class="empty-state"><div class="empty-icon">📢</div><h3>Нет VK-групп</h3>
            <p>Добавьте группу по ID — сюда будут публиковаться анонсы мероприятий</p></div>`;
    } else {
        html += `<div class="admin-list" style="margin-top:12px">`;
        for (const g of groups) {
            html += `
                <div class="admin-list-item">
                    <div style="flex:1">
                        <div><b>${escapeHtml(g.title || g.group_id)}</b>
                            ${g.has_token ? '<span class="badge badge-published">токен</span>' : '<span class="badge badge-draft">нет токена</span>'}
                        </div>
                        <div class="hint">ID: ${escapeHtml(g.group_id)}</div>
                    </div>
                    <button class="btn btn-sm btn-danger" onclick="removeMyVKGroup('${escapeHtml(g.group_id)}')">Удалить</button>
                </div>`;
        }
        html += `</div>`;
    }
    container.innerHTML = html;
}

async function addMyVKGroup() {
    const groupId = document.getElementById("vkg_id").value.trim();
    if (!groupId) { showToast("Введите ID VK-группы", true); return; }
    const title = document.getElementById("vkg_title").value.trim() || null;

    // В VK Mini App запрашиваем community token (VKWebAppGetCommunityToken):
    // приложение должно быть установлено в группу, пользователь — её админ.
    // Токен нужен для wall.post/messages.send (анонсы и DM билета).
    let communityToken;
    if (window.vkBridge && state.platform === "vk") {
        showLoading();
        try {
            const res = await window.vkBridge.send("VKWebAppGetCommunityToken", {
                app_id: parseInt(state.vkAppId, 10) || undefined,
                group_id: parseInt(groupId, 10),
                scope: "wall,messages,manage,photos,app_widget",
            });
            communityToken = (res && res.access_token) || "";
        } catch (e) {
            hideLoading();
            showToast("Не удалось получить токен группы. Установите приложение в группу и убедитесь, что вы её администратор", true);
            return;
        }
        if (!communityToken) {
            hideLoading();
            showToast("Токен группы не получен (нет прав админа или приложение не установлено в группу)", true);
            return;
        }
        hideLoading();
    }

    const payload = { group_id: groupId, title };
    if (communityToken) payload.community_token = communityToken;
    try {
        await api("/api/me/vk-groups", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        showToast("✅ VK-группа добавлена" + (communityToken ? " (с токеном)" : ""));
        await showMyVKGroups();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function removeMyVKGroup(groupId) {
    if (!(await tgConfirm(`Удалить VK-группу ${groupId}?`))) return;
    try {
        await api(`/api/me/vk-groups/${encodeURIComponent(groupId)}`, { method: "DELETE" });
        showToast("✅ VK-группа удалена");
        await showMyVKGroups();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ─── Линковка площадок (organizer-only) ───────────────────────

async function createVKLinkCode() {
    try {
        const res = await api("/api/me/link-code", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target_platform: "vk" }),
        });
        tgAlert(`🔗 Код привязки VK:\n\n<b>${res.code}</b>\n\nДействует ${res.ttl_minutes} мин.\nВведите его в VK Mini App → «Привязать Telegram».`);
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function linkVKByCode() {
    const code = await tgPrompt("Код привязки из Telegram:");
    if (!code) return;
    try {
        const res = await api("/api/me/link", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code: code.trim() }),
        });
        showToast("✅ VK привязан к Telegram");
        await loadMe();
        await showProfile();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function adminCheckExpired() {
    try {
        const res = await api("/api/admin/channels/check_expired", { method: "POST" });
        showToast(`✅ Проверено: ${res.checked}, отключено: ${res.deactivated}`);
        await showAdminChannels();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ─── Статистика (super-admin) ──────────────────────────────────

async function showAdminStats() {
    setActiveTab("stats");
    state.lastAction = "showAdminStats";
    updateToolbar("Статистика", true, false);
    showPage("admin-stats");
    showLoading();
    try {
        const stats = await api("/api/admin/stats");
        state.globalStats = stats;
        renderAdminStats(stats);
    } catch (err) {
        hideLoading();
        showError(err.message || "Ошибка загрузки статистики");
    }
}

function renderAdminStats(s) {
    hideLoading();
    document.getElementById("adminStatsContent").innerHTML = `
        <div class="stat-grid">
            <div class="stat-card"><div class="stat-value">${s.users_count}</div><div class="stat-label">👥 Пользователей</div></div>
            <div class="stat-card"><div class="stat-value">${s.channels_count}</div><div class="stat-label">📢 Каналов</div></div>
            <div class="stat-card"><div class="stat-value">${s.active_subs}</div><div class="stat-label">Активных подписок</div></div>
            <div class="stat-card"><div class="stat-value">${s.events_count}</div><div class="stat-label">🎫 Мероприятий</div></div>
            <div class="stat-card"><div class="stat-value">${s.upcoming_count}</div><div class="stat-label">Предстоящих</div></div>
            <div class="stat-card"><div class="stat-value">${s.tickets_active}</div><div class="stat-label">🎟 Активных билетов</div></div>
            <div class="stat-card stat-revenue"><div class="stat-value">${formatPrice(s.revenue)}</div><div class="stat-label">💰 Выручка</div></div>
        </div>
    `;
}

// ─── Добавить канал (super-admin) ─────────────────────────────

async function adminAddChannelPrompt() {
    const telegramChannelId = await tgPrompt("Telegram ID канала (@username или числовой):");
    if (!telegramChannelId) return;
    const days = await tgPrompt("Срок подписки (дней):", "30");
    if (!days) return;
    const tier = (await tgPrompt("Тариф (basic/pro):", "basic")) === "pro" ? "pro" : "basic";
    const title = await tgPrompt("Название (необязательно):", "") || null;
    try {
        await api("/api/admin/channels", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                telegram_channel_id: telegramChannelId.trim(),
                title,
                duration_days: parseInt(days, 10) || 30,
                tier,
            }),
        });
        showToast("✅ Канал добавлен и подписка активирована");
        await showAdminChannels();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ─── Рассылка (super-admin) ───────────────────────────────────

function showBroadcast() {
    updateToolbar("Рассылка", true, false);
    showPage("admin-broadcast");
    document.getElementById("adminBroadcastContent").innerHTML = `
        <div class="form-field">
            <label class="form-label">Сообщение для всех активных каналов</label>
            <textarea class="form-input" id="bc_text" placeholder="Введите текст рассылки..." style="min-height:120px"></textarea>
        </div>
        <button class="btn btn-primary" onclick="doBroadcast()">📣 Отправить</button>
        <div id="bcResult"></div>
    `;
}

async function doBroadcast() {
    const text = document.getElementById("bc_text").value.trim();
    if (!text) { showToast("Введите текст", true); return; }
    const btn = document.querySelector('#adminBroadcastContent .btn-primary');
    btn.disabled = true;
    try {
        const res = await api("/api/admin/broadcast", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
        });
        document.getElementById("bcResult").innerHTML =
            `<div class="checkin-result checkin-ok">✅ Отправлено в ${res.sent}/${res.total} каналов</div>`;
        document.getElementById("bc_text").value = "";
    } catch (e) {
        showToast(e.message || "Ошибка", true);
    } finally {
        btn.disabled = false;
    }
}

// ─── Инфо о пользователе (super-admin) ───────────────────────

function showUserInfo() {
    updateToolbar("Инфо о пользователе", true, false);
    showPage("admin-userinfo");
    document.getElementById("adminUserInfoContent").innerHTML = `
        <div class="form-field">
            <label class="form-label">Telegram ID или @username организатора</label>
            <input class="form-input" id="ui_userid" placeholder="123456789 или @ivan">
        </div>
        <button class="btn btn-primary" onclick="doUserInfoLookup()">🔍 Найти</button>
        <div id="uiResult"></div>
    `;
    const input = document.getElementById("ui_userid");
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") doUserInfoLookup(); });
}

async function doUserInfoLookup() {
    const id = document.getElementById("ui_userid").value.trim();
    if (!id) { showToast("Введите Telegram ID", true); return; }
    const resultBox = document.getElementById("uiResult");
    resultBox.innerHTML = '<p class="hint">Поиск...</p>';
    try {
        const user = await api(`/api/admin/users/${encodeURIComponent(id)}`);
        const channels = user.channels || [];
        resultBox.innerHTML = `
            <div class="profile-card" style="margin-top:16px">
                <div class="profile-avatar">👤</div>
                <h2>${escapeHtml(user.name || "Без имени")}</h2>
                <p class="hint">
                    ${user.username ? `<code>@${escapeHtml(user.username)}</code> · ` : ''}
                    Telegram ID: <code>${escapeHtml(user.telegram_user_id)}</code>
                </p>
            </div>
            <div class="profile-card" style="margin-top:16px">
                <h3>Подписка организатора</h3>
                <p class="hint">
                    Статус: ${user.is_subscription_active ? '🟢 активна' : '🔴 нет'}
                    · тариф: ${escapeHtml(user.subscription_tier || '—')}
                    · до: ${user.subscription_until ? formatDate(user.subscription_until) : '—'}
                </p>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
                    <input class="form-input" id="ui_sub_days" type="number" min="1" value="30" style="width:80px" title="Дни">
                    <select class="form-input" id="ui_sub_tier" style="width:110px">
                        <option value="basic">basic</option>
                        <option value="pro">pro</option>
                    </select>
                    <button class="btn btn-sm btn-primary" onclick="adminUserSubscribe('${user.telegram_user_id}')">🟢 Подписать</button>
                </div>
                <div class="hint" style="margin:4px 0 0">Если пользователь не найден — он должен хоть раз зайти в бота/Mini App</div>
            </div>
            <h3 style="margin:16px 0 8px">Каналы (${channels.length})</h3>
            ${channels.length === 0
                ? '<p class="hint">Нет каналов</p>'
                : `<div class="admin-list">${channels.map(ch => `
                    <div class="admin-list-item">
                        <div><b>${escapeHtml(ch.title || ch.telegram_channel_id)}</b>
                            <span class="badge ${ch.subscription_tier === 'pro' ? 'badge-tier-pro' : 'badge-tier-basic'}">${ch.subscription_tier}</span>
                            ${ch.is_subscription_active ? '<span class="badge badge-published">активна</span>' : '<span class="badge badge-off">нет</span>'}
                        </div>
                    </div>`).join('')}</div>`}
        `;
    } catch (e) {
        resultBox.innerHTML = `<div class="checkin-result checkin-fail">❌ ${escapeHtml(e.message || "Пользователь не найден")}</div>`;
    }
}

async function adminUserSubscribe(userId) {
    const days = parseInt(document.getElementById("ui_sub_days").value, 10) || 30;
    const tier = document.getElementById("ui_sub_tier").value;
    try {
        await api(`/api/admin/users/${encodeURIComponent(userId)}/subscription`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ duration_days: days, tier }),
        });
        showToast("✅ Подписка выдана");
        doUserInfoLookup();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// Матрица ролей: пользователь → организатор (через подписку)
async function becomeOrganizer() {
    try {
        await api("/api/me/subscription", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tier: "basic" }),
        });
        showToast("✅ Вы стали организатором");
        await loadMe();
        await showHome();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ─── Здоровье (super-admin) ───────────────────────────────────

async function showAdminHealth() {
    updateToolbar("Здоровье", true, false);
    showPage("admin-health");
    showLoading();
    try {
        const h = await api("/api/admin/health");
        hideLoading();
        document.getElementById("adminHealthContent").innerHTML = `
            <div class="stat-grid">
                <div class="stat-card ${h.status === 'ok' ? 'checkin-ok' : 'checkin-fail'}">
                    <div class="stat-value">${h.status === 'ok' ? '✅' : '⚠️'}</div>
                    <div class="stat-label">Статус</div>
                </div>
                <div class="stat-card"><div class="stat-value">${h.db_ok ? '✅' : '❌'}</div><div class="stat-label">База данных</div></div>
            </div>
            <div class="admin-list-item" style="margin-top:12px">
                <div><b>🤖 Бот</b></div>
                <div class="hint">${h.bot_username ? '@' + escapeHtml(h.bot_username) : 'неизвестно'}</div>
            </div>
        `;
    } catch (e) {
        hideLoading();
        showError(e.message || "Ошибка загрузки");
    }
}

// ═══════════════════════════════════════════════════════════════
// Управление подпиской канала (super-admin): тип + срок
// ═══════════════════════════════════════════════════════════════

function showAdminChannelSubscription(channelId) {
    const channel = (state.adminChannels || []).find(c => c.id === channelId);
    if (!channel) { showToast("Канал не найден", true); return; }

    updateToolbar("Подписка канала", true, false);
    showPage("admin-channel-subscription");

    const curTier = channel.subscription_tier === "pro" ? "pro" : "basic";
    document.getElementById("adminChannelSubContent").innerHTML = `
        <h2 style="margin-bottom:8px">${escapeHtml(channel.title || channel.telegram_channel_id)}</h2>
        <p class="hint">${escapeHtml(channel.telegram_channel_id)}${channel.subscription_until ? ' · до ' + formatDate(channel.subscription_until) : ''}</p>

        <div class="form-field">
            <label class="form-label">Тип подписки (тариф)</label>
            <select class="form-input" id="sub_tier">
                <option value="basic" ${curTier === 'basic' ? 'selected' : ''}>Basic</option>
                <option value="pro" ${curTier === 'pro' ? 'selected' : ''}>Pro</option>
            </select>
        </div>
        <div class="form-field">
            <label class="form-label">Период</label>
            <div style="display:flex;gap:8px">
                <input class="form-input" type="number" min="1" step="1" id="sub_period" value="1" style="flex:1">
                <select class="form-input" id="sub_unit" style="flex:1">
                    <option value="days">Дней</option>
                    <option value="months" selected>Месяцев</option>
                    <option value="years">Лет</option>
                </select>
            </div>
        </div>
        <button class="btn btn-primary" onclick="applySubscription('${channelId}')">💾 Применить (тип + срок)</button>
        <button class="btn btn-secondary" onclick="changeTier('${channelId}')">🔄 Только сменить тариф</button>
        <button class="btn btn-secondary" onclick="showAdminChannels()">← К списку</button>
    `;
}

async function applySubscription(channelId) {
    const tier = document.getElementById("sub_tier").value;
    const period = parseInt(document.getElementById("sub_period").value, 10);
    const period_unit = document.getElementById("sub_unit").value;
    if (!period || period <= 0) { showToast("Укажите количество", true); return; }
    try {
        await api(`/api/admin/channels/${channelId}/subscription`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tier, period, period_unit }),
        });
        showToast("✅ Подписка обновлена");
        await showAdminChannels();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

async function changeTier(channelId) {
    // Используем выбранный в форме тариф (не prompt — он дублирует select и не работает в Telegram)
    const tier = document.getElementById("sub_tier").value;
    if (tier !== "basic" && tier !== "pro") { showToast("Тариф: basic или pro", true); return; }
    try {
        await api(`/api/admin/channels/${channelId}/tier`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tier }),
        });
        showToast("✅ Тариф сменён");
        await showAdminChannels();
    } catch (e) { showToast(e.message || "Ошибка", true); }
}

// ═══════════════════════════════════════════════════════════════
// Инфо о канале (детальная сводка, super-admin)
// ═══════════════════════════════════════════════════════════════

async function showChannelInfo(channelId) {
    updateToolbar("Инфо о канале", true, false);
    showPage("admin-channels");
    const container = document.getElementById("adminChannelsContent");
    showLoading();
    try {
        const info = await api(`/api/admin/channels/${channelId}`);
        hideLoading();
        container.innerHTML = `
            <h2 style="margin-bottom:8px">${escapeHtml(info.title || info.telegram_channel_id)}</h2>
            <p class="hint">${escapeHtml(info.telegram_channel_id)}</p>
            <div class="event-meta">
                <div class="meta-row"><span class="meta-label">Статус</span><span>${info.is_subscription_active ? '🟢 Активна' : '🔴 Неактивна'}</span></div>
                <div class="meta-row"><span class="meta-label">Тариф</span><span>${info.subscription_tier}</span></div>
                <div class="meta-row"><span class="meta-label">Подписка до</span><span>${info.subscription_until ? formatDate(info.subscription_until) : '—'}</span></div>
                <div class="meta-row"><span class="meta-label">Админы</span><span>${(info.admins || []).length ? info.admins.map(escapeHtml).join(', ') : '—'}</span></div>
                <div class="meta-row"><span class="meta-label">Мероприятий</span><span>${info.events_count}</span></div>
                <div class="meta-row"><span class="meta-label">Предстоящих</span><span>${info.upcoming_count}</span></div>
                <div class="meta-row"><span class="meta-label">Продано билетов</span><span>${info.tickets_sold}</span></div>
            </div>
            <button class="btn btn-secondary" onclick="showAdminChannels()">← К списку каналов</button>
        `;
    } catch (e) {
        hideLoading();
        showError(e.message || "Ошибка загрузки канала");
    }
}
