"""
Публикации события в цели — event_publications (#164).

VK-анонс публикуется фотоисторией сообщества с community token.
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.core.models import EventPoster, PlatformType, SubscriptionTier, VKGroup
from app.core.services import UserService, EventService, VKGroupService
from app.web.vk_api import (
    VKAPIError,
    post_to_group_story,
    post_to_group_wall,
    verify_vk_user_and_group_admin,
    verify_vk_wall_post,
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
        event.id, PlatformType.vk, "vk_group_story", group.group_id,
        created_by=owner.id, status="posted",
    )
    pubs = await event_svc.list_publications(event.id)
    assert len(pubs) == 1
    assert pubs[0].target_type == "vk_group_story"
    assert pubs[0].status == "posted"


async def test_add_publication_idempotent(db_session, owner_event_vk_group):
    owner, event, group = owner_event_vk_group
    event_svc = EventService(db_session)
    await event_svc.add_publication(event.id, PlatformType.vk, "vk_group_story", group.group_id)
    # Повторная публикация в ту же цель — обновление, не дубликат
    await event_svc.add_publication(event.id, PlatformType.vk, "vk_group_story", group.group_id, status="posted")
    assert len(await event_svc.list_publications(event.id)) == 1


async def test_remove_publication(db_session, owner_event_vk_group):
    owner, event, group = owner_event_vk_group
    event_svc = EventService(db_session)
    await event_svc.add_publication(event.id, PlatformType.vk, "vk_group_story", group.group_id)
    pid = (await event_svc.list_publications(event.id))[0].id
    assert await event_svc.remove_publication(pid) is True
    assert await event_svc.list_publications(event.id) == []


# ─── VK API клиент ───────────────────────────────────────────────


class TestVKAPI:
    async def test_post_to_group_story_success(self):
        group = VKGroup(group_id="123", community_token="")
        class UploadResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"upload_result": "uploaded-story-hash"}

        class UploadClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, files):
                assert url == "https://upload.vk.test/story"
                assert files["file0"][1] == b"poster"
                return UploadResponse()

        api_responses = [
            {"upload_url": "https://upload.vk.test/story"},
            {"items": [{"id": 42}]},
        ]
        with (
            patch("app.config.settings.vk_token_encryption_key", TEST_KEY),
            patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock, side_effect=api_responses) as call,
            patch("app.web.vk_api.httpx.AsyncClient", side_effect=lambda **kwargs: UploadClient()),
        ):
            group.community_token = Fernet(TEST_KEY).encrypt(b"token").decode()
            assert await post_to_group_story(
                group,
                b"poster",
                link_url="https://vk.com/app123?event_id=event-1",
                link_text="ticket",
            ) is True
            assert [call_args.args[0] for call_args in call.await_args_list] == [
                "stories.getPhotoUploadServer", "stories.save"
            ]
            assert call.await_args_list[0].kwargs["group_id"] == 123
            assert call.await_args_list[0].kwargs["link_text"] == "ticket"
            assert "uploaded-story-hash" in call.await_args_list[1].kwargs["upload_results_json"]

    async def test_post_to_group_story_no_token(self):
        group = VKGroup(group_id="123", community_token="")
        assert await post_to_group_story(group, b"poster") is False

    async def test_post_to_group_story_api_error(self):
        group = VKGroup(group_id="123", community_token="")
        with (
            patch("app.config.settings.vk_token_encryption_key", TEST_KEY),
            patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock, side_effect=VKAPIError("boom")),
        ):
            group.community_token = Fernet(TEST_KEY).encrypt(b"token").decode()
            assert await post_to_group_story(group, b"poster") is False

    async def test_verify_vk_user_token_checks_identity_and_admin(self):
        with patch(
            "app.web.vk_api.vk_api_call",
            new_callable=AsyncMock,
            side_effect=[[{"id": 55}], {"groups": [{"id": 123, "is_admin": 1}]}],
        ) as call:
            assert await verify_vk_user_and_group_admin("55", "123", "user-token") is True
            assert [args.args[0] for args in call.await_args_list] == ["users.get", "groups.getById"]
            assert call.await_args_list[1].kwargs["fields"] == "is_admin,admin_level,verified,trending,wall"

        with patch(
            "app.web.vk_api.vk_api_call",
            new_callable=AsyncMock,
            return_value=[{"id": 999}],
        ):
            assert await verify_vk_user_and_group_admin("55", "123", "other-user-token") is False

    async def test_verify_manual_wall_post_checks_community_and_post_id(self):
        with (
            patch("app.web.vk_api.verify_vk_user_and_group_admin", new_callable=AsyncMock, return_value=True),
            patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock, return_value={
                "items": [{"id": 88, "owner_id": -123}],
            }) as call,
        ):
            assert await verify_vk_wall_post("55", "123", 88, "user-token") is True
            assert call.await_args.args[0] == "wall.getById"
            assert call.await_args.kwargs["posts"] == "-123_88"

    async def test_post_to_group_wall_uploads_photo_and_posts_as_community(self):
        class UploadResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"server": 1, "photo": "uploaded-photo", "hash": "upload-hash"}

        class UploadClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, files):
                assert url == "https://upload.vk.test/wall"
                assert files["photo"][1] == b"poster"
                return UploadResponse()

        api_responses = [
            {"upload_url": "https://upload.vk.test/wall"},
            [{"id": 77, "owner_id": -123}],
            {"post_id": 88},
        ]
        with (
            patch("app.web.vk_api.vk_api_call", new_callable=AsyncMock, side_effect=api_responses) as call,
            patch("app.web.vk_api.httpx.AsyncClient", side_effect=lambda **kwargs: UploadClient()),
        ):
            assert await post_to_group_wall("123", "user-token", b"poster", "Event post") is True
        assert [args.args[0] for args in call.await_args_list] == [
            "photos.getWallUploadServer", "photos.saveWallPhoto", "wall.post"
        ]
        wall_args = call.await_args_list[-1].kwargs
        assert wall_args["owner_id"] == -123
        assert wall_args["from_group"] == 1
        assert wall_args["attachments"] == "photo-123_77"


# ─── Web API ─────────────────────────────────────────────────────


class TestPublishWeb:
    async def test_publish_to_vk_group_creates_publication(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        await db_session.commit()

        from app.core.models import PlatformType
        from app.web.dependencies import CurrentUser, get_current_user
        test_app = db_client._transport.app
        test_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=owner.id,
            telegram_user_id="55",
            name="VK admin",
            is_super_admin=False,
            platform=PlatformType.vk,
        )
        with (
            patch("app.web.routes.verify_vk_user_and_group_admin", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.post_to_group_wall", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.get_group_story_eligibility", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.post_to_group_story", new_callable=AsyncMock, return_value=True),
        ):
            resp = await db_client.post(
                f"/api/admin/events/{event.id}/publish",
                headers={"X-Skip-Auth": "1"},
                json={"vk_group_id": group.group_id, "vk_user_access_token": "temporary-user-token"},
            )
        test_app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert data["announced"] is True
        assert data["platform"] == "vk"
        assert data["publication_types"] == ["wall", "story"]
        assert data["group_id"] == group.group_id

        # Запись публикации создана
        resp = await db_client.get(
            f"/api/admin/events/{event.id}/publications", headers={"X-Skip-Auth": "1"},
        )
        pubs = resp.json()["publications"]
        assert {(pub["target_type"], pub["status"]) for pub in pubs} == {
            ("vk_group_wall", "posted"), ("vk_group_story", "posted")
        }

    async def test_publish_to_vk_group_marks_story_fallback_for_manual_wall(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        await db_session.commit()

        with patch("app.web.routes.get_group_story_eligibility", new_callable=AsyncMock, return_value=False):
            resp = await db_client.post(
                f"/api/admin/events/{event.id}/publish",
                headers={"X-Skip-Auth": "1"},
                json={"vk_group_id": group.group_id},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["announced"] is False
        assert data["manual_fallback"] is True
        assert data["manual_post"]["owner_id"] == -int(group.group_id)
        assert event.title in data["manual_post"]["message"]

    async def test_record_manual_vk_publication_requires_vk_confirmation(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
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
        with patch("app.web.routes.verify_vk_wall_post", new_callable=AsyncMock, return_value=True):
            resp = await db_client.post(
                f"/api/admin/events/{event.id}/vk-publications/manual",
                headers={"X-Skip-Auth": "1"},
                json={
                    "vk_group_id": group.group_id,
                    "post_id": 88,
                    "vk_user_access_token": "temporary-user-token",
                },
            )
        test_app.dependency_overrides.clear()
        assert resp.status_code == 200
        publications = (await EventService(db_session).list_publications(event.id))
        assert len(publications) == 1
        assert publications[0].target_type == "vk_group_wall"
        assert publications[0].status == "posted"

    async def test_publish_to_vk_group_requires_poster(self, db_client, db_session, owner_event_vk_group):
        owner, event, group = owner_event_vk_group
        poster = await db_session.get(EventPoster, event.id)
        await db_session.delete(poster)
        await db_session.commit()

        resp = await db_client.post(
            f"/api/admin/events/{event.id}/publish",
            headers={"X-Skip-Auth": "1"},
            json={"vk_group_id": group.group_id},
        )

        assert resp.status_code == 409
        assert "афишу" in resp.json()["detail"]

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
        with (
            patch("app.web.routes.get_group_story_eligibility", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.post_to_group_story", new_callable=AsyncMock, return_value=True),
        ):
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
