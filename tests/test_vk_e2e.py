"""
Полный контур VK (#166): сквозной сценарий организатора через web.

Киллер-фича: одно мероприятие на всех площадках.
Сценарий:
1. Организатор (канон) в TG: подписка + код привязки.
2. VK-пользователь вводит код → вход по VK ведёт на канон организатора.
3. Организатор добавляет VK-группу (self-service, token шифруется).
4. Создаёт мероприятие (owner) + соработника (менеджера).
5. Публикует анонс в VK-группу (стена) — запись event_publications.
6. Покупатель (VK, отдельный аккаунт) покупает билет.
7. Организатор проверяет билет по коду (проверил в TG, куплено в VK) —
   киллер-фича: check-in по коду работает с любой площадки.
"""

import uuid
import base64
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from cryptography.fernet import Fernet

from app.core.models import PlatformType, SubscriptionTier, Event
from app.core.services import UserService, EventService, VKGroupService, TicketService

TEST_KEY = Fernet.generate_key().decode()


def _vk_auth_header(user_id=5305539, app_id=123456, secret="test_vk_secret_key"):
    """Валидный X-VK-Init-Data (launch params + подпись) для аутентификации VK-покупателя."""
    from app.web.vk_auth import compute_sign

    params = {
        "vk_app_id": str(app_id),
        "vk_user_id": str(user_id),
        "vk_ts": str(int(time.time())),
        "vk_ref": "catalog",
    }
    sign = compute_sign(params, secret)
    params["sign"] = sign
    query = urlencode(sorted(params.items()))
    return base64.b64encode(query.encode()).decode()


@contextmanager
def _vk_settings(app_id=123456, secret="test_vk_secret_key"):
    """Активные VK Mini App настройки для X-VK-Init-Data аутентификации."""
    with (
        patch("app.web.vk_auth.settings.vk_app_id", app_id),
        patch("app.web.vk_auth.settings.vk_secret_key", secret),
    ):
        yield


async def _publish(db_session, event_id: uuid.UUID):
    """Опубликовать мероприятие (черновик → продажа)."""
    ev = await db_session.get(Event, event_id)
    ev.is_published = True
    ev.is_active = True


