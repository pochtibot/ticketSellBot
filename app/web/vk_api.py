"""VK API client for server-side community operations.

Community token хранится зашифрованным и расшифровывается только для API-запроса.
"""

import json
import logging

import httpx

from app.core.crypto import decrypt_token

logger = logging.getLogger("ticketbot.web.vk_api")

_API_VERSION = "5.199"


class VKAPIError(Exception):
    """Ошибка VK API."""

    def __init__(self, message: str, *, code: int | None = None, api_message: str | None = None):
        super().__init__(message)
        self.code = code
        self.api_message = api_message or message


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
            f"VK API {method} error {err.get('error_code')}: {err.get('error_msg')}",
            code=err.get("error_code"),
            api_message=str(err.get("error_msg") or "VK API error"),
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


async def send_vk_organizer_announcement_dm(
    vk_user_id: str,
    text: str,
    group,
    *,
    keyboard: dict | None = None,
    poster_data: bytes | None = None,
    poster_content_type: str = "image/jpeg",
) -> bool:
    """Send a formatted event announcement with optional poster and link button."""
    token = decrypt_token(group.community_token or "")
    if not token:
        logger.warning("Нет community token для VK-группы %s — DM анонса не отправлен", group.group_id)
        return False
    peer_id = int(vk_user_id)
    attachment = None
    if poster_data:
        try:
            attachment = await _upload_message_photo(
                token, peer_id, poster_data, poster_content_type
            )
        except (VKAPIError, httpx.HTTPError, KeyError, TypeError, ValueError) as e:
            logger.warning("Не удалось добавить афишу VK-анонса для группы %s: %s", group.group_id, e)
            return False

    params = {
        "peer_id": peer_id,
        "message": text,
        "random_id": 0,
    }
    if keyboard:
        params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
    if attachment:
        params["attachment"] = attachment
    try:
        await vk_api_call("messages.send", token, **params)
        logger.info("Анонс отправлен организатору VK %s от группы %s", vk_user_id, group.group_id)
        return True
    except VKAPIError as e:
        logger.error("Ошибка отправки VK-анонса организатору %s: %s", vk_user_id, e)
        return False


async def _upload_message_photo(
    token: str,
    peer_id: int,
    image_data: bytes,
    content_type: str,
) -> str:
    """Upload an event poster through VK's documented private-message photo flow."""
    upload_info = await vk_api_call("photos.getMessagesUploadServer", token, peer_id=peer_id)
    upload_url = upload_info.get("upload_url")
    if not upload_url:
        raise ValueError("VK не вернул адрес загрузки афиши")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            upload_url,
            files={"photo": ("event-poster.jpg", image_data, content_type)},
        )
        response.raise_for_status()
        uploaded = response.json()

    saved = await vk_api_call(
        "photos.saveMessagesPhoto",
        token,
        server=uploaded["server"],
        photo=uploaded["photo"],
        hash=uploaded["hash"],
    )
    photo = saved[0] if isinstance(saved, list) and saved else saved
    owner_id = photo.get("owner_id")
    photo_id = photo.get("id")
    if owner_id is None or photo_id is None:
        raise ValueError("VK не вернул идентификатор загруженной афиши")
    return f"photo{owner_id}_{photo_id}"
