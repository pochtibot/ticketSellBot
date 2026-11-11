"""Event placement records and VK Mini App publishing behavior."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from cryptography.fernet import Fernet

from app.core.models import EventPoster, PlatformType, SubscriptionTier
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

    async def test_vk_publish_sends_announcement_to_organizer_dm(
        self, db_client, db_session, owner_event_vk_group, monkeypatch
    ):
        owner, event, group = owner_event_vk_group
        monkeypatch.setattr("app.web.routes.settings.vk_app_id", 54698875)
        db_session.add(EventPoster(event_id=event.id, image_data=b"poster", content_type="image/jpeg"))
        await db_session.commit()
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
        assert resp.json() == {"sent": True, "group_id": group.group_id}
        assert send_dm.await_args.args[0] == "5305539"
        message = send_dm.await_args.args[1]
        assert message.startswith("📣 АНОНС ГОТОВ\n\n🎟 ")
        assert "📅 " in message
        assert "📍 " in message
        assert "💳 " in message
        assert "Страница мероприятия — по кнопке ниже." in message
        keyboard = send_dm.await_args.kwargs["keyboard"]
        assert send_dm.await_args.kwargs["poster_data"] == b"poster"
        assert send_dm.await_args.kwargs["poster_content_type"] == "image/jpeg"
        button = keyboard["buttons"][0][0]
        assert button["action"]["type"] == "open_app"
        assert button["action"]["label"] == "Открыть мероприятие"
        assert button["action"]["app_id"] == 54698875
        assert button["action"]["owner_id"] == -int(group.group_id)
        assert button["action"]["hash"] == f"event_id={event.id}"
        assert "color" not in button
        publications = await EventService(db_session).list_publications(event.id)
        assert len(publications) == 1
        assert publications[0].target_type == "vk_organizer_dm"

    async def test_vk_broadcast_audience_preview_uses_owned_group_subscribers(
        self, db_client, owner_event_vk_group
    ):
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
            "app.web.routes.get_vk_newsletter_users",
            new_callable=AsyncMock,
            return_value=["101", "102", "103"],
        ) as get_users:
            response = await db_client.get(
                f"/api/admin/events/{event.id}/vk-broadcast-audience?group_id={group.group_id}",
                headers={"X-Skip-Auth": "1"},
            )
        test_app.dependency_overrides.clear()

        assert response.status_code == 200, response.text
        assert response.json() == {
            "group_id": group.group_id,
            "group_title": "Группа",
            "intent": "promo_newsletter",
            "subscribers": 3,
        }
        get_users.assert_awaited_once_with(group)

    async def test_vk_broadcast_sends_event_to_newsletter_subscribers(
        self, db_client, db_session, owner_event_vk_group, monkeypatch
    ):
        owner, event, group = owner_event_vk_group
        monkeypatch.setattr("app.web.routes.settings.vk_app_id", 54698875)
        from app.web.dependencies import CurrentUser, get_current_user
        test_app = db_client._transport.app
        test_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=owner.id,
            telegram_user_id="5305539",
            name="VK organizer",
            is_super_admin=False,
            platform=PlatformType.vk,
        )
        with (
            patch("app.web.routes.get_vk_newsletter_users", new_callable=AsyncMock, return_value=["101", "102"]),
            patch(
                "app.web.routes.send_vk_newsletter",
                new_callable=AsyncMock,
                return_value=(2, 0),
            ) as send_newsletter,
        ):
            response = await db_client.post(
                f"/api/admin/events/{event.id}/vk-broadcast",
                json={"group_id": group.group_id},
                headers={"X-Skip-Auth": "1"},
            )
        test_app.dependency_overrides.clear()

        assert response.status_code == 200, response.text
        assert response.json() == {"sent": 2, "failed": 0, "total": 2, "group_id": group.group_id}
        args, kwargs = send_newsletter.await_args
        assert args[:2] == (group, ["101", "102"])
        assert "Событие для публикации" in args[2]
        assert kwargs["intent"] == "promo_newsletter"
        button = kwargs["keyboard"]["buttons"][0][0]["action"]
        assert button["type"] == "open_app"
        assert button["hash"] == f"event_id={event.id}"

    async def test_vk_broadcast_rejects_group_not_owned_by_event_organizer(
        self, db_client, db_session, owner_event_vk_group
    ):
        _owner, event, _group = owner_event_vk_group
        other_user = await UserService(db_session).get_or_create(
            PlatformType.telegram, "99999", name="Other organizer",
        )
        from app.web.dependencies import CurrentUser, get_current_user
        test_app = db_client._transport.app
        test_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=other_user.id,
            telegram_user_id="5305539",
            name="Other organizer",
            is_super_admin=False,
            platform=PlatformType.vk,
        )
        response = await db_client.get(
            f"/api/admin/events/{event.id}/vk-broadcast-audience?group_id=12345678",
            headers={"X-Skip-Auth": "1"},
        )
        test_app.dependency_overrides.clear()
        assert response.status_code == 403

    async def test_vk_organizer_announcement_sends_again_on_each_request(
        self, db_client, db_session, owner_event_vk_group
    ):
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

        with patch(
            "app.web.routes.send_vk_organizer_announcement_dm",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_dm:
            responses = [
                await db_client.post(
                    f"/api/admin/events/{event.id}/send-vk-announcement",
                    headers={"X-Skip-Auth": "1"},
                )
                for _ in range(2)
            ]
        test_app.dependency_overrides.clear()

        assert all(resp.status_code == 200 for resp in responses)
        assert all(
            resp.json() == {"sent": True, "group_id": group.group_id}
            for resp in responses
        )
        assert send_dm.await_count == 2
        publications = await EventService(db_session).list_publications(event.id)
        assert len(publications) == 1

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
