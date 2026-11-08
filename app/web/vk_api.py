"""VK API клиент для историй сообществ и личных сообщений.

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


def _response_items(response) -> list[dict]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        items = response.get("groups") or response.get("items") or []
        return [item for item in items if isinstance(item, dict)]
    return []


async def get_group_story_eligibility(group_id: str, community_token: str) -> bool:
    """Return True only when VK reports a verified or trending community."""
    token = decrypt_token(community_token or "")
    if not token:
        return False
    try:
        response = await vk_api_call(
            "groups.getById",
            token,
            group_ids=str(group_id),
            fields="verified,trending",
        )
        return any(
            str(item.get("id")) == str(group_id)
            and (bool(int(item.get("verified") or 0)) or bool(int(item.get("trending") or 0)))
            for item in _response_items(response)
        )
    except (VKAPIError, TypeError, ValueError, KeyError) as e:
        logger.warning("Could not check VK story eligibility for group %s: %s", group_id, e)
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


async def post_to_group_story(
    group,
    image: bytes,
    *,
    link_url: str | None = None,
    link_text: str | None = None,
) -> bool:
    """Загрузить афишу в историю VK-сообщества и опубликовать её."""
    token = decrypt_token(group.community_token or "")
    if not token:
        logger.warning("Нет community token для VK-группы %s", group.group_id)
        return False
    if not image:
        logger.warning("Для истории VK не передана афиша сообщества %s", group.group_id)
        return False
    if bool(link_url) != bool(link_text):
        logger.warning("Для ссылки истории VK нужны оба параметра: link_url и link_text")
        return False

    try:
        params = {"group_id": int(group.group_id.lstrip("-"))}
        if link_url and link_text:
            params.update(link_url=link_url, link_text=link_text)
        upload_server = await vk_api_call("stories.getPhotoUploadServer", token, **params)
        upload_url = upload_server.get("upload_url")
        if not upload_url:
            raise VKAPIError("VK не вернул upload_url для истории")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                upload_url,
                files={"file0": ("poster.jpg", image, "image/jpeg")},
            )
            response.raise_for_status()
            upload_data = response.json()

        upload_result = upload_data.get("upload_result")
        if not upload_result and isinstance(upload_data.get("response"), dict):
            upload_result = upload_data["response"].get("upload_result")
        if not upload_result:
            raise VKAPIError("VK не вернул результат загрузки афиши")

        saved = await vk_api_call(
            "stories.save",
            token,
            upload_results_json=json.dumps(
                [{"upload_result": upload_result}], ensure_ascii=False
            ),
        )
        items = saved.get("items", []) if isinstance(saved, dict) else []
        if not items:
            raise VKAPIError("VK не подтвердил публикацию истории")
        story_id = items[0].get("id") if isinstance(items[0], dict) else None
        logger.info(
            "История опубликована в VK-группу %s%s",
            group.group_id,
            f" (story_id={story_id})" if story_id else "",
        )
        return True
    except (VKAPIError, httpx.HTTPError, ValueError, KeyError) as e:
        logger.error("Ошибка публикации истории в VK-группу %s: %s", group.group_id, e)
        return False
