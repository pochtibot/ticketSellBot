"""VK API client for private ticket messages.

Community token хранится зашифрованным и расшифровывается только для API-запроса.
"""

import logging

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
