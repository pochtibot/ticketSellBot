"""
VK-группы как цели публикации (#163): self-service организатора.

- community token шифруется (Fernet, settings.vk_token_encryption_key).
- group_id уникален глобально; анти-захват (группа другого организатора → ошибка).
- Эндпоинты /api/me/vk-groups (GET/POST/DELETE), токен в ответ не попадает.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.core.models import PlatformType
from app.core.services import UserService, VKGroupService
from app.core.crypto import encrypt_token, decrypt_token

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture
def crypto_key():
    with patch("app.config.settings.vk_token_encryption_key", TEST_KEY):
        yield


# ─── crypto helper ───────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip(crypto_key):
    enc = encrypt_token("vk-community-secret-123")
    assert enc != "vk-community-secret-123"
    assert decrypt_token(enc) == "vk-community-secret-123"


def test_encrypt_requires_key():
    with patch("app.config.settings.vk_token_encryption_key", None):
        with pytest.raises(ValueError):
            encrypt_token("secret")


# ─── DM билета в ЛС VK (messages.send от группы организатора) ────


async def test_send_vk_ticket_dm_success(db_session, vk_owner, crypto_key):
    """Билет отправляется в ЛС VK покупателю от имени группы."""
    from app.core.services import VKGroupService
    from app.web.vk_api import send_vk_ticket_dm

    group_svc = VKGroupService(db_session)
    group = await group_svc.register_vk_group(
        vk_owner.id, "7777777", title="Группа", community_token="vk-secret",
    )

    with patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock) as mock_call:
        ok = await send_vk_ticket_dm(
            vk_user_id="5305539", text="🎫 Ваш билет: AB3X-K7M9", group=group,
        )
    assert ok is True
    mock_call.assert_awaited_once()
    args, kwargs = mock_call.await_args
    assert args[0] == "messages.send"
    assert kwargs["peer_id"] == 5305539
    assert "Ваш билет" in kwargs["message"]


async def test_send_vk_ticket_dm_no_token(db_session, vk_owner, crypto_key):
    """Без community token — DM не отправляется (False, не ошибка)."""
    from app.core.services import VKGroupService
    from app.web.vk_api import send_vk_ticket_dm

    group_svc = VKGroupService(db_session)
    group = await group_svc.register_vk_group(vk_owner.id, "8888888", title="Без токена")

    with patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock) as mock_call:
        ok = await send_vk_ticket_dm("5305539", "text", group=group)
    assert ok is False
    mock_call.assert_not_awaited()


async def test_send_vk_ticket_dm_api_error(db_session, vk_owner, crypto_key):
    """Ошибка VK API при messages.send → False (тихо, билет остаётся в кабинете)."""
    from app.core.services import VKGroupService
    from app.web.vk_api import send_vk_ticket_dm, VKAPIError

    group_svc = VKGroupService(db_session)
    group = await group_svc.register_vk_group(
        vk_owner.id, "9999999", title="Группа", community_token="vk-secret",
    )

    with patch(
        "app.web.vk_api.vk_api_call",
        new_callable=AsyncMock,
        side_effect=VKAPIError("messages.send error 901: Cannot send messages for user without permission"),
    ):
        ok = await send_vk_ticket_dm("5305539", "text", group=group)
    assert ok is False


# ─── Сервисный слой ──────────────────────────────────────────────


@pytest_asyncio.fixture
async def vk_owner(db_session):
    user_svc = UserService(db_session)
    owner = await user_svc.get_or_create(PlatformType.telegram, "vk_owner_123", name="Организатор VK")
    return owner


async def test_register_encrypts_token(db_session, vk_owner, crypto_key):
    group_svc = VKGroupService(db_session)
    group = await group_svc.register_vk_group(
        vk_owner.id, "12345678", title="Моя группа", community_token="super-secret-token",
    )
    # В БД токен зашифрован
    assert group.community_token != "super-secret-token"
    assert "super-secret-token" not in (group.community_token or "")
    # Расшифровка возвращает оригинал
    assert decrypt_token(group.community_token) == "super-secret-token"


async def test_register_without_token_ok(db_session, vk_owner, crypto_key):
    group_svc = VKGroupService(db_session)
    group = await group_svc.register_vk_group(vk_owner.id, "111", title="Без токена")
    assert group.community_token is None


async def test_register_antihijack(db_session, vk_owner, crypto_key):
    group_svc = VKGroupService(db_session)
    other = await UserService(db_session).get_or_create(PlatformType.telegram, "other_vk_owner")
    await group_svc.register_vk_group(vk_owner.id, "999", title="Моя")

    with pytest.raises(ValueError):
        await group_svc.register_vk_group(other.id, "999", title="Чужая")


async def test_register_idempotent_updates_token(db_session, vk_owner, crypto_key):
    group_svc = VKGroupService(db_session)
    await group_svc.register_vk_group(vk_owner.id, "555", title="A")
    group = await group_svc.register_vk_group(
        vk_owner.id, "555", title="A2", community_token="new-token",
    )
    assert group.title == "A2"
    assert decrypt_token(group.community_token) == "new-token"


async def test_list_and_remove(db_session, vk_owner, crypto_key):
    group_svc = VKGroupService(db_session)
    await group_svc.register_vk_group(vk_owner.id, "222", title="Группа 1")
    await group_svc.register_vk_group(vk_owner.id, "333", title="Группа 2")

    groups = await group_svc.list_vk_groups(vk_owner.id)
    assert {g.group_id for g in groups} == {"222", "333"}

    assert await group_svc.remove_vk_group(vk_owner.id, "222") is True
    assert await group_svc.remove_vk_group(vk_owner.id, "222") is False
    assert {g.group_id for g in await group_svc.list_vk_groups(vk_owner.id)} == {"333"}


# ─── Web API ─────────────────────────────────────────────────────


class TestVKGroupsWeb:
    async def test_register_and_list(self, db_client, db_session):
        user_svc = UserService(db_session)
        user = await user_svc.get_or_create(PlatformType.telegram, "12345", name="Dev")
        await db_session.commit()

        with (
            patch("app.config.settings.vk_token_encryption_key", TEST_KEY),
            patch("app.web.routes.verify_group_token", new_callable=AsyncMock, return_value=True),
        ):
            resp = await db_client.post(
                "/api/me/vk-groups",
                headers={"X-Skip-Auth": "1"},
                json={"group_id": "888", "title": "Моя группа", "community_token": "token-888"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["group_id"] == "888"
            assert data["has_token"] is True
            assert "community_token" not in data

            resp = await db_client.get("/api/me/vk-groups", headers={"X-Skip-Auth": "1"})
            assert resp.status_code == 200
            groups = resp.json()
            assert len(groups) == 1
            assert groups[0]["has_token"] is True

    async def test_token_not_stored_in_plain(self, db_client, db_session):
        user_svc = UserService(db_session)
        user = await user_svc.get_or_create(PlatformType.telegram, "12345", name="Dev")
        await db_session.commit()

        with (
            patch("app.config.settings.vk_token_encryption_key", TEST_KEY),
            patch("app.web.routes.verify_group_token", new_callable=AsyncMock, return_value=True),
        ):
            await db_client.post(
                "/api/me/vk-groups",
                headers={"X-Skip-Auth": "1"},
                json={"group_id": "777", "community_token": "plain-secret"},
            )
            # Проверяем в БД — токен зашифрован (внутри with, ключ активен)
            group_svc = VKGroupService(db_session)
            group = await group_svc.get_by_group_id("777")
            assert group.community_token != "plain-secret"
            assert decrypt_token(group.community_token) == "plain-secret"

    async def test_remove(self, db_client, db_session):
        user_svc = UserService(db_session)
        await user_svc.get_or_create(PlatformType.telegram, "12345", name="Dev")
        with patch("app.config.settings.vk_token_encryption_key", TEST_KEY):
            await db_client.post(
                "/api/me/vk-groups",
                headers={"X-Skip-Auth": "1"},
                json={"group_id": "666", "title": "Удаляемая"},
            )
            resp = await db_client.delete("/api/me/vk-groups/666", headers={"X-Skip-Auth": "1"})
            assert resp.status_code == 200
            resp = await db_client.get("/api/me/vk-groups", headers={"X-Skip-Auth": "1"})
            assert resp.json() == []

    async def test_register_empty_group_id_400(self, db_client):
        resp = await db_client.post(
            "/api/me/vk-groups",
            headers={"X-Skip-Auth": "1"},
            json={"group_id": "   "},
        )
        assert resp.status_code == 400

    async def test_register_token_rejected_if_not_verified(self, db_client, db_session):
        """Токен, не подтверждённый VK API (чужой/битый), → 400."""
        user_svc = UserService(db_session)
        await user_svc.get_or_create(PlatformType.telegram, "12345", name="Dev")
        await db_session.commit()

        with patch(
            "app.web.routes.verify_group_token", new_callable=AsyncMock, return_value=False
        ):
            resp = await db_client.post(
                "/api/me/vk-groups",
                headers={"X-Skip-Auth": "1"},
                json={"group_id": "424242", "community_token": "bad-token"},
            )
        assert resp.status_code == 400

    async def test_register_token_accepted_when_verified(self, db_client, db_session, crypto_key):
        """Токен, подтверждённый VK API (принадлежит группе), → 201."""
        user_svc = UserService(db_session)
        await user_svc.get_or_create(PlatformType.telegram, "12345", name="Dev")
        await db_session.commit()

        with patch(
            "app.web.routes.verify_group_token", new_callable=AsyncMock, return_value=True
        ):
            resp = await db_client.post(
                "/api/me/vk-groups",
                headers={"X-Skip-Auth": "1"},
                json={"group_id": "424243", "community_token": "good-token"},
            )
        assert resp.status_code == 201
