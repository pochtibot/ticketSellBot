"""VK API client for community widgets and private messages.

Community token хранится зашифрованным и расшифровывается только для API-запроса.
"""

import json
import logging
from datetime import timezone

import httpx

from app.core.crypto import decrypt_token

logger = logging.getLogger("ticketbot.web.vk_api")

_API_VERSION = "5.199"


class VKAPIError(Exception):
    """Ошибка VK API."""


async def vk_api_call(method: str, token: str, **params) -> dict:
    """Вызвать метод VK API с access_token."""
    url = f"https://api.vk.com/method/{method}"
    data = {"access_token": token, "v": _API_VERSION, **params}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data)
        payload = resp.json()
    if "error" in payload:
        err = payload["error"]
        raise VKAPIError(
            f"VK API {method} error {err.get('error_code')}: {err.get('error_msg')}"
        )
    return payload.get("response", {})


async def verify_group_token(group_id: str, token: str) -> bool:
    """Проверить, что community token действительно относится к указанной группе.

    Вызывает VK API groups.getById с токеном. Возвращает True, если в ответе
    есть группа с совпадающим id. Ошибки VK (нет доступа, неверный/чужой токен,
    группа не найдена) → False. Защита от регистрации чужой группы со своим токеном.
    """
    if not token:
        return False
    try:
        response = await vk_api_call("groups.getById", token, group_ids=str(group_id))
    except VKAPIError as e:
        logger.warning("verify_group_token failed for group %s: %s", group_id, e)
        return False

    # groups.getById v5.199 returns {"groups": [...], "profiles": [...]};
    # keep compatibility with wrappers/older responses that use items or a list.
    if isinstance(response, dict):
        items = response.get("groups") or response.get("items") or []
    elif isinstance(response, list):
        items = response
    else:
        items = []
    for group in items:
        if isinstance(group, dict) and str(group.get("id")) == str(group_id):
            return True
    logger.warning("verify_group_token: группа %s не найдена по токену", group_id)
    return False


async def update_community_event_widget(group, event) -> None:
    """Replace a community app's text widget with a compact event card."""
    token = decrypt_token(group.community_token or "")
    if not token:
        raise VKAPIError("Для VK-сообщества не сохранён ключ с правом app_widget")

    event_date = event.date
    if getattr(event_date, "tzinfo", None) is None:
        event_date = event_date.replace(tzinfo=timezone.utc)
    event_date = event_date.astimezone(timezone.utc)
    date_text = event_date.strftime("%d.%m.%Y в %H:%M UTC")
    price_text = f"Билет: {event.price} ₽" if event.price and float(event.price) > 0 else "Бесплатно"
    details = " · ".join(
        value for value in (date_text, event.location or "Место не указано", price_text) if value
    )
    widget = {
        "title": str(event.title or "Мероприятие")[:100],
        "text": details[:200],
        "descr": "Билеты и подробности — в приложении Билетер",
    }
    # Values are JSON-encoded so event content cannot escape the VKScript object.
    await vk_api_call(
        "appWidgets.update",
        token,
        type="text",
        code=f"return {json.dumps(widget, ensure_ascii=False)};",
    )


async def send_vk_ticket_dm(vk_user_id: str, text: str, group) -> bool:
    """Отправить билет в личные сообщения VK покупателю от имени группы организатора.

    Для messages.send нужен: community token группы (расшифрованный) + право
    пользователя на сообщения от этой группы (VKWebAppAllowMessagesFromGroup на фронте).
    Если токена нет или VK возвращает ошибку (например, нет разрешения) — тихо False:
    билет всегда остаётся доступным в кабинете («Мои билеты»).
    """
    token = decrypt_token(group.community_token or "")
    if not token:
        logger.warning("Нет community token для VK-группы %s — DM билета не отправлен", group.group_id)
        return False
    try:
        await vk_api_call(
            "messages.send",
            token,
            peer_id=int(vk_user_id),
            message=text,
            random_id=0,
        )
        logger.info("Билет отправлен в ЛС VK покупателю %s от группы %s", vk_user_id, group.group_id)
        return True
    except VKAPIError as e:
        logger.error("Ошибка messages.send покупателю %s: %s", vk_user_id, e)
        return False
