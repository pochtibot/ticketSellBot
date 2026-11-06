"""
Публикации события в цели — event_publications (#164).

Одно мероприятие публикуется в N мест: TG-канал / VK-группа (стена).
Постинг в VK — через community token (wall.post).
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.core.models import PlatformType, SubscriptionTier, VKGroup
from app.core.services import UserService, EventService, VKGroupService
from app.web.vk_api import VKAPIError, post_to_group_wall

TEST_KEY = Fernet.generate_key().decode()


@pytest_asyncio.fixture
async def owner_event_vk_group(db_session):
    """Организатор (12345) + его мероприятие + VK-группа с зашифрованным токеном."""
    user_svc = UserService(db_session)
    owner = await user_svc.get_or_create(PlatformType.telegram, "12345", name="Организатор")
    await user_svc.activate_subscription(owner.id, days=30, tier=SubscriptionTier.pro)

    event_svc = EventService(db_session)
    event = await event_svc.create(
        title="Событие для публикации",
        description="",
        date=datetime.now(timezone.utc) + timedelta(days=7),
        location="Москва",
        price=0,
        total_tickets=50,
        channel_id=None,
        owner_user_id=owner.id,
    )
    event.is_published = True
    await db_session.flush()

    with patch("app.config.settings.vk_token_encryption_key", TEST_KEY):
        group_svc = VKGroupService(db_session)
        group = await group_svc.register_vk_group(
            owner.id, "12345678", title="Группа", community_token="vk-secret-token",
        )
    await db_session.flush()
    return owner, event, group


# ─── Сервисный слой: event_publications ──────────────────────────


async def test_add_publication_and_list(db_session, owner_event_vk_group):
    owner, event, group = owner_event_vk_group
    event_svc = EventService(db_session)

    await event_svc.add_publication(
        event.id, PlatformType.vk, "vk_group_wall", group.group_id,
        created_by=owner.id, status="posted",
    )
    pubs = await event_svc.list_publications(event.id)
    assert len(pubs) == 1
    assert pubs[0].target_type == "vk_group_wall"
    assert pubs[0].status == "posted"


async def test_add_publication_idempotent(db_session, owner_event_vk_group):
    owner, event, group = owner_event_vk_group
    event_svc = EventService(db_session)
    await event_svc.add_publication(event.id, PlatformType.vk, "vk_group_wall", group.group_id)
    # Повторная публикация в ту же цель — обновление, не дубликат
    await event_svc.add_publication(event.id, PlatformType.vk, "vk_group_wall", group.group_id, status="posted")
    assert len(await event_svc.list_publications(event.id)) == 1


async def test_remove_publication(db_session, owner_event_vk_group):
    owner, event, group = owner_event_vk_group
    event_svc = EventService(db_session)
    await event_svc.add_publication(event.id, PlatformType.vk, "vk_group_wall", group.group_id)
    pid = (await event_svc.list_publications(event.id))[0].id
    assert await event_svc.remove_publication(pid) is True
    assert await event_svc.list_publications(event.id) == []


# ─── VK API клиент ───────────────────────────────────────────────


class TestVKAPI:
    async def test_post_to_group_wall_success(self):
        group = VKGroup(group_id="123", community_token="")
        with (
            patch("app.config.settings.vk_token_encryption_key", TEST_KEY),
            patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock, return_value={"post_id": 1}) as call,
        ):
            group.community_token = Fernet(TEST_KEY).encrypt(b"token").decode()
            assert await post_to_group_wall(group, "Анонс") is True
            call.assert_awaited_once()
            kwargs = call.await_args
            assert kwargs.args[0] == "wall.post"
            assert kwargs.kwargs["owner_id"] == "-123"
            assert kwargs.kwargs["from_group"] == 1

    async def test_post_to_group_wall_no_token(self):
        group = VKGroup(group_id="123", community_token="")
        assert await post_to_group_wall(group, "Анонс") is False

    async def test_post_to_group_wall_api_error(self):
        group = VKGroup(group_id="123", community_token="")
        with (
            patch("app.config.settings.vk_token_encryption_key", TEST_KEY),
            patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock, side_effect=VKAPIError("boom")),
        ):
            group.community_token = Fernet(TEST_KEY).encrypt(b"token").decode()
            assert await post_to_group_wall(group, "Анонс") is False


# ─── Web API ─────────────────────────────────────────────────────


class TestPublishWeb:
    async def test_publish_to_vk_group_creates_publication(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        await db_session.commit()

        with patch("app.web.routes.post_to_group_wall", new_callable=AsyncMock, return_value=True):
            resp = await db_client.post(
                f"/api/admin/events/{event.id}/publish",
                headers={"X-Skip-Auth": "1"},
                json={"vk_group_id": group.group_id},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["announced"] is True
        assert data["platform"] == "vk"
        assert data["group_id"] == group.group_id

        # Запись публикации создана
        resp = await db_client.get(
            f"/api/admin/events/{event.id}/publications", headers={"X-Skip-Auth": "1"},
        )
        pubs = resp.json()["publications"]
        assert len(pubs) == 1
        assert pubs[0]["target_type"] == "vk_group_wall"
        assert pubs[0]["status"] == "posted"

    async def test_publish_to_foreign_vk_group_403(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        # Чужая группа (другой владелец)
        user_svc = UserService(db_session)
        other = await user_svc.get_or_create(PlatformType.telegram, "other_group_owner")
        with patch("app.config.settings.vk_token_encryption_key", TEST_KEY):
            group_svc = VKGroupService(db_session)
            foreign = await group_svc.register_vk_group(other.id, "999999", title="Чужая")
        await db_session.commit()

        resp = await db_client.post(
            f"/api/admin/events/{event.id}/publish",
            headers={"X-Skip-Auth": "1"},
            json={"vk_group_id": foreign.group_id},
        )
        assert resp.status_code == 403

    async def test_publish_to_unknown_vk_group_404(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        await db_session.commit()
        resp = await db_client.post(
            f"/api/admin/events/{event.id}/publish",
            headers={"X-Skip-Auth": "1"},
            json={"vk_group_id": "no_such_group"},
        )
        assert resp.status_code == 404

    async def test_publish_both_targets_400(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        await db_session.commit()
        resp = await db_client.post(
            f"/api/admin/events/{event.id}/publish",
            headers={"X-Skip-Auth": "1"},
            json={"vk_group_id": group.group_id, "channel_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 400

    async def test_remove_publication(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        await db_session.commit()
        with patch("app.web.routes.post_to_group_wall", new_callable=AsyncMock, return_value=True):
            await db_client.post(
                f"/api/admin/events/{event.id}/publish",
                headers={"X-Skip-Auth": "1"},
                json={"vk_group_id": group.group_id},
            )
        resp = await db_client.get(
            f"/api/admin/events/{event.id}/publications", headers={"X-Skip-Auth": "1"},
        )
        pid = resp.json()["publications"][0]["id"]
        resp = await db_client.delete(
            f"/api/admin/events/{event.id}/publications/{pid}", headers={"X-Skip-Auth": "1"},
        )
        assert resp.status_code == 200
        resp = await db_client.get(
            f"/api/admin/events/{event.id}/publications", headers={"X-Skip-Auth": "1"},
        )
        assert resp.json()["publications"] == []
