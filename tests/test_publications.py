"""
Публикации события в цели — event_publications (#164).

VK-анонс публикуется фотоисторией сообщества с community token.
"""

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.core.models import EventPoster, PlatformType, SubscriptionTier
from app.core.services import UserService, EventService, VKGroupService
from app.web.vk_api import (
    update_community_event_widget,
)

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
    db_session.add(EventPoster(event_id=event.id, image_data=b"poster-bytes", content_type="image/jpeg"))
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
        event.id, PlatformType.telegram, "telegram_channel", group.group_id,
        created_by=owner.id, status="posted",
    )
    pubs = await event_svc.list_publications(event.id)
    assert len(pubs) == 1
    assert pubs[0].target_type == "telegram_channel"
    assert pubs[0].status == "posted"


async def test_add_publication_idempotent(db_session, owner_event_vk_group):
    owner, event, group = owner_event_vk_group
    event_svc = EventService(db_session)
    await event_svc.add_publication(event.id, PlatformType.telegram, "telegram_channel", group.group_id)
    # Повторная публикация в ту же цель — обновление, не дубликат
    await event_svc.add_publication(event.id, PlatformType.telegram, "telegram_channel", group.group_id, status="posted")
    assert len(await event_svc.list_publications(event.id)) == 1


async def test_remove_publication(db_session, owner_event_vk_group):
    owner, event, group = owner_event_vk_group
    event_svc = EventService(db_session)
    await event_svc.add_publication(event.id, PlatformType.telegram, "telegram_channel", group.group_id)
    pid = (await event_svc.list_publications(event.id))[0].id
    assert await event_svc.remove_publication(pid) is True
    assert await event_svc.list_publications(event.id) == []


# ─── VK API клиент ───────────────────────────────────────────────


class TestVKAPI:
    async def test_update_community_event_widget_uses_group_token_and_text_widget(self):
        group = SimpleNamespace(
            group_id="12345678",
            community_token=Fernet(TEST_KEY).encrypt(b"vk-secret-token").decode(),
        )
        event = SimpleNamespace(
            title="Событие для виджета",
            date=datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
            location="Москва",
            price=0,
        )
        with (
            patch("app.config.settings.vk_token_encryption_key", TEST_KEY),
            patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock) as call,
        ):
            await update_community_event_widget(group, event)
        assert call.await_args.args == ("appWidgets.update", "vk-secret-token")
        assert call.await_args.kwargs["type"] == "text"
        code = call.await_args.kwargs["code"]
        assert code.startswith("return {")
        assert "Событие для виджета" in code
        assert "Москва" in code

# ─── Web API ─────────────────────────────────────────────────────


class TestPublishWeb:
    async def test_update_vk_event_widget_uses_connected_community(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        await db_session.commit()
        from app.web.dependencies import CurrentUser, get_current_user
        test_app = db_client._transport.app
        test_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=owner.id,
            telegram_user_id="55",
            name="Organizer",
            is_super_admin=False,
            platform=PlatformType.vk,
        )
        with patch("app.web.routes.update_community_event_widget", new_callable=AsyncMock) as update:
            resp = await db_client.post(
                f"/api/admin/events/{event.id}/vk-widget",
                headers={"X-Skip-Auth": "1"},
                json={"vk_group_id": group.group_id},
            )
        test_app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert resp.json()["updated"] is True
        update.assert_awaited_once_with(group, event)
        publications = await EventService(db_session).list_publications(event.id)
        assert [(publication.target_type, publication.status) for publication in publications] == [
            ("vk_group_widget", "posted"),
        ]

    async def test_vk_publish_only_makes_event_available_in_ticketbot(self, db_client, db_session, owner_event_vk_group):
        owner, event, _group = owner_event_vk_group
        event.is_published = False
        await db_session.commit()

        from app.web.dependencies import CurrentUser, get_current_user
        test_app = db_client._transport.app
        test_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=owner.id,
            telegram_user_id="55",
            name="VK admin",
            is_super_admin=False,
            platform=PlatformType.vk,
        )
        resp = await db_client.post(
            f"/api/admin/events/{event.id}/publish",
            headers={"X-Skip-Auth": "1"},
        )
        test_app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json() == {
            "id": str(event.id),
            "is_published": True,
            "announced": False,
            "platform": "vk",
            "publication_types": [],
        }
        await db_session.refresh(event)
        assert event.is_published is True
        assert await EventService(db_session).list_publications(event.id) == []

    async def test_remove_publication(self, db_client, db_session, owner_event_vk_group):
        owner, event, _group = owner_event_vk_group
        event_svc = EventService(db_session)
        await event_svc.add_publication(
            event.id, PlatformType.telegram, "telegram_channel", "tg-channel-1",
            created_by=owner.id, status="posted",
        )
        await db_session.commit()
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