async def test_full_vk_organizer_cycle(db_client, db_session):
    user_svc = UserService(db_session)
    event_svc = EventService(db_session)
    group_svc = VKGroupService(db_session)
    ticket_svc = TicketService(db_session)

    # ── 1. Организатор (канон 12345 — совпадает с X-Skip-Auth) ──
    org = await user_svc.get_or_create(PlatformType.telegram, "12345", name="Организатор")
    await user_svc.activate_subscription(org.id, days=30, tier=SubscriptionTier.pro)
    code = await user_svc.create_link_code(org.id, PlatformType.vk, ttl_minutes=10)

    # ── 2. VK-пользователь привязывается → канон организатора ──
    await user_svc.consume_link_code(code, PlatformType.vk, "vk_buyer_uid", current_user_id=None)
    # Покупатель (другой VK-пользователь, не привязан) — раздельный аккаунт.
    # Числовой VK ID, чтобы аутентифицироваться как покупатель через launch params (X-VK-Init-Data).
    buyer = await user_svc.get_or_create(PlatformType.vk, "7777777", name="Покупатель VK")
    assert buyer.id != org.id

    # ── 3. VK-группа (self-service, token шифруется) ──
    with patch("app.config.settings.vk_token_encryption_key", TEST_KEY):
        group = await group_svc.register_vk_group(
            org.id, "100500", title="Группа продаж", community_token="vk-secret",
        )

    # ── 4. Мероприятие (owner) + соработник ──
    event = await event_svc.create(
        title="Событие VK",
        description="Анонс во все площадки",
        date=datetime.now(timezone.utc) + timedelta(days=7),
        location="Москва",
        price=0,
        total_tickets=20,
        channel_id=None,
        owner_user_id=org.id,
    )
    event.is_published = True
    mgr = await user_svc.get_or_create(PlatformType.telegram, "vk_mgr", name="Менеджер")
    await event_svc.add_manager(event.id, mgr.id)
    await db_session.commit()

    # ── 5. Публикация в VK-группу (стена) через web ──
    with patch("app.web.routes.post_to_group_wall", new_callable=AsyncMock, return_value=True):
        resp = await db_client.post(
            f"/api/admin/events/{event.id}/publish",
            headers={"X-Skip-Auth": "1"},
            json={"vk_group_id": group.group_id},
        )
        assert resp.status_code == 200
        assert resp.json()["announced"] is True

    resp = await db_client.get(
        f"/api/admin/events/{event.id}/publications", headers={"X-Skip-Auth": "1"},
    )
    pubs = resp.json()["publications"]
    assert any(p["target_type"] == "vk_group_wall" and p["status"] == "posted" for p in pubs)

    # ── 6. Покупатель из VK покупает билет ──
    ticket = await ticket_svc.buy_ticket(buyer.id, event.id)
    assert ticket.validation_code is not None
    await db_session.commit()

    # ── 6b. F1: билет в ЛС VK — POST /tickets/{id}/send-vk.
    #        Реальная VK-группа (шаг 3) + реальная принадлежность билета.
    #        Отправка в VK (messages.send) замокана — сети в тесте нет. ──
    vk_header = _vk_auth_header(user_id=7777777)
    with (
        patch("app.web.vk_auth.settings.vk_app_id", 123456),
        patch("app.web.vk_auth.settings.vk_secret_key", "test_vk_secret_key"),
        patch("app.web.routes.send_vk_ticket_dm", new_callable=AsyncMock, return_value=True),
    ):
        resp = await db_client.post(
            f"/api/tickets/{ticket.id}/send-vk",
            headers={"X-VK-Init-Data": vk_header},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sent"] is True, resp.json()
    assert resp.json()["group_id"] == group.group_id, resp.json()

    # ── 7. Статистика учитывает проданный билет (куплен в VK) ──
    resp = await db_client.get(f"/api/admin/events/{event.id}/stats", headers={"X-Skip-Auth": "1"})
    assert resp.status_code == 200
    assert resp.json()["sold"] == 1

    # ── 8. Киллер-фича: проверка билета по коду (TG/web), купленного в VK ──
    resp = await db_client.get(
        f"/api/admin/tickets/validate?code={ticket.validation_code}",
        headers={"X-Skip-Auth": "1"},
    )
    assert resp.status_code == 200
    info = resp.json()
    assert info["found"] is True
    assert info["status"] == "active"

    # Отметить вход (owner из TG/web) — билет учтён как использованный
    resp = await db_client.post(
        "/api/admin/tickets/checkin",
        headers={"X-Skip-Auth": "1"},
        json={"code": ticket.validation_code},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "checked_in"


async def test_vk_buy_refund_sync_to_tg(db_client, db_session):
    """E2E-синхронизация: VK-покупка → VK-возврат, организатор TG видит оба состояния.

    Киллер-фича в обе стороны: продажа/возврат, совершённые на одной площадке,
    мгновенно отражаются на другой (общая БД, единые счётчики sold/available).
    Сценарий:
    1. Организатор (TG, X-Skip-Auth) создаёт owner-мероприятие.
    2. VK-покупатель (X-VK-Init-Data, реальная аутентификация) покупает билет.
    3. Организатор TG видит sold=1, available уменьшился.
    4. VK-покупатель возвращает билет (POST /api/tickets/{id}/cancel, VK-заголовок).
    5. Организатор TG видит sold=0, refunded=1, available вернулся.
    """
    user_svc = UserService(db_session)

    # Организатор (канон 12345 — совпадает с X-Skip-Auth), pro-подписка
    org = await user_svc.get_or_create(PlatformType.telegram, "12345", name="Организатор")
    await user_svc.activate_subscription(org.id, days=30, tier=SubscriptionTier.pro)
    await db_session.commit()

    # Создать мероприятие через реальный web API (X-Skip-Auth)
    resp = await db_client.post(
        "/api/admin/events",
        headers={"X-Skip-Auth": "1"},
        json={
            "title": "Синхронизация VK↔TG",
            "date": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "price": 0,
            "total_tickets": 10,
            "channel_id": None,
            "owner_user_id": str(org.id),
        },
    )
    assert resp.status_code == 201, resp.text
    event_id = uuid.UUID(resp.json()["id"])
    await _publish(db_session, event_id)
    await db_session.commit()

    # ── VK-покупатель покупает билет (web, реальная VK-аутентификация) ──
    vk_header = _vk_auth_header(user_id=7777777)
    with _vk_settings():
        resp = await db_client.post(
            f"/api/events/{event_id}/buy",
            headers={"X-VK-Init-Data": vk_header},
        )
    assert resp.status_code == 201, resp.text
    ticket = resp.json()
    assert ticket["validation_code"] is not None
    assert ticket["amount"] == 100
    ticket_id = ticket["ticket_id"]

    # ── Организатор TG видит проданный билет ──
    resp = await db_client.get(f"/api/admin/events/{event_id}/stats", headers={"X-Skip-Auth": "1"})
    assert resp.status_code == 200
    assert resp.json()["sold"] == 1, resp.json()
    assert resp.json()["available"] == 9, resp.json()

    # ── VK-покупатель возвращает билет (web, VK-заголовок) ──
    with _vk_settings():
        resp = await db_client.post(
            f"/api/tickets/{ticket_id}/cancel",
            headers={"X-VK-Init-Data": vk_header},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "refunded"

    # ── Организатор TG видит возврат: sold=0, refunded=1, available вернулся ──
    resp = await db_client.get(f"/api/admin/events/{event_id}/stats", headers={"X-Skip-Auth": "1"})
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["sold"] == 0, stats
    assert stats["refunded"] == 1, stats
    assert stats["available"] == 10, stats


async def test_tg_buy_vk_checkin_symmetry(db_client, db_session):
    """Симметрия киллер-фичи: билет, купленный в TG, проверяется в VK.

    Обратная связка к test_full_vk_organizer_cycle (купил в VK → проверил в TG):
    1. Канон-организатор (TG) + линковка VK (код привязки) — одна identity.
    2. TG-покупатель (X-Skip-Auth) покупает билет через web.
    3. VK-организатор (X-VK-Init-Data, линкованный) validate/checkin → 200.
    Это доказывает платформонезависимость кода билета в обе стороны.
    """
    user_svc = UserService(db_session)
    event_svc = EventService(db_session)

    # Канон-организатор (отдельный TG-пользователь, НЕ 12345 — покупатель другой)
    org = await user_svc.get_or_create(PlatformType.telegram, "canon_tg_org", name="Канон TG")
    await user_svc.activate_subscription(org.id, days=30, tier=SubscriptionTier.pro)
    # Линковка VK → канон (вводит код привязки на VK-стороне)
    code = await user_svc.create_link_code(org.id, PlatformType.vk, ttl_minutes=10)
    await user_svc.consume_link_code(code, PlatformType.vk, "222222", current_user_id=None)
    await db_session.commit()

    # Мероприятие владельца (канон TG) через реальный web API от лица X-Skip-Auth НЕЛЬЗЯ
    # (X-Skip-Auth = 12345 ≠ canon_tg_org) → создаём через сервис, как в каноническом контуре.
    event = await event_svc.create(
        title="TG→VK check-in",
        description="Куплено в TG, проверено в VK",
        date=datetime.now(timezone.utc) + timedelta(days=7),
        location="Москва",
        price=0,
        total_tickets=20,
        channel_id=None,
        owner_user_id=org.id,
    )
    event.is_published = True
    event.is_active = True
    await db_session.commit()

    # ── TG-покупатель (X-Skip-Auth) покупает билет ──
    resp = await db_client.post(f"/api/events/{event.id}/buy", headers={"X-Skip-Auth": "1"})
    assert resp.status_code == 201, resp.text
    code_buyer = resp.json()["validation_code"]
    assert code_buyer is not None

    # ── VK-организатор (линкованный канон) проверяет по коду ──
    vk_org_header = _vk_auth_header(user_id=222222)
    with _vk_settings():
        resp = await db_client.get(
            f"/api/admin/tickets/validate?code={code_buyer}",
            headers={"X-VK-Init-Data": vk_org_header},
        )
    assert resp.status_code == 200, resp.text
    info = resp.json()
    assert info["found"] is True, info
    assert info["status"] == "active", info

    # ── VK-организатор отмечает вход ──
    with _vk_settings():
        resp = await db_client.post(
            "/api/admin/tickets/checkin",
            headers={"X-VK-Init-Data": vk_org_header},
            json={"code": code_buyer},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "checked_in", resp.json()


async def test_vk_identity_resolves_canon(db_session):
    """Вход по VK после линковки возвращает канонического организатора."""
    user_svc = UserService(db_session)
    org = await user_svc.get_or_create(PlatformType.telegram, "canon_org", name="Канон")
    code = await user_svc.create_link_code(org.id, PlatformType.vk, ttl_minutes=10)
    await user_svc.consume_link_code(code, PlatformType.vk, "vk_identity_uid", current_user_id=None)

    vk_user = await user_svc.get_or_create(PlatformType.vk, "vk_identity_uid", name="X")
    assert vk_user.id == org.id
    assert vk_user.name == "Канон"
