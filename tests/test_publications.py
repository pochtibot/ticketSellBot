"""Event placement records and VK Mini App publishing behavior."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from cryptography.fernet import Fernet

from app.core.models import PlatformType, SubscriptionTier
from app.core.services import UserService, EventService, VKGroupService
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


class TestPublishWeb:
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
            "vk_group_id": "12345678",
        }
        await db_session.refresh(event)
        assert event.is_published is True
        assert await EventService(db_session).list_publications(event.id) == []

    async def test_vk_publish_sends_announcement_to_organizer_dm(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        from app.web.dependencies import CurrentUser, get_current_user
        test_app = db_client._transport.app
        test_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=owner.id,
            telegram_user_id="5305539",
            name="VK organizer",
            is_super_admin=False,
            platform=PlatformType.vk,
        )
        with patch(
            "app.web.routes.send_vk_organizer_announcement_dm",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_dm:
            resp = await db_client.post(
                f"/api/admin/events/{event.id}/send-vk-announcement",
                headers={"X-Skip-Auth": "1"},
            )
            test_app.dependency_overrides.clear()

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"sent": True, "already_sent": False, "group_id": group.group_id}
        assert send_dm.await_args.args[0] == "5305539"
        assert "Событие для публикации" in send_dm.await_args.args[1]
        publications = await EventService(db_session).list_publications(event.id)
        assert len(publications) == 1
        assert publications[0].target_type == "vk_organizer_dm"

    async def test_vk_organizer_announcement_is_not_sent_twice(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        from app.web.dependencies import CurrentUser, get_current_user
        test_app = db_client._transport.app
        test_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=owner.id,
            telegram_user_id="5305539",
            name="VK organizer",
            is_super_admin=False,
            platform=PlatformType.vk,
        )
        service = EventService(db_session)
        await service.add_publication(
            event_id=event.id,
            platform=PlatformType.vk,
            target_type="vk_organizer_dm",
            target_id=f"{group.group_id}:5305539",
        )
        await db_session.commit()

        with patch("app.web.routes.send_vk_organizer_announcement_dm", new_callable=AsyncMock) as send_dm:
            resp = await db_client.post(
                f"/api/admin/events/{event.id}/send-vk-announcement",
                headers={"X-Skip-Auth": "1"},
            )
        test_app.dependency_overrides.clear()

        assert resp.status_code == 200, resp.text
        assert resp.json()["already_sent"] is True
        send_dm.assert_not_awaited()

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
