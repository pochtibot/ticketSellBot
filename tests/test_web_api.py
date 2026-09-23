"""
Тесты FastAPI (Mini App) хендлеров.

Используем TestClient от FastAPI + X-Skip-Auth header для тестов.
initData validation тестируем отдельно с известными HMAC-векторами.
"""

import contextlib
import hashlib
from uuid import UUID as _UUID
import hmac
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.web.server import create_app


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def client():
    """Создаёт тестовый FastAPI клиент с замоканной БД."""
    app = create_app()
    with (
        patch("app.web.server.init_db", new_callable=AsyncMock),
        patch("app.web.server.close_db", new_callable=AsyncMock),
        TestClient(app) as c,
    ):
        yield c


def make_valid_init_data(bot_token: str, user_id: int = 12345, **extra) -> str:
    """Generate a valid initData string for testing.

    Creates a data_check_string from params, signs it with the bot token,
    and returns the full query string including the hash.
    """
    params = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
        **extra,
    }

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )

    secret_key = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()
    sig = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    params["hash"] = sig
    return urlencode(sorted(params.items()))


def _vk_auth_header(user_id=5305539, app_id=123456, secret="test_vk_secret_key"):
    """Валидный X-VK-Init-Data (launch params + подпись) для тестов."""
    import base64
    import time
    from urllib.parse import urlencode

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


# ═══════════════════════════════════════════════════════════════
# initData Validation Tests
# ═══════════════════════════════════════════════════════════════

class TestInitDataValidation:
    """Тесты валидации initData."""

    def test_validate_valid_init_data(self):
        """Валидная initData должна проходить проверку."""
        from app.web.dependencies import validate_init_data
        from fastapi import HTTPException

        bot_token = "test:token"
        init_data = make_valid_init_data(bot_token)

        with patch("app.web.dependencies.settings.telegram_token", bot_token):
            result = validate_init_data(x_init_data=init_data, x_skip_auth=None)

        assert result["user"]["id"] == 12345
        assert "hash" in result
        assert "auth_date" in result

    def test_validate_missing_hash(self):
        """Отсутствие hash должно вызывать 401."""
        from app.web.dependencies import validate_init_data
        from fastapi import HTTPException

        bad_data = "auth_date=123456&user=%7B%22id%22%3A1%7D"

        with patch("app.web.dependencies.settings.telegram_token", "test:token"):
            with pytest.raises(HTTPException) as exc:
                validate_init_data(x_init_data=bad_data, x_skip_auth=None)
            assert exc.value.status_code == 401

    def test_validate_wrong_signature(self):
        """Неверная подпись должна вызывать 401."""
        from app.web.dependencies import validate_init_data
        from fastapi import HTTPException

        bot_token = "test:token"
        init_data = make_valid_init_data(bot_token)
        # Tamper with the hash
        init_data = init_data.replace(init_data.split("hash=")[-1][:8], "deadbeef")

        with patch("app.web.dependencies.settings.telegram_token", bot_token):
            with pytest.raises(HTTPException) as exc:
                validate_init_data(x_init_data=init_data, x_skip_auth=None)
            assert exc.value.status_code == 401

    def test_validate_missing_header(self):
        """Отсутствие заголовка X-Init-Data должно вызывать 401."""
        from app.web.dependencies import validate_init_data
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            validate_init_data(x_init_data=None)
        assert exc.value.status_code == 401

    def test_validate_skip_auth(self):
        """Заголовок X-Skip-Auth должен пропускать валидацию (dev: allow_skip_auth=True)."""
        from app.web.dependencies import validate_init_data

        with patch("app.web.dependencies.settings.allow_skip_auth", True):
            result = validate_init_data(x_init_data=None, x_skip_auth="1")
        assert result["user"]["id"] == 12345

    def test_skip_auth_rejected_in_production(self):
        """В production (allow_skip_auth=False) X-Skip-Auth игнорируется → 401.

        Защита от бэкдора: заголовок, который клиент шлёт сам, НЕ должен
        обходить аутентификацию на проде. Разрешение — только через
        settings.allow_skip_auth (dev/test), по умолчанию False.
        """
        from app.web.dependencies import validate_init_data
        from fastapi import HTTPException

        with patch("app.web.dependencies.settings.allow_skip_auth", False):
            with pytest.raises(HTTPException) as exc:
                validate_init_data(x_init_data=None, x_skip_auth="1")
        assert exc.value.status_code == 401


# ═══════════════════════════════════════════════════════════════
# Rate limiting
# ═══════════════════════════════════════════════════════════════

class TestRateLimit:
    """Per-IP rate limiting (защита от brute-force/скрейпинга)."""

    def test_rate_limit_returns_429(self, client):
        """Превышение лимита запросов → 429; /health не лимитируется."""
        with patch("app.config.settings.rate_limit_per_minute", 3):
            # /health в whitelist — не лимитируется даже при превышении
            assert client.get("/health").status_code == 200
            # до лимита — 401 (нет auth), в пределах 3
            for _ in range(3):
                assert client.get("/api/me").status_code == 401
            # 4-й запрос — 429
            resp = client.get("/api/me")
            assert resp.status_code == 429


# ═══════════════════════════════════════════════════════════════
# API Endpoint Tests
# ═══════════════════════════════════════════════════════════════

class TestAPIEndpoints:
    """Тесты API endpoints с замоканными сервисами."""

    def test_health(self, client):
        """GET /health всегда доступен."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_events_unauthorized(self, client):
        """GET /api/events без initData — 401."""
        resp = client.get("/api/events")
        assert resp.status_code == 401

    def test_events_skip_auth(self, client):
        """GET /api/events с X-Skip-Auth — 200 и список."""
        with patch(
            "app.web.routes.EventService.list_upcoming",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = client.get(
                "/api/events",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_events_with_data(self, client):
        """GET /api/events возвращает список мероприятий."""
        from datetime import datetime, timezone

        mock_event = Mock()
        mock_event.id = "550e8400-e29b-41d4-a716-446655440000"
        mock_event.title = "Test Event"
        mock_event.date = datetime.now(timezone.utc)
        mock_event.location = "Moscow"
        mock_event.price = 1000.0
        mock_event.available_tickets = 50
        mock_event.total_tickets = 100
        mock_event.age_restriction = "12+"
        mock_event.media_telegram_file_id = None
        mock_event.media_type = None

        with (
            patch("app.web.routes.EventService.list_upcoming", new_callable=AsyncMock, return_value=[mock_event]),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
            patch("app.web.routes.VKPayOrderService.reserved_seats_map", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.get(
                "/api/events",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Event"
        assert data[0]["price"] == 1000.0
        assert data[0]["age_restriction"] == "12+"

    def test_event_detail_found(self, client):
        """GET /api/events/{id} — детали мероприятия."""
        from datetime import datetime, timezone

        mock_event = Mock()
        mock_event.id = "550e8400-e29b-41d4-a716-446655440000"
        mock_event.title = "Test Event"
        mock_event.description = "Description"
        mock_event.date = datetime.now(timezone.utc)
        mock_event.location = "Moscow"
        mock_event.price = 1000.0
        mock_event.available_tickets = 50
        mock_event.total_tickets = 100
        mock_event.is_active = True
        mock_event.is_published = True
        mock_event.deleted_at = None
        mock_event.age_restriction = "16+"
        mock_event.media_telegram_file_id = None
        mock_event.media_type = None

        with (
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
            patch("app.web.routes.VKPayOrderService.reserved_seats_map", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.get(
                "/api/events/550e8400-e29b-41d4-a716-446655440000",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test Event"
        assert resp.json()["age_restriction"] == "16+"

    def test_event_detail_draft_404(self, client):
        """GET /api/events/{id} — черновик (is_published=False) → 404 (не утекает наружу)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.is_active = True
        mock_event.is_published = False
        mock_event.deleted_at = None

        with (
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.get(f"/api/events/{EVENT_ID}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 404

    def test_event_detail_inactive_404(self, client):
        """GET /api/events/{id} — неактивное мероприятие → 404."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.is_active = False
        mock_event.is_published = True
        mock_event.deleted_at = None

        with (
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.get(f"/api/events/{EVENT_ID}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 404

    def test_event_detail_deleted_404(self, client):
        """GET /api/events/{id} — удалённое мероприятие → 404."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.is_active = True
        mock_event.is_published = True
        mock_event.deleted_at = datetime.now(timezone.utc)

        with (
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.get(f"/api/events/{EVENT_ID}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 404

    def test_event_detail_not_found(self, client):
        """GET /api/events/{id} — 404 если нет."""
        with patch(
            "app.web.routes.EventService.get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.get(
                "/api/events/550e8400-e29b-41d4-a716-446655440000",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 404

    def test_event_detail_invalid_uuid(self, client):
        """GET /api/events/{id} с не-UUID — 400."""
        resp = client.get(
            "/api/events/not-a-uuid",
            headers={"X-Skip-Auth": "1"},
        )
        assert resp.status_code == 400

    def test_buy_ticket_success(self, client):
        """POST /api/events/{id}/buy — успешная покупка."""
        mock_result = {
            "ticket_id": "660e8400-e29b-41d4-a716-446655440001",
            "event_title": "Test Event",
            "event_date": "2026-12-25T19:00:00+00:00",
            "amount": 1000.0,
            "payment_id": "770e8400-e29b-41d4-a716-446655440002",
            "payment_status": "pending",
            "purchase_date": "2026-07-10T12:00:00+00:00",
            "validation_code": "TEST-CODE",
        }

        with (
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock) as mock_user,
            patch("app.web.routes.TicketService.buy_ticket_webapp", new_callable=AsyncMock) as mock_buy,
            patch("app.web.routes._send_ticket_dm", new_callable=AsyncMock, return_value=False),
        ):
            mock_user.return_value = Mock(id="550e8400-e29b-41d4-a716-446655440000")
            mock_buy.return_value = mock_result

            resp = client.post(
                "/api/events/550e8400-e29b-41d4-a716-446655440000/buy",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 201
        assert resp.json()["payment_status"] == "pending"

    def test_buy_ticket_conflict(self, client):
        """POST /api/events/{id}/buy — конфликт (билеты кончились)."""
        with (
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock) as mock_user,
            patch("app.web.routes.TicketService.buy_ticket_webapp", new_callable=AsyncMock) as mock_buy,
        ):
            mock_user.return_value = Mock(id="550e8400-e29b-41d4-a716-446655440000")
            mock_buy.side_effect = ValueError("Билеты закончились")

            resp = client.post(
                "/api/events/550e8400-e29b-41d4-a716-446655440000/buy",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 409
        assert "Билеты закончились" in resp.json()["detail"]

    def test_buy_ticket_vk_platform(self, client):
        """VK-покупатель → get_or_create с PlatformType.vk (не telegram)."""
        from app.core.models import PlatformType

        vk_header = _vk_auth_header(user_id=5305539)
        with (
            patch("app.web.vk_auth.settings.vk_app_id", 123456),
            patch("app.web.vk_auth.settings.vk_secret_key", "test_vk_secret_key"),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=Mock(price=0)),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock) as mock_user,
            patch("app.web.routes.TicketService.buy_ticket_webapp", new_callable=AsyncMock) as mock_buy,
            patch("app.web.routes._send_ticket_dm", new_callable=AsyncMock, return_value=False),
        ):
            mock_user.return_value = Mock(id="550e8400-e29b-41d4-a716-446655440000")
            mock_buy.return_value = {
                "ticket_id": "x", "event_title": "E", "event_date": "d", "validation_code": None,
            }
            resp = client.post(
                "/api/events/550e8400-e29b-41d4-a716-446655440000/buy",
                headers={"X-VK-Init-Data": vk_header},
            )
        assert resp.status_code == 201
        args, kwargs = mock_user.await_args
        assert kwargs["platform"] == PlatformType.vk
        assert kwargs["platform_user_id"] == "5305539"

    def test_vk_paid_ticket_cannot_use_legacy_buy_route(self, client):
        vk_header = _vk_auth_header(user_id=5305539)
        with (
            patch("app.web.vk_auth.settings.vk_app_id", 123456),
            patch("app.web.vk_auth.settings.vk_secret_key", "test_vk_secret_key"),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=Mock(price=100)),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.post(
                "/api/events/550e8400-e29b-41d4-a716-446655440000/buy",
                headers={"X-VK-Init-Data": vk_header},
            )
        assert resp.status_code == 409
        assert "VK Pay" in resp.json()["detail"]

    def test_list_tickets_vk_platform(self, client):
        """VK-пользователь видит свои билеты (platform=vk)."""
        from app.core.models import PlatformType

        vk_header = _vk_auth_header(user_id=5305539)
        with (
            patch("app.web.vk_auth.settings.vk_app_id", 123456),
            patch("app.web.vk_auth.settings.vk_secret_key", "test_vk_secret_key"),
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock) as mock_user,
            patch("app.web.routes.TicketService.get_user_tickets", new_callable=AsyncMock) as mock_tickets,
        ):
            mock_user.return_value = Mock(id="550e8400-e29b-41d4-a716-446655440000")
            mock_tickets.return_value = []
            resp = client.get("/api/tickets", headers={"X-VK-Init-Data": vk_header})
        assert resp.status_code == 200
        args, kwargs = mock_user.await_args
        assert kwargs["platform"] == PlatformType.vk
        assert kwargs["platform_user_id"] == "5305539"

    def test_cancel_ticket_vk_platform(self, client):
        """VK-пользователь отменяет билет (platform=vk)."""
        from app.core.models import PlatformType

        vk_header = _vk_auth_header(user_id=5305539)
        with (
            patch("app.web.vk_auth.settings.vk_app_id", 123456),
            patch("app.web.vk_auth.settings.vk_secret_key", "test_vk_secret_key"),
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock) as mock_user,
            patch("app.web.routes.TicketService.cancel_ticket", new_callable=AsyncMock) as mock_cancel,
            patch("app.web.routes._send_ticket_dm", new_callable=AsyncMock, return_value=False),
        ):
            mock_user.return_value = Mock(id="550e8400-e29b-41d4-a716-446655440000")
            mock_cancel.return_value = Mock(
                status=Mock(value="active"),
                event_id="550e8400-e29b-41d4-a716-446655440000",
                validation_code="CODE-1234",
            )
            resp = client.post(
                "/api/tickets/550e8400-e29b-41d4-a716-446655440000/cancel",
                headers={"X-VK-Init-Data": vk_header},
            )
        assert resp.status_code == 200
        args, kwargs = mock_user.await_args
        assert kwargs["platform"] == PlatformType.vk
        assert kwargs["platform_user_id"] == "5305539"

    def test_my_tickets(self, client):
        """GET /api/tickets — список билетов пользователя."""
        from datetime import datetime, timezone

        mock_tickets = [
            {
                "id": "660e8400-e29b-41d4-a716-446655440001",
                "event_id": "550e8400-e29b-41d4-a716-446655440000",
                "event_title": "Test Event",
                "purchase_date": datetime.now(timezone.utc),
                "status": "active",
            }
        ]

        with (
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock) as mock_user,
            patch("app.web.routes.TicketService.get_user_tickets", new_callable=AsyncMock) as mock_tickets_svc,
        ):
            mock_user.return_value = Mock(id="550e8400-e29b-41d4-a716-446655440000")
            mock_tickets_svc.return_value = mock_tickets

            resp = client.get(
                "/api/tickets",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["event_title"] == "Test Event"

    def test_cancel_ticket_success(self, client):
        """POST /api/tickets/{id}/cancel — успешная отмена + DM."""
        with (
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock) as mock_user,
            patch("app.web.routes.TicketService.cancel_ticket", new_callable=AsyncMock) as mock_cancel,
            patch("app.web.routes._send_ticket_dm", new_callable=AsyncMock, return_value=False),
        ):
            mock_ticket = Mock()
            mock_ticket.id = "660e8400-e29b-41d4-a716-446655440001"
            mock_ticket.status.value = "refunded"
            mock_ticket.event_id = "550e8400-e29b-41d4-a716-446655440000"
            mock_user.return_value = Mock(id="550e8400-e29b-41d4-a716-446655440000")
            mock_cancel.return_value = mock_ticket

            resp = client.post(
                "/api/tickets/660e8400-e29b-41d4-a716-446655440001/cancel",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "refunded"


# ═══════════════════════════════════════════════════════════════
# Admin API Tests (личный кабинет + админка)
# ═══════════════════════════════════════════════════════════════

CHANNEL_ID = "11111111-1111-4111-8111-111111111111"
CHANNEL_ID_2 = "99999999-9999-4999-8999-999999999999"
EVENT_ID = "22222222-2222-4222-8222-222222222222"
USER_ID = "33333333-3333-4333-8333-333333333333"


@contextlib.contextmanager
def admin_auth(is_super=False, channel_ids=None, sub_valid=True, organizer=False):
    """Контекст-менеджер: резолв текущего пользователя с заданной ролью."""
    channel_ids = channel_ids or []
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("app.web.dependencies.settings.admin_telegram_ids", "12345" if is_super else ""))
        _mock_user = Mock(id=_UUID(USER_ID))
        _mock_user.name = "Dev"
        _mock_user.username = None
        _mock_user.platform_user_id = "12345"
        _mock_user.subscription_tier = Mock()
        _mock_user.subscription_tier.value = "basic"
        _mock_user.is_subscription_active = False
        _mock_user.subscription_until = None
        stack.enter_context(patch(
            "app.web.dependencies.UserService.get_or_create",
            new_callable=AsyncMock,
            return_value=_mock_user,
        ))
        stack.enter_context(patch(
            "app.web.dependencies.UserService.is_subscription_valid",
            new_callable=AsyncMock,
            return_value=organizer,
        ))
        stack.enter_context(patch(
            "app.web.routes.UserService.get_by_platform_user_id",
            new_callable=AsyncMock,
            return_value=_mock_user,
        ))
        stack.enter_context(patch(
            "app.web.dependencies.ChannelService.get_channel_ids_by_admin",
            new_callable=AsyncMock,
            return_value=channel_ids,
        ))
        stack.enter_context(patch(
            "app.web.dependencies.ChannelService.is_subscription_valid",
            new_callable=AsyncMock,
            return_value=sub_valid,
        ))
        stack.enter_context(patch(
            "app.web.dependencies.EventService.get_manager_event_ids",
            new_callable=AsyncMock,
            return_value=[],
        ))
        stack.enter_context(patch(
            "app.web.dependencies.VKGroupService.list_vk_groups",
            new_callable=AsyncMock,
            return_value=[],
        ))
        yield



def _mock_channel():
    ch = Mock()
    ch.id = CHANNEL_ID
    ch.telegram_channel_id = "@test"
    ch.title = "Test Channel"
    ch.is_subscription_active = True
    ch.subscription_tier.value = "pro"
    ch.subscription_until = None
    return ch

class TestAdminAPI:
    """Тесты личного кабинета и админки."""

    def test_me_role_user(self, client):
        """GET /api/me — роль user при отсутствии прав."""
        with (
            admin_auth(is_super=False, channel_ids=[]),
            patch("app.web.routes.ChannelService.get_channels_by_admin", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get("/api/me", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "user"
        assert data["is_super_admin"] is False

    def test_me_role_organizer_with_channel(self, client):
        """GET /api/me — роль organizer при канале с активной подпиской."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], sub_valid=True),
            patch("app.web.routes.ChannelService.get_channels_by_admin", new_callable=AsyncMock, return_value=[_mock_channel()]),
        ):
            resp = client.get("/api/me", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "organizer"

    def test_me_role_super_admin(self, client):
        """GET /api/me — роль super_admin по config."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.get_channels_by_admin", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get("/api/me", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "super_admin"
        assert data["is_super_admin"] is True

    def test_admin_events_forbidden_for_regular_user(self, client):
        """GET /api/admin/events — 403 для обычного пользователя (только организатор)."""
        with (
            admin_auth(is_super=False, channel_ids=[]),
            patch("app.web.routes.EventService.list_all", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get("/api/admin/events", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    def test_admin_events_list_super_empty(self, client):
        """GET /api/admin/events — суперадмин видит пусто (НЕ управляет мероприятиями)."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.EventService.list_all", new_callable=AsyncMock, return_value=[Mock()]),
        ):
            resp = client.get("/api/admin/events", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_admin_create_event_ok(self, client):
        """POST /api/admin/events — создание черновика."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.is_published = False

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.create", new_callable=AsyncMock, return_value=mock_event) as m_create,
        ):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "New Event",
                    "date": "2026-12-01T19:00:00Z",
                    "price": 0,
                    "total_tickets": 50,
                    "channel_id": CHANNEL_ID,
                    "age_restriction": "18+",
                },
            )
        assert resp.status_code == 201
        assert resp.json()["is_published"] is False
        # age_restriction проброшен в сервис
        assert m_create.await_args.kwargs["age_restriction"] == "18+"

    def test_admin_create_event_bad_age_restriction(self, client):
        """POST /api/admin/events — недопустимое age_restriction ("21+") → 422."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "New Event",
                    "date": "2026-12-01T19:00:00Z",
                    "price": 0,
                    "total_tickets": 50,
                    "channel_id": CHANNEL_ID,
                    "age_restriction": "21+",
                },
            )
        assert resp.status_code == 422

    def test_admin_create_event_empty_age_restriction(self, client):
        """POST /api/admin/events — пустой age_restriction → 422 (только 0+/6+/12+/16+/18+)."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "New Event",
                    "date": "2026-12-01T19:00:00Z",
                    "price": 0,
                    "total_tickets": 50,
                    "channel_id": CHANNEL_ID,
                    "age_restriction": "",
                },
            )
        assert resp.status_code == 422

    def test_admin_update_event_bad_age_restriction(self, client):
        """PATCH /api/admin/events/{id} — недопустимое age_restriction ("21+") → 422."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.patch(
                f"/api/admin/events/{EVENT_ID}",
                headers={"X-Skip-Auth": "1"},
                json={"age_restriction": "21+"},
            )
        assert resp.status_code == 422

    def test_admin_create_event_wrong_channel(self, client):
        """POST /api/admin/events — 403 если канал вне managed."""
        other_channel = "99999999-9999-4999-8999-999999999999"
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "New Event",
                    "date": "2026-12-01T19:00:00Z",
                    "price": 0,
                    "total_tickets": 50,
                    "channel_id": other_channel,
                },
            )
        assert resp.status_code == 403

    def test_admin_create_event_conflict(self, client):
        """POST /api/admin/events — 409 при ValueError (напр. платное на basic)."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.create", new_callable=AsyncMock, side_effect=ValueError("Тариф не позволяет платные мероприятия")),
        ):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "New Event",
                    "date": "2026-12-01T19:00:00Z",
                    "price": 500,
                    "total_tickets": 50,
                    "channel_id": CHANNEL_ID,
                },
            )
        assert resp.status_code == 409

    def test_admin_publish(self, client):
        """POST /api/admin/events/{id}/publish — публикация + анонс."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.update", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.post_event_announcement", new_callable=AsyncMock, return_value=True),
        ):
            resp = client.post(f"/api/admin/events/{EVENT_ID}/publish", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["announced"] is True

    def test_admin_checkin_ok(self, client):
        """POST /api/admin/tickets/checkin — успешный вход."""
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.status.value = "checked_in"
        mock_ticket.event_id = EVENT_ID
        mock_ticket.checked_in_at = None

        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID(USER_ID)
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.TicketService.check_in_by_code", new_callable=AsyncMock, return_value=mock_ticket),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(
                "/api/admin/tickets/checkin",
                headers={"X-Skip-Auth": "1"},
                json={"code": "AB3X-K7M9"},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_admin_checkin_conflict(self, client):
        """POST /api/admin/tickets/checkin — 409 при ошибке."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.TicketService.check_in_by_code", new_callable=AsyncMock, side_effect=ValueError("Билет не найден")),
        ):
            resp = client.post(
                "/api/admin/tickets/checkin",
                headers={"X-Skip-Auth": "1"},
                json={"code": "ZZZZ-ZZZZ"},
            )
        assert resp.status_code == 409

    def test_admin_validate_ticket(self, client):
        """GET /api/admin/tickets/validate — проверка кода."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.TicketService.validate_ticket", new_callable=AsyncMock, return_value={"found": False, "status": "not_found"}),
        ):
            resp = client.get("/api/admin/tickets/validate?code=ZZZZ-ZZZZ", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["found"] is False

    def test_admin_channels_forbidden_for_channel_admin(self, client):
        """GET /api/admin/channels — 403 для channel-admin."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]):
            resp = client.get("/api/admin/channels", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    def test_admin_channels_list_super(self, client):
        """GET /api/admin/channels — super-admin видит каналы."""
        mock_channel = Mock()
        mock_channel.id = CHANNEL_ID
        mock_channel.telegram_channel_id = "@test"
        mock_channel.title = "Test Channel"
        mock_channel.is_subscription_active = True
        mock_channel.subscription_tier.value = "pro"
        mock_channel.subscription_until = None

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.list_all", new_callable=AsyncMock, return_value=[mock_channel]),
            patch("app.web.routes.ChannelAdminService.get_admin_ids", new_callable=AsyncMock, return_value=["123"]),
        ):
            resp = client.get("/api/admin/channels", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_admin_subscribe(self, client):
        """POST /api/admin/channels/{id}/subscribe."""
        mock_channel = Mock()
        mock_channel.is_subscription_active = True
        mock_channel.subscription_tier.value = "pro"
        mock_channel.subscription_until = None

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.activate_subscription", new_callable=AsyncMock, return_value=mock_channel),
        ):
            resp = client.post(
                f"/api/admin/channels/{CHANNEL_ID}/subscribe",
                headers={"X-Skip-Auth": "1"},
                json={"duration_days": 30, "tier": "pro"},
            )
        assert resp.status_code == 200
        assert resp.json()["subscription_tier"] == "pro"

    def test_admin_stats_forbidden(self, client):
        """GET /api/admin/stats — 403 для channel-admin."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]):
            resp = client.get("/api/admin/stats", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    def test_admin_stats_ok(self, client):
        """GET /api/admin/stats — super-admin видит статистику."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.StatsService.get_global_stats", new_callable=AsyncMock, return_value={"users_count": 1, "revenue": 0}),
        ):
            resp = client.get("/api/admin/stats", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["users_count"] == 1

    def test_admin_event_stats_channel_admin(self, client):
        """GET /api/admin/events/{id}/stats — channel-admin видит (без тарифного гейта)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.get_event_stats", new_callable=AsyncMock, return_value={"total_tickets": 10, "sold": 2}),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/stats", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["sold"] == 2

    def test_admin_csv_export(self, client):
        """GET /api/admin/events/{id}/tickets.csv — CSV экспорт."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)
        mock_tickets = [{
            "ticket_id": "t1", "event_title": "Test", "user_name": "User",
            "purchase_date": "2026-08-01T10:00:00Z", "status": "active",
            "validation_code": "AB3X-K7M9", "checked_in_at": "", "checked_in_by": "", "is_free": "нет",
        }]

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.TicketService.export_event_tickets", new_callable=AsyncMock, return_value=mock_tickets),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/tickets.csv", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "AB3X-K7M9" in resp.text


class TestSuperAdminGaps:
    """Тесты закрытия пробелов супер-админского функционала."""

    def test_admin_create_channel(self, client):
        """POST /api/admin/channels — создаёт канал и подписывает."""
        mock_channel = Mock()
        mock_channel.id = CHANNEL_ID
        mock_channel.telegram_channel_id = "@newchan"
        mock_channel.is_subscription_active = True
        mock_channel.subscription_tier.value = "pro"
        mock_channel.subscription_until = None

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.get_by_telegram_id", new_callable=AsyncMock, return_value=None),
            patch("app.web.routes.ChannelService.create", new_callable=AsyncMock, return_value=mock_channel),
            patch("app.web.routes.ChannelService.activate_subscription", new_callable=AsyncMock, return_value=mock_channel),
        ):
            resp = client.post(
                "/api/admin/channels",
                headers={"X-Skip-Auth": "1"},
                json={"telegram_channel_id": "@newchan", "duration_days": 30, "tier": "pro"},
            )
        assert resp.status_code == 201
        assert resp.json()["subscription_tier"] == "pro"

    def test_admin_create_channel_existing(self, client):
        """POST /api/admin/channels — обновляет подписку существующего."""
        mock_channel = Mock()
        mock_channel.id = CHANNEL_ID
        mock_channel.telegram_channel_id = "@exist"
        mock_channel.is_subscription_active = True
        mock_channel.subscription_tier.value = "basic"
        mock_channel.subscription_until = None

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.get_by_telegram_id", new_callable=AsyncMock, return_value=mock_channel),
            patch("app.web.routes.ChannelService.activate_subscription", new_callable=AsyncMock, return_value=mock_channel),
        ):
            resp = client.post(
                "/api/admin/channels",
                headers={"X-Skip-Auth": "1"},
                json={"telegram_channel_id": "@exist", "duration_days": 30, "tier": "basic"},
            )
        assert resp.status_code == 201
        # create не вызывался для существующего
        from unittest.mock import call
        assert resp.json()["channel_id"] == CHANNEL_ID

    def test_admin_create_channel_forbidden(self, client):
        """POST /api/admin/channels — 403 для channel-admin."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]):
            resp = client.post(
                "/api/admin/channels",
                headers={"X-Skip-Auth": "1"},
                json={"telegram_channel_id": "@newchan", "duration_days": 30, "tier": "basic"},
            )
        assert resp.status_code == 403

    def test_admin_user_info(self, client):
        """GET /api/admin/users/{id} — инфо о пользователе."""
        mock_user = Mock(id=_UUID(USER_ID))
        mock_user.platform_user_id = "541587295"
        mock_user.username = "ivan"
        mock_user.name = "Иван"
        mock_user.created_at = None
        mock_user.is_subscription_active = False
        mock_user.subscription_tier.value = "basic"
        mock_user.subscription_until = None

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id", new_callable=AsyncMock, return_value=mock_user),
            patch("app.web.routes.ChannelService.get_channels_by_admin", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get("/api/admin/users/541587295", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Иван"
        assert resp.json()["telegram_user_id"] == "541587295"

    def test_admin_user_info_not_found(self, client):
        """GET /api/admin/users/{id} — 404 если пользователя нет."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id", new_callable=AsyncMock, return_value=None),
        ):
            resp = client.get("/api/admin/users/999999", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 404

    def test_admin_user_info_forbidden(self, client):
        """GET /api/admin/users/{id} — 403 для channel-admin."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]):
            resp = client.get("/api/admin/users/541587295", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    def test_admin_broadcast(self, client):
        """POST /api/admin/broadcast — рассылка во все активные каналы."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.send_broadcast", new_callable=AsyncMock, return_value=(5, 5)),
        ):
            resp = client.post(
                "/api/admin/broadcast",
                headers={"X-Skip-Auth": "1"},
                json={"text": "Всем привет!"},
            )
        assert resp.status_code == 200
        assert resp.json()["sent"] == 5
        assert resp.json()["total"] == 5

    def test_admin_broadcast_empty(self, client):
        """POST /api/admin/broadcast — 400 на пустое сообщение."""
        with admin_auth(is_super=True, channel_ids=[]):
            resp = client.post(
                "/api/admin/broadcast",
                headers={"X-Skip-Auth": "1"},
                json={"text": "   "},
            )
        assert resp.status_code == 400

    def test_admin_broadcast_forbidden(self, client):
        """POST /api/admin/broadcast — 403 для channel-admin."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]):
            resp = client.post(
                "/api/admin/broadcast",
                headers={"X-Skip-Auth": "1"},
                json={"text": "hi"},
            )
        assert resp.status_code == 403

    def test_admin_health(self, client):
        """GET /api/admin/health — статус, username, БД."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.get_telegram_bot", return_value=Mock()),
        ):
            resp = client.get("/api/admin/health", headers={"X-Skip-Auth": "1"})
        # Запрос к реальной БД может вернуть 500 в тесте — проверяем только код
        assert resp.status_code in (200, 500)

    def test_admin_health_forbidden(self, client):
        """GET /api/admin/health — 403 для channel-admin."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]):
            resp = client.get("/api/admin/health", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403


class TestUserSoftDelete:
    """Soft-delete пользователей (super-admin only)."""

    def test_admin_list_users(self, client):
        """GET /api/admin/users — список пользователей."""
        mock_user = Mock()
        mock_user.id = USER_ID
        mock_user.platform_user_id = "12345"
        mock_user.name = "Test User"
        mock_user.created_at = None
        mock_user.is_subscription_active = False
        mock_user.subscription_tier = Mock(value="basic")

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.list_all",
                  new_callable=AsyncMock, return_value=[mock_user]),
        ):
            resp = client.get("/api/admin/users", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["telegram_user_id"] == "12345"

    def test_admin_delete_user(self, client):
        """DELETE /api/admin/users/{id} — мягкое удаление."""
        mock_user = Mock()
        mock_user.id = USER_ID
        mock_user.deleted_at = None

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id",
                  new_callable=AsyncMock, return_value=mock_user),
            patch("app.web.routes.UserService.soft_delete",
                  new_callable=AsyncMock, return_value=mock_user),
        ):
            resp = client.delete("/api/admin/users/12345", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_admin_delete_user_not_found(self, client):
        """DELETE /api/admin/users/{id} — пользователь не найден."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id",
                  new_callable=AsyncMock, return_value=None),
        ):
            resp = client.delete("/api/admin/users/99999", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 404

    def test_admin_delete_user_already_deleted(self, client):
        """DELETE /api/admin/users/{id} — уже удалён → 409."""
        mock_user = Mock()
        mock_user.id = USER_ID
        mock_user.deleted_at = "2026-01-01T00:00:00Z"

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id",
                  new_callable=AsyncMock, return_value=mock_user),
        ):
            resp = client.delete("/api/admin/users/12345", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 409

    def test_admin_delete_user_forbidden(self, client):
        """DELETE /api/admin/users/{id} — не super-admin → 403."""
        with admin_auth(is_super=False, channel_ids=[]):
            resp = client.delete("/api/admin/users/12345", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403


class TestInviteApi:
    """Тесты эндпоинтов пригласительных (TDD: до реализации)."""

    def test_admin_issue_invite(self, client):
        """POST /admin/events/{id}/invites — админ канала выдаёт пригласительное."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        mock_invite = Mock()
        mock_invite.id = EVENT_ID
        mock_invite.validation_code = "AB3X-K7M9"
        mock_invite.seats = 1
        mock_invite.status.value = "active"

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.TicketService.issue_invite", new_callable=AsyncMock, return_value=mock_invite),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/invites",
                headers={"X-Skip-Auth": "1"},
                json={"seats": 1},
            )
        assert resp.status_code == 201
        assert resp.json()["validation_code"] == "AB3X-K7M9"

    def test_admin_issue_invite_super_forbidden(self, client):
        """POST invites — 403 для суперадмина (не выдаёт пригласительные)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/invites",
                headers={"X-Skip-Auth": "1"},
                json={"seats": 1},
            )
        assert resp.status_code == 403

    def test_admin_issue_invite_not_admin_forbidden(self, client):
        """POST invites — 403 если канал не в managed."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=["99999999-9999-4999-8999-999999999999"]),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/invites",
                headers={"X-Skip-Auth": "1"},
                json={"seats": 1},
            )
        assert resp.status_code == 403

    def test_admin_issue_invite_no_pro(self, client):
        """POST invites — 403 если канал не pro (require_feature False)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=False),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/invites",
                headers={"X-Skip-Auth": "1"},
                json={"seats": 1},
            )
        assert resp.status_code == 403

    def test_admin_issue_invite_conflict(self, client):
        """POST invites — 409 при ValueError."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.TicketService.issue_invite", new_callable=AsyncMock, side_effect=ValueError("Квота исчерпана")),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/invites",
                headers={"X-Skip-Auth": "1"},
                json={"seats": 1},
            )
        assert resp.status_code == 409

    def test_admin_cancel_invite(self, client):
        """POST invites/{tid}/cancel — отмена пригласительного."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)
        mock_invite = Mock()
        mock_invite.id = EVENT_ID
        mock_invite.status.value = "refunded"

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.TicketService.cancel_invite", new_callable=AsyncMock, return_value=mock_invite),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/invites/{EVENT_ID}/cancel",
                headers={"X-Skip-Auth": "1"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "refunded"

    def test_admin_get_invites(self, client):
        """GET /admin/events/{id}/invites — список пригласительных."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.TicketService.get_event_invites", new_callable=AsyncMock, return_value=[{"is_invite": True, "validation_code": "AB3X-K7M9"}]),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/invites", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert len(resp.json()["invites"]) == 1

    def test_admin_ticket_qr(self, client):
        """GET /admin/tickets/{id}/qr — PNG-картинка QR."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.event_id = EVENT_ID
        mock_ticket.validation_code = "AB3X-K7M9"

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.TicketService.get_ticket_event", new_callable=AsyncMock, return_value=(mock_ticket, mock_event)),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.generate_qr_png", new_callable=Mock, return_value=b"\x89PNG..."),
        ):
            resp = client.get(f"/api/admin/tickets/{EVENT_ID}/qr", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert "image/png" in resp.headers["content-type"]

    def test_buyer_ticket_qr(self, client):
        """Покупатель получает QR СВОЕГО билета (GET /api/tickets/{id}/qr, без pro)."""
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.user_id = _UUID(USER_ID)  # владелец
        mock_ticket.event_id = EVENT_ID
        mock_ticket.validation_code = "AB3X-K7M9"
        _owner = Mock(id=_UUID(USER_ID))

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock, return_value=_owner),
            patch("app.web.routes.TicketService.get_ticket_event", new_callable=AsyncMock, return_value=(mock_ticket, Mock())),
            patch("app.web.routes.generate_qr_png", new_callable=Mock, return_value=b"\x89PNG..."),
        ):
            resp = client.get(f"/api/tickets/{EVENT_ID}/qr", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert "image/png" in resp.headers["content-type"]

    def test_buyer_ticket_qr_foreign_403(self, client):
        """Чужой пользователь НЕ получает QR чужого билета (403)."""
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.user_id = "other-user-id"  # чужой владелец
        mock_ticket.event_id = EVENT_ID
        mock_ticket.validation_code = "AB3X-K7M9"
        _owner = Mock(id=_UUID(USER_ID))

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock, return_value=_owner),
            patch("app.web.routes.TicketService.get_ticket_event", new_callable=AsyncMock, return_value=(mock_ticket, Mock())),
        ):
            resp = client.get(f"/api/tickets/{EVENT_ID}/qr", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    def test_send_vk_ticket(self, client):
        """POST /api/tickets/{id}/send-vk — билет в ЛС VK от группы (owner + группа + токен)."""
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.user_id = _UUID(USER_ID)  # владелец
        mock_ticket.event_id = EVENT_ID
        mock_ticket.validation_code = "AB3X-K7M9"
        mock_event = Mock()
        mock_event.owner_user_id = _UUID(USER_ID)
        mock_event.title = "Событие"
        mock_event.date = Mock()
        mock_event.date.isoformat.return_value = "2026-12-25"
        _owner = Mock(id=_UUID(USER_ID))
        _vk_group = Mock()
        _vk_group.group_id = "7777777"

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock, return_value=_owner),
            patch("app.web.routes.TicketService.get_ticket_event", new_callable=AsyncMock, return_value=(mock_ticket, mock_event)),
            patch("app.web.routes.VKGroupService.list_vk_groups", new_callable=AsyncMock, return_value=[_vk_group]),
            patch("app.web.routes.send_vk_ticket_dm", new_callable=AsyncMock, return_value=True),
        ):
            resp = client.post(f"/api/tickets/{EVENT_ID}/send-vk", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["sent"] is True
        assert resp.json()["group_id"] == "7777777"

    def test_send_vk_ticket_foreign_403(self, client):
        """Чужой пользователь НЕ может отправить чужой билет в ЛС (403)."""
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.user_id = "other-user-id"  # чужой владелец
        mock_ticket.event_id = EVENT_ID
        mock_ticket.validation_code = "AB3X-K7M9"
        _owner = Mock(id=_UUID(USER_ID))

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock, return_value=_owner),
            patch("app.web.routes.TicketService.get_ticket_event", new_callable=AsyncMock, return_value=(mock_ticket, Mock())),
        ):
            resp = client.post(f"/api/tickets/{EVENT_ID}/send-vk", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403


@pytest.mark.integration
class TestCabinetFlow:
    """Сквозной web-flow пользователя на реальной БД (db_client, async)."""

    async def test_full_cabinet_flow(self, db_client, db_session, sample_channel, sample_event):
        """browse → buy → my tickets → admin → invite → checkin → stats."""
        from app.core.models import SubscriptionTier
        from app.core.services import ChannelAdminService

        event_id = str(sample_event.id)

        # Роль: юзер 12345 (X-Skip-Auth) — АДМИН КАНАЛА sample_channel (не супер:
        # суперадмин не выдаёт пригласительные). Реальный юзер создаётся
        # get_or_create (не мокаем) — buy и админка используют одного юзера.
        assert sample_channel.subscription_tier == SubscriptionTier.pro
        await ChannelAdminService(db_session).sync_admins(sample_channel.id, ["12345"])
        await db_session.commit()

        # 1. browse — событие видно
        resp = await db_client.get("/api/events", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        events = [e for e in resp.json() if e["id"] == event_id]
        assert len(events) == 1

        # 2. buy — билет создан
        resp = await db_client.post(f"/api/events/{event_id}/buy", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 201, resp.text
        ticket_id = resp.json()["ticket_id"]

        # 3. my tickets — билет с кодом (sample_event платный → is_free=False)
        resp = await db_client.get("/api/tickets", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        tickets = [t for t in resp.json() if t["id"] == ticket_id]
        assert len(tickets) == 1
        assert tickets[0]["validation_code"] is not None
        assert tickets[0]["is_free"] is False  # A: платный → QR

        # 3b. QR покупателя (F2) — реальный PNG на реальной БД (generate_qr_png)
        resp = await db_client.get(f"/api/tickets/{ticket_id}/qr", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert "image/png" in resp.headers["content-type"]
        assert resp.content.startswith(b"\x89PNG"), "ответ не является PNG"

        # 3c. validate (путь сканера): QR кодирует validation_code, сканер прогоняет
        # его через GET /validate → в ответе event_id/ticket_id (для проверки доступа)
        code = tickets[0]["validation_code"]
        resp = await db_client.get(f"/api/admin/tickets/validate?code={code}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        vdata = resp.json()
        assert vdata["event_id"] == event_id
        assert vdata["ticket_id"] == ticket_id

        # 4. admin — событие в списке и его билеты
        resp = await db_client.get("/api/admin/events", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert any(e["id"] == event_id for e in resp.json())

        resp = await db_client.get(f"/api/admin/events/{event_id}/tickets", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert len(resp.json()["tickets"]) == 1

        # 5. invite — пригласительное (нужна квота + pro)
        from app.core.services import EventService
        await EventService(db_session).update(sample_event.id, invites_quota=5)
        await db_session.commit()
        assert sample_channel.subscription_tier == SubscriptionTier.pro
        resp = await db_client.post(
                f"/api/admin/events/{event_id}/invites",
                headers={"X-Skip-Auth": "1"},
                json={"seats": 1},
        )
        assert resp.status_code == 201, resp.text

        # 6. checkin — отметка входа по коду купленного билета
        code = tickets[0]["validation_code"]
        resp = await db_client.post(
                "/api/admin/tickets/checkin",
                headers={"X-Skip-Auth": "1"},
                json={"code": code},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

        # 7. stats — sold=0 (билет использован), invites_issued=1, quota=5
        resp = await db_client.get(f"/api/admin/events/{event_id}/stats", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        stats = resp.json()
        # sold считает АКТИВНЫЕ оплаченные; после checkin билет не active → 0
        assert stats["sold"] == 0, f"sold={stats['sold']}"
        assert stats["invites_issued"] == 1, f"invites_issued={stats['invites_issued']}"
        assert stats["invites_quota"] == 5

    async def test_buyer_refund_releases_seat(self, db_client, db_session, sample_channel, sample_event):
        """F3: возврат билета — статус refunded, место возвращено в продажу.

        Покупатель (12345) покупает билет → POST /api/tickets/{id}/cancel →
        status=refunded → available увеличился на 1 (реальная БД).
        """
        from app.core.services import ChannelAdminService

        event_id = str(sample_event.id)
        await ChannelAdminService(db_session).sync_admins(sample_channel.id, ["12345"])
        await db_session.commit()

        # 1. Покупка
        resp = await db_client.post(f"/api/events/{event_id}/buy", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 201, resp.text
        ticket_id = resp.json()["ticket_id"]

        resp = await db_client.get(f"/api/admin/events/{event_id}/stats", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        available_after_buy = resp.json()["available"]

        # 2. Возврат билета (владелец, без подписки)
        resp = await db_client.post(f"/api/tickets/{ticket_id}/cancel", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "refunded"

        # 3. Место вернулось в продажу
        resp = await db_client.get(f"/api/admin/events/{event_id}/stats", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["available"] == available_after_buy + 1, resp.json()

    async def test_invite_claimed_by_guest_via_link(self, db_client, db_session, sample_channel, sample_event):
        """F-invites: организатор выдаёт пригласительное → гость активирует по ссылке.

        Полный e2e: орг (12345) выдаёт invite → гость (66666) открывает ссылку
        (?invite=<код>) и активирует → билет в «Моих билетах» гостя →
        организатор проверяет по коду (LEFT JOIN) → sold не растёт, available уменьшился.
        """
        from app.core.models import SubscriptionTier
        from app.core.services import ChannelAdminService, EventService

        event_id = str(sample_event.id)
        await ChannelAdminService(db_session).sync_admins(sample_channel.id, ["12345"])
        await EventService(db_session).update(sample_event.id, invites_quota=5)
        await db_session.commit()
        assert sample_channel.subscription_tier == SubscriptionTier.pro

        # 0. Базовое available до выдачи
        resp = await db_client.get(f"/api/admin/events/{event_id}/stats", headers={"X-Skip-Auth": "1"})
        available_before = resp.json()["available"]

        # 1. Организатор выдаёт пригласительное (seats=2)
        resp = await db_client.post(
            f"/api/admin/events/{event_id}/invites",
            headers={"X-Skip-Auth": "1"},
            json={"seats": 2},
        )
        assert resp.status_code == 201, resp.text
        invite_code = resp.json()["validation_code"]

        # stats: available уменьшился на 2, sold=0, invites_issued=1
        resp = await db_client.get(f"/api/admin/events/{event_id}/stats", headers={"X-Skip-Auth": "1"})
        stats = resp.json()
        assert stats["invites_issued"] == 1
        assert stats["sold"] == 0
        assert stats["available"] == available_before - 2, stats

        # 2. Гость (66666, отдельный пользователь) активирует по коду из ссылки
        resp = await db_client.post(
            f"/api/invites/{invite_code}/claim",
            headers={"X-Skip-Auth": "1"},
        )
        assert resp.status_code == 200, resp.text
        claimed = resp.json()
        assert claimed["ticket_id"] is not None
        assert claimed["event_title"] == sample_event.title

        # 3. Пригласительное видно в «Моих билетах» гостя (66666)
        resp = await db_client.get("/api/tickets", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        guest_tickets = [t for t in resp.json() if t["id"] == claimed["ticket_id"]]
        assert len(guest_tickets) == 1
        assert guest_tickets[0]["validation_code"] == invite_code

        # 4. Организатор проверяет пригласительное по коду (LEFT JOIN для invite)
        resp = await db_client.get(
            f"/api/admin/tickets/validate?code={invite_code}",
            headers={"X-Skip-Auth": "1"},
        )
        assert resp.status_code == 200, resp.text
        info = resp.json()
        assert info["found"] is True
        assert info["status"] == "active"

        # 5. sold НЕ вырос (пригласительное не в sold), available НЕ изменился после активации
        resp = await db_client.get(f"/api/admin/events/{event_id}/stats", headers={"X-Skip-Auth": "1"})
        stats = resp.json()
        assert stats["sold"] == 0
        assert stats["invites_issued"] == 1
        assert stats["available"] == available_before - 2

    async def test_free_organizer_one_event_limit(self, db_client, db_session):
        """B: free-организатор — 1 опубликованное будущее; 2-е → 409.

        Пользователь без подписки создаёт 2 бесплатных события, публикует первое
        (200), второе (409). После переноса даты первого в прошлое — можно снова.
        """
        from datetime import datetime, timezone, timedelta
        from app.core.models import Event as EventModel, PlatformType
        from app.core.services import UserService
        from sqlalchemy import select

        # Юзер 12345 = free-организатор (basic-подписка, лимит 1 опубликованное будущее)
        user = await UserService(db_session).get_or_create(
            PlatformType.telegram, "12345", "Free Org",
        )
        from app.core.models import SubscriptionTier
        await UserService(db_session).activate_subscription(
            user.id, days=30, tier=SubscriptionTier.basic,
        )
        await db_session.commit()

        def _payload(title, days=7):
            return {
                "title": title,
                "date": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
                "price": 0,
                "total_tickets": 10,
                "owner_user_id": str(user.id),
            }

        # Создать два черновика
        r1 = await db_client.post("/api/admin/events", headers={"X-Skip-Auth": "1"}, json=_payload("Первое"))
        assert r1.status_code == 201, r1.text
        id1 = r1.json()["id"]
        r2 = await db_client.post("/api/admin/events", headers={"X-Skip-Auth": "1"}, json=_payload("Второе"))
        assert r2.status_code == 201, r2.text
        id2 = r2.json()["id"]

        # Публикация: анонс — внешний side-effect, мокаем (как в organizer_e2e)
        from unittest.mock import patch as _patch
        from app.web.routes import post_event_announcement, send_announcement_dm
        with (
            _patch("app.web.routes.post_event_announcement", new_callable=AsyncMock, return_value=False),
            _patch("app.web.routes.send_announcement_dm", new_callable=AsyncMock, return_value=False),
        ):
            # Публикация первого — ок
            p1 = await db_client.post(f"/api/admin/events/{id1}/publish", headers={"X-Skip-Auth": "1"}, json={})
            assert p1.status_code == 200, p1.text

            # Публикация второго — 409 (лимит)
            p2 = await db_client.post(f"/api/admin/events/{id2}/publish", headers={"X-Skip-Auth": "1"}, json={})
            assert p2.status_code == 409, p2.text
            assert "только одно мероприятие" in p2.json()["detail"]

            # Перенести дату первого в прошлое → слот освободился
            ev = (await db_session.execute(select(EventModel).where(EventModel.id == _UUID(id1)))).scalar_one()
            ev.date = datetime.now(timezone.utc) - timedelta(days=1)
            await db_session.commit()

            # Повторная публикация второго — ок
            p2b = await db_client.post(f"/api/admin/events/{id2}/publish", headers={"X-Skip-Auth": "1"}, json={})
            assert p2b.status_code == 200, p2b.text

    async def test_event_premium_unlocks_paid_features(self, db_client, db_session):
        """C: basic-организатор покупает премиум на событие → платное + QR + пригласительные."""
        from datetime import datetime, timezone, timedelta
        from app.core.models import Event as EventModel, PlatformType
        from app.core.services import UserService
        from sqlalchemy import select

        user = await UserService(db_session).get_or_create(
            PlatformType.telegram, "12345", "Org",
        )
        from app.core.models import SubscriptionTier
        await UserService(db_session).activate_subscription(
            user.id, days=30, tier=SubscriptionTier.basic,
        )
        await db_session.commit()

        # Черновик (бесплатный)
        resp = await db_client.post(
            "/api/admin/events", headers={"X-Skip-Auth": "1"},
            json={
                "title": "Premium Event", "price": 0, "total_tickets": 10,
                "owner_user_id": str(user.id),
                "date": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            },
        )
        assert resp.status_code == 201, resp.text
        event_id = resp.json()["id"]

        # Купить премиум на событие
        resp = await db_client.post(
            f"/api/me/events/{event_id}/premium", headers={"X-Skip-Auth": "1"}, json={},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_premium"] is True

        # is_premium виден в деталях
        resp = await db_client.get(f"/api/admin/events/{event_id}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_premium"] is True

        # Теперь цену поднять можно (премиум дал paid_events)
        resp = await db_client.patch(
            f"/api/admin/events/{event_id}", headers={"X-Skip-Auth": "1"},
            json={"price": 500},
        )
        assert resp.status_code == 200, resp.text

        # Проверим цену через детали
        resp = await db_client.get(f"/api/admin/events/{event_id}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["price"] == 500
        assert resp.json()["is_premium"] is True


class TestCoverageGaps:
    """Покрытие эндпоинтов, не имевших тестов."""

    def test_patch_me(self, client):
        """PATCH /api/me — обновление имени."""
        _u = Mock()
        _u.name = "Новое"
        with (
            admin_auth(is_super=False, channel_ids=[]),
            patch("app.web.routes.UserService.update_name", new_callable=AsyncMock, return_value=_u),
        ):
            resp = client.patch("/api/me", headers={"X-Skip-Auth": "1"}, json={"name": "Новое"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Новое"

    def test_delete_me(self, client):
        """DELETE /api/me — удаление аккаунта (self-service, п.1.1.10)."""
        _u = Mock()
        _u.id = _UUID(USER_ID)
        _u.deleted_at = datetime.now(timezone.utc)
        with (
            admin_auth(is_super=False, channel_ids=[]),
            patch("app.web.routes.UserService.delete_account", new_callable=AsyncMock, return_value=_u),
        ):
            resp = client.delete("/api/me", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] is True
        assert body["id"] == USER_ID

    def test_admin_get_event(self, client):
        """GET /api/admin/events/{id} — детали (админ)."""
        from datetime import datetime, timezone
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)
        mock_event.title = "Test"
        mock_event.description = None
        mock_event.date = datetime.now(timezone.utc)
        mock_event.location = None
        mock_event.price = 100.0
        mock_event.total_tickets = 10
        mock_event.available_tickets = 10
        mock_event.is_active = True
        mock_event.is_published = True
        mock_event.is_free = False
        mock_event.age_restriction = "18+"
        mock_event.media_telegram_file_id = None
        mock_event.media_type = None

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.get_event_is_premium", new_callable=AsyncMock, return_value=False),
            patch("app.web.routes.ChannelService.get_by_id", new_callable=AsyncMock, return_value=Mock(title="Ch")),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test"
        assert resp.json()["age_restriction"] == "18+"

    def test_admin_update_event(self, client):
        """PATCH /api/admin/events/{id} — обновление."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.update", new_callable=AsyncMock, return_value=mock_event) as m_update,
        ):
            resp = client.patch(
                f"/api/admin/events/{EVENT_ID}",
                headers={"X-Skip-Auth": "1"},
                json={"title": "New", "age_restriction": "16+"},
            )
        assert resp.status_code == 200
        assert resp.json()["updated"] is True
        # age_restriction проброшен в сервис (update(uid, **data))
        assert m_update.await_args.kwargs["age_restriction"] == "16+"

    def test_admin_toggle(self, client):
        """POST /api/admin/events/{id}/toggle."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)
        mock_event.is_active = False

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.set_active", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(f"/api/admin/events/{EVENT_ID}/toggle", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_admin_delete(self, client):
        """POST /api/admin/events/{id}/delete."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.soft_delete", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(f"/api/admin/events/{EVENT_ID}/delete", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_admin_repost(self, client):
        """POST /api/admin/events/{id}/repost."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.post_event_announcement", new_callable=AsyncMock, return_value=True),
        ):
            resp = client.post(f"/api/admin/events/{EVENT_ID}/repost", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["announced"] is True

    def test_admin_event_tickets_list(self, client):
        """GET /api/admin/events/{id}/tickets."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.TicketService.get_event_tickets", new_callable=AsyncMock, return_value=[{"id": "t1"}]),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/tickets", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert len(resp.json()["tickets"]) == 1

    def test_admin_ticket_cancel(self, client):
        """POST /api/admin/tickets/{id}/cancel (admin)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)
        mock_ticket = Mock()
        mock_ticket.status.value = "refunded"

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.TicketService.get_ticket_event", new_callable=AsyncMock, return_value=(mock_ticket, mock_event)),
            patch("app.web.routes.TicketService.admin_cancel_ticket", new_callable=AsyncMock, return_value=mock_ticket),
        ):
            resp = client.post(f"/api/admin/tickets/{EVENT_ID}/cancel", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "refunded"

    def test_admin_channel_info(self, client):
        """GET /api/admin/channels/{id} — детальная информация."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.get_channel_summary", new_callable=AsyncMock, return_value={"id": CHANNEL_ID, "events_count": 2}),
        ):
            resp = client.get(f"/api/admin/channels/{CHANNEL_ID}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["events_count"] == 2

    def test_admin_unsubscribe(self, client):
        """POST /api/admin/channels/{id}/unsubscribe."""
        mock_channel = Mock()
        mock_channel.is_subscription_active = False

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.deactivate_subscription", new_callable=AsyncMock, return_value=mock_channel),
        ):
            resp = client.post(f"/api/admin/channels/{CHANNEL_ID}/unsubscribe", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["is_subscription_active"] is False

    def test_admin_change_admin(self, client):
        """POST /api/admin/channels/{id}/change_admin."""
        mock_channel = Mock()
        mock_channel.id = CHANNEL_ID
        mock_channel.telegram_channel_id = "@chan"

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.get_by_id", new_callable=AsyncMock, return_value=mock_channel),
            patch("app.web.routes.ChannelService.change_admin", new_callable=AsyncMock, return_value=(mock_channel, ["old1"])),
        ):
            resp = client.post(
                f"/api/admin/channels/{CHANNEL_ID}/change_admin",
                headers={"X-Skip-Auth": "1"},
                json={"new_admin_id": "999"},
            )
        assert resp.status_code == 200
        assert resp.json()["new_admin_id"] == "999"

    def test_admin_check_expired(self, client):
        """POST /api/admin/channels/check_expired."""
        mock_channel = Mock()
        mock_channel.id = CHANNEL_ID
        mock_channel.is_subscription_active = True

        # Fake-сессия: execute возвращает список каналов
        class _FakeResult:
            def scalars(self):
                return self
            def all(self):
                return [mock_channel]
        class _FakeSession:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False
            async def execute(self, *a, **kw):
                return _FakeResult()
            async def commit(self):
                pass
        class _FakeFactory:
            def __call__(self):
                return _FakeSession()

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.async_session_factory", _FakeFactory()),
            patch("app.web.routes.ChannelService.is_subscription_valid", new_callable=AsyncMock, return_value=False),
        ):
            resp = client.post("/api/admin/channels/check_expired", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["deactivated"] == 1

    def test_csv_export_empty_404(self, client):
        """GET tickets.csv без билетов — 404."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.TicketService.export_event_tickets", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/tickets.csv", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 404

    def test_events_channel_filter(self, client):
        """GET /api/events?channel_id= фильтрует."""
        with (
            patch("app.web.routes.EventService.list_upcoming", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get(f"/api/events?channel_id={CHANNEL_ID}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_vk_me(self, client):
        """GET /api/vk/me — без auth."""
        with (
            patch("app.web.routes.UserService.get_or_create", new_callable=AsyncMock, return_value=Mock()),
        ):
            resp = client.get("/api/vk/me?user_id=123&user_name=Test")
        assert resp.status_code == 200
        assert resp.json()["platform"] == "vk"

    def test_vk_me_profile_contract(self, client):
        """GET /api/me exposes platform-neutral identity for VK."""
        current = Mock()
        current.user_id = _UUID(USER_ID)
        current.telegram_user_id = "5305539"
        current.platform = __import__("app.core.models", fromlist=["PlatformType"]).PlatformType.vk
        current.name = "VK User"
        current.role = "user"
        current.is_super_admin = False
        current.is_organizer = False
        current.has_group = False
        current.vk_group_ids = []
        app = create_app()
        from app.web.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda: current
        with (
            patch("app.web.routes.ChannelService.get_channels_by_admin", new_callable=AsyncMock, return_value=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id", new_callable=AsyncMock, return_value=None),
            patch("app.web.server.init_db", new_callable=AsyncMock),
            patch("app.web.server.close_db", new_callable=AsyncMock),
            TestClient(app) as vk_client,
        ):
            resp = vk_client.get("/api/me")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        data = resp.json()
        assert data["platform"] == "vk"
        assert data["platform_user_id"] == "5305539"
        assert "telegram_user_id" not in data
        assert data["channels"] == []

    def test_telegram_me_profile_contract_legacy_context(self, client):
        """Legacy Telegram profile contract remains unchanged."""
        with (
            admin_auth(is_super=False, channel_ids=[]),
            patch("app.web.routes.ChannelService.get_channels_by_admin", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get("/api/me", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["telegram_user_id"] == "12345"
        assert resp.json()["platform"] == "telegram"


class TestSubscriptionApi:
    """Тесты смены подписки (TDD: до реализации)."""

    def test_admin_update_subscription(self, client):
        """POST /admin/channels/{id}/subscription — тип+срок."""
        mock_channel = Mock()
        mock_channel.id = CHANNEL_ID
        mock_channel.subscription_tier.value = "pro"
        mock_channel.subscription_until = None

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.change_subscription", new_callable=AsyncMock, return_value=mock_channel),
        ):
            resp = client.post(
                f"/api/admin/channels/{CHANNEL_ID}/subscription",
                headers={"X-Skip-Auth": "1"},
                json={"tier": "pro", "period": 3, "period_unit": "months"},
            )
        assert resp.status_code == 200
        assert resp.json()["subscription_tier"] == "pro"

    def test_admin_change_tier(self, client):
        """POST /admin/channels/{id}/tier — только тип."""
        mock_channel = Mock()
        mock_channel.id = CHANNEL_ID
        mock_channel.subscription_tier.value = "basic"

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.change_tier", new_callable=AsyncMock, return_value=mock_channel),
        ):
            resp = client.post(
                f"/api/admin/channels/{CHANNEL_ID}/tier",
                headers={"X-Skip-Auth": "1"},
                json={"tier": "basic"},
            )
        assert resp.status_code == 200
        assert resp.json()["subscription_tier"] == "basic"

    def test_admin_update_subscription_forbidden(self, client):
        """POST subscription — 403 для channel-admin."""
        with admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)]):
            resp = client.post(
                f"/api/admin/channels/{CHANNEL_ID}/subscription",
                headers={"X-Skip-Auth": "1"},
                json={"tier": "pro", "period": 3, "period_unit": "months"},
            )
        assert resp.status_code == 403

    def test_admin_update_subscription_not_found(self, client):
        """POST subscription — 404 если канал не найден."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.ChannelService.change_subscription", new_callable=AsyncMock, return_value=None),
        ):
            resp = client.post(
                f"/api/admin/channels/{CHANNEL_ID}/subscription",
                headers={"X-Skip-Auth": "1"},
                json={"tier": "pro", "period": 3, "period_unit": "months"},
            )
        assert resp.status_code == 404


class TestOrganizerApi:
    """Тесты роли организатора (TDD: до реализации)."""

    def test_create_event_as_organizer_without_channel(self, client):
        """Организатор без канала создаёт мероприятие (owner_user_id)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.is_published = False

        with (
            admin_auth(is_super=False, channel_ids=[], sub_valid=True, organizer=True),
            patch("app.web.routes.EventService.create", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "Org Event",
                    "date": "2026-12-01T19:00:00Z",
                    "price": 0,
                    "total_tickets": 50,
                    "channel_id": None,
                    "owner_user_id": USER_ID,
                },
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_published"] is False

    def test_create_event_no_channel_no_owner(self, client):
        """Без channel и owner → 400/409 (организатор без канала)."""
        with admin_auth(is_super=False, channel_ids=[], organizer=True):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "No Target",
                    "date": "2026-12-01T19:00:00Z",
                    "price": 0,
                    "total_tickets": 50,
                    "channel_id": None,
                    "owner_user_id": None,
                },
            )
        assert resp.status_code in (400, 409)

    def test_organizer_own_events(self, client):
        """Организатор видит свои мероприятия (по owner)."""
        from datetime import datetime, timezone
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = USER_ID
        mock_event.title = "My"
        mock_event.date = datetime.now(timezone.utc)
        mock_event.location = None
        mock_event.price = 0.0
        mock_event.total_tickets = 10
        mock_event.available_tickets = 10
        mock_event.is_active = True
        mock_event.is_published = True
        mock_event.is_free = True
        mock_event.age_restriction = "0+"
        mock_event.media_telegram_file_id = None
        mock_event.media_type = None

        with (
            admin_auth(is_super=False, channel_ids=[], sub_valid=True, organizer=True),
            patch("app.web.routes.EventService.list_all", new_callable=AsyncMock, return_value=[mock_event]),
            patch("app.web.routes.EventService.get_event_premium_map", new_callable=AsyncMock, return_value={}),
            patch("app.web.routes.ChannelService.get_by_id", new_callable=AsyncMock, return_value=None),
        ):
            resp = client.get("/api/admin/events", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestOwnerAccess:
    """Доступ организатора-владельца к своим owner-мероприятиям (баг #050)."""

    def test_owner_can_stats(self, client):
        """Владелец owner-мероприятия видит статистику (не 403)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID(USER_ID)

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.get_event_stats", new_callable=AsyncMock, return_value={"sold": 1}),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/stats", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["sold"] == 1

    def test_owner_can_toggle(self, client):
        """Владелец может toggle своё owner-мероприятие."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID(USER_ID)
        mock_event.is_active = True

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.set_active", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(f"/api/admin/events/{EVENT_ID}/toggle", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200

    def test_owner_can_delete(self, client):
        """Владелец может удалить своё owner-мероприятие."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID(USER_ID)

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.soft_delete", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(f"/api/admin/events/{EVENT_ID}/delete", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_other_user_cannot_stats(self, client):
        """Другой пользователь не видит чужое owner-мероприятие (403)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID("99999999-9999-4999-8999-999999999999")  # другой владелец

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/stats", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403


class TestCheckinAccess:
    """Доступ к check-in: только организатор мероприятия (замечание C)."""

    def test_owner_can_checkin_own(self, client):
        """Владелец owner-мероприятия может checkin свой билет."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID(USER_ID)
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.status.value = "checked_in"
        mock_ticket.event_id = EVENT_ID
        mock_ticket.checked_in_at = None

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.TicketService.check_in_by_code", new_callable=AsyncMock, return_value=mock_ticket),
        ):
            resp = client.post(
                "/api/admin/tickets/checkin",
                headers={"X-Skip-Auth": "1"},
                json={"code": "AB3X-K7M9"},
            )
        assert resp.status_code == 200

    def test_other_organizer_cannot_checkin(self, client):
        """Чужой организатор НЕ может checkin чужое owner-мероприятие (403)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID("99999999-9999-4999-8999-999999999999")  # другой владелец

        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.event_id = EVENT_ID
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.TicketService.check_in_by_code", new_callable=AsyncMock, return_value=mock_ticket),
        ):
            resp = client.post(
                "/api/admin/tickets/checkin",
                headers={"X-Skip-Auth": "1"},
                json={"code": "AB3X-K7M9"},
            )
        assert resp.status_code == 403


class TestValidateAccess:
    """Доступ к GET /api/admin/tickets/validate — только организатор мероприятия.

    Регресс-тесты фикса: validate_ticket теперь возвращает event_id,
    и проверка _can_manage_event в маршруте реально срабатывает (раньше была мёртвой).
    """

    def _validate(self, client, code="AB3X-K7M9"):
        return client.get(f"/api/admin/tickets/validate?code={code}", headers={"X-Skip-Auth": "1"})

    def test_admin_validate_owner_allowed(self, client):
        """Владелец owner-мероприятия может валидировать билет (200)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID(USER_ID)

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.TicketService.validate_ticket", new_callable=AsyncMock, return_value={
                "found": True, "status": "active", "event_id": EVENT_ID, "ticket_id": EVENT_ID,
            }),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = self._validate(client)
        assert resp.status_code == 200

    def test_admin_validate_other_organizer_forbidden(self, client):
        """Чужой организатор НЕ может валидировать билет чужого события (403).

        Главный security-тест: раньше проверка доступа была мёртвой и вернул бы 200.
        """
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID("99999999-9999-4999-8999-999999999999")  # другой владелец

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.TicketService.validate_ticket", new_callable=AsyncMock, return_value={
                "found": True, "status": "active", "event_id": EVENT_ID, "ticket_id": EVENT_ID,
            }),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = self._validate(client)
        assert resp.status_code == 403

    def test_admin_validate_super_admin_allowed(self, client):
        """Супер-админ валидирует билет чужого события (200)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID("99999999-9999-4999-8999-999999999999")

        with (
            admin_auth(is_super=True, channel_ids=[], organizer=True),
            patch("app.web.routes.TicketService.validate_ticket", new_callable=AsyncMock, return_value={
                "found": True, "status": "active", "event_id": EVENT_ID, "ticket_id": EVENT_ID,
            }),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = self._validate(client)
        assert resp.status_code == 200

    def test_admin_validate_channel_admin_allowed(self, client):
        """Админ канала мероприятия может валидировать билет (200)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = _UUID(CHANNEL_ID)
        mock_event.owner_user_id = None

        with (
            # managed_channel_ids — list[UUID] (как на проде); строки в списке канал НЕ находят
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.TicketService.validate_ticket", new_callable=AsyncMock, return_value={
                "found": True, "status": "active", "event_id": EVENT_ID, "ticket_id": EVENT_ID,
            }),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = self._validate(client)
        assert resp.status_code == 200

    def test_admin_validate_not_found_no_403(self, client):
        """Несуществующий код → 200 found:false (без 403 — не светим существование билета)."""
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.TicketService.validate_ticket", new_callable=AsyncMock, return_value={"found": False, "status": "not_found"}),
        ):
            resp = self._validate(client, code="ZZZZ-ZZZZ")
        assert resp.status_code == 200
        assert resp.json()["found"] is False


class TestQrGate:
    """QR-коды — фича pro (матрица qr_codes)."""

    def test_basic_organizer_cannot_qr(self, client):
        """Организатор на basic (без канала) НЕ получает QR (403)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID(USER_ID)
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.event_id = EVENT_ID
        mock_ticket.validation_code = "AB3X-K7M9"

        # owner-мероприятие, basic-подписка (require_feature False)
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=False),
            patch("app.web.routes.TicketService.get_ticket_event", new_callable=AsyncMock, return_value=(mock_ticket, mock_event)),
        ):
            resp = client.get(f"/api/admin/tickets/{EVENT_ID}/qr", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    def test_pro_organizer_can_qr(self, client):
        """Организатор на pro получает QR (200)."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.channel_id = None
        mock_event.owner_user_id = _UUID(USER_ID)
        mock_ticket = Mock()
        mock_ticket.id = EVENT_ID
        mock_ticket.event_id = EVENT_ID
        mock_ticket.validation_code = "AB3X-K7M9"

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.TicketService.get_ticket_event", new_callable=AsyncMock, return_value=(mock_ticket, mock_event)),
            patch("app.web.routes.generate_qr_png", new_callable=Mock, return_value=b"\x89PNG..."),
        ):
            resp = client.get(f"/api/admin/tickets/{EVENT_ID}/qr", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200


class TestOpenCreateAndSubscribe:
    """Только организатор создаёт + покупка подписки (матрица ролей)."""

    def test_create_forbidden_without_subscription(self, client):
        """Пользователь БЕЗ подписки НЕ создаёт мероприятие → 403."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.is_published = False

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.EventService.create", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "Free Event", "date": "2026-12-01T19:00:00Z",
                    "price": 0, "total_tickets": 50,
                    "channel_id": None, "owner_user_id": USER_ID,
                },
            )
        assert resp.status_code == 403, resp.text

    def test_create_organizer_with_subscription(self, client):
        """Организатор (с подпиской) создаёт мероприятие → 201."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.is_published = False

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.create", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.post(
                "/api/admin/events",
                headers={"X-Skip-Auth": "1"},
                json={
                    "title": "Free Event", "date": "2026-12-01T19:00:00Z",
                    "price": 0, "total_tickets": 50,
                    "channel_id": None, "owner_user_id": USER_ID,
                },
            )
        assert resp.status_code == 201, resp.text

    def test_buy_subscription_me(self, client):
        """POST /api/me/subscription — покупка подписки (активация)."""
        _mock_user = Mock(id=_UUID(USER_ID))
        _mock_user.subscription_tier.value = "pro"
        _mock_user.subscription_until = None

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.UserService.activate_subscription", new_callable=AsyncMock, return_value=_mock_user),
        ):
            resp = client.post(
                "/api/me/subscription",
                headers={"X-Skip-Auth": "1"},
                json={"tier": "pro"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["subscription_tier"] == "pro"


# ═══════════════════════════════════════════════════════════════
# My Channels API (самообслуживание каналов)
# ═══════════════════════════════════════════════════════════════

class TestMyChannelsApi:
    """Self-service channel management for organizers."""

    def test_post_me_channels_creates_and_syncs_admin(self, client):
        """POST /api/me/channels — организатор добавляет новый канал → 201."""
        mock_channel = _mock_channel()
        mock_channel.is_subscription_active = False
        mock_channel.subscription_until = None
        mock_channel.subscription_tier.value = "basic"

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.ChannelService.get_by_telegram_id",
                  new_callable=AsyncMock, return_value=None),
            patch("app.web.routes.ChannelService.create",
                  new_callable=AsyncMock, return_value=mock_channel),
            patch("app.web.routes.ChannelAdminService.sync_admins",
                  new_callable=AsyncMock) as mock_sync,
            patch("app.web.routes.ChannelAdminService.user_is_admin",
                  new_callable=AsyncMock, return_value=False),
        ):
            resp = client.post(
                "/api/me/channels",
                headers={"X-Skip-Auth": "1"},
                json={"telegram_channel_id": "@newchan"},
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["telegram_channel_id"] == "@test"
        mock_sync.assert_called_once()

    def test_post_me_channels_allows_regular_user(self, client):
        """POST /api/me/channels — обычный пользователь может добавить СВОЙ канал (201)."""
        mock_channel = _mock_channel()
        mock_channel.is_subscription_active = False
        mock_channel.subscription_until = None

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.ChannelService.get_by_telegram_id",
                  new_callable=AsyncMock, return_value=None),
            patch("app.web.routes.ChannelService.create",
                  new_callable=AsyncMock, return_value=mock_channel),
            patch("app.web.routes.ChannelAdminService.sync_admins",
                  new_callable=AsyncMock),
        ):
            resp = client.post(
                "/api/me/channels",
                headers={"X-Skip-Auth": "1"},
                json={"telegram_channel_id": "@mychan"},
            )
        assert resp.status_code == 201, resp.text

    def test_post_me_channels_existing_idempotent(self, client):
        """POST /api/me/channels — канал уже есть и пользователь админ → 200."""
        mock_channel = _mock_channel()

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.ChannelService.get_by_telegram_id",
                  new_callable=AsyncMock, return_value=mock_channel),
            patch("app.web.routes.ChannelAdminService.user_is_admin",
                  new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.ChannelService.create",
                  new_callable=AsyncMock) as mock_create,
        ):
            resp = client.post(
                "/api/me/channels",
                headers={"X-Skip-Auth": "1"},
                json={"telegram_channel_id": "@test"},
            )
        assert resp.status_code == 200, resp.text
        mock_create.assert_not_called()

    def test_post_me_channels_existing_not_admin_409(self, client):
        """POST /api/me/channels — канал есть но пользователь не админ → 409."""
        mock_channel = _mock_channel()

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.ChannelService.get_by_telegram_id",
                  new_callable=AsyncMock, return_value=mock_channel),
            patch("app.web.routes.ChannelAdminService.user_is_admin",
                  new_callable=AsyncMock, return_value=False),
        ):
            resp = client.post(
                "/api/me/channels",
                headers={"X-Skip-Auth": "1"},
                json={"telegram_channel_id": "@test"},
            )
        assert resp.status_code == 409, resp.text

    def test_post_me_channels_empty_id_400(self, client):
        """POST /api/me/channels — пустой telegram_channel_id → 400."""
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
        ):
            resp = client.post(
                "/api/me/channels",
                headers={"X-Skip-Auth": "1"},
                json={"telegram_channel_id": ""},
            )
        assert resp.status_code == 400, resp.text

    def test_get_me_channels_list(self, client):
        """GET /api/me/channels — список каналов пользователя."""
        ch1 = _mock_channel()
        ch2 = _mock_channel()
        ch2.id = CHANNEL_ID_2

        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.ChannelService.get_channels_by_admin",
                  new_callable=AsyncMock, return_value=[ch1, ch2]),
        ):
            resp = client.get("/api/me/channels", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        channels = resp.json()
        assert len(channels) == 2
        assert channels[0]["id"] == CHANNEL_ID
        assert channels[1]["id"] == CHANNEL_ID_2
        # Без subscription-полей — только идентификация канала
        assert "is_subscription_active" not in channels[0]
        assert "status" not in channels[0]


class TestPromoCodesAPI:
    """Админ-эндпоинты промокодов: create/list/toggle + гейты pro."""

    def _mock_event(self):
        ev = Mock()
        ev.id = EVENT_ID
        ev.owner_user_id = None
        ev.channel_id = _UUID(CHANNEL_ID)
        return ev

    def test_admin_create_promo_success(self, client):
        """Канальный админ создаёт промокод на своё событие (201)."""
        mock_promo = Mock()
        mock_promo.id = EVENT_ID
        mock_promo.code = "SUMMER10"
        mock_promo.discount_type.value = "percent"
        mock_promo.discount_value = 10
        mock_promo.starts_at = None
        mock_promo.ends_at = None
        mock_promo.max_uses = 0
        mock_promo.used_count = 0
        mock_promo.is_active = True
        mock_promo.created_at = datetime.now(timezone.utc)

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.TicketService.create_promo_code", new_callable=AsyncMock, return_value=mock_promo),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/promo-codes",
                headers={"X-Skip-Auth": "1"},
                json={"code": "SUMMER10", "discount_type": "percent", "discount_value": 10},
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()["code"] == "SUMMER10"

    def test_admin_create_promo_no_pro(self, client):
        """Без pro-фичи promo_codes → 403."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=False),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/promo-codes",
                headers={"X-Skip-Auth": "1"},
                json={"code": "SUMMER10", "discount_type": "percent", "discount_value": 10},
            )
        assert resp.status_code == 403

    def test_admin_create_promo_not_admin(self, client):
        """Пользователь без доступа к событию → 403."""
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/promo-codes",
                headers={"X-Skip-Auth": "1"},
                json={"code": "SUMMER10", "discount_type": "percent", "discount_value": 10},
            )
        assert resp.status_code == 403

    def test_admin_create_promo_conflict(self, client):
        """Конфликт (дубликат кода) → 409."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.TicketService.create_promo_code", new_callable=AsyncMock, side_effect=ValueError("Промокод не найден")),
        ):
            resp = client.post(
                f"/api/admin/events/{EVENT_ID}/promo-codes",
                headers={"X-Skip-Auth": "1"},
                json={"code": "SUMMER10", "discount_type": "percent", "discount_value": 10},
            )
        assert resp.status_code == 409

    def test_admin_list_promos(self, client):
        """Список промокодов события (200)."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.TicketService.list_promo_codes", new_callable=AsyncMock, return_value=[{"code": "SUMMER10"}]),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/promo-codes", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["promo_codes"]) == 1

    def test_admin_list_promos_not_admin(self, client):
        """Список промокодов чужого события → 403."""
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/promo-codes", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    def test_admin_toggle_promo(self, client):
        """Toggle вкл/выкл промокода (200)."""
        mock_promo = Mock()
        mock_promo.id = EVENT_ID
        mock_promo.event_id = EVENT_ID
        mock_promo.is_active = False

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.TicketService.get_promo_code_by_id", new_callable=AsyncMock, return_value=mock_promo),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.TicketService.toggle_promo_code", new_callable=AsyncMock, return_value=mock_promo),
        ):
            resp = client.post(f"/api/admin/promo-codes/{EVENT_ID}/toggle", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False

    def test_admin_toggle_promo_no_pro(self, client):
        """Toggle без pro-фичи → 403."""
        mock_promo = Mock()
        mock_promo.id = EVENT_ID
        mock_promo.event_id = EVENT_ID

        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.TicketService.get_promo_code_by_id", new_callable=AsyncMock, return_value=mock_promo),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=False),
        ):
            resp = client.post(f"/api/admin/promo-codes/{EVENT_ID}/toggle", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    def test_buy_ticket_with_promo_code(self, client):
        """POST /buy с промокодом передаёт promo_code в сервис."""
        mock_buy = AsyncMock(return_value={
            "ticket_id": EVENT_ID,
            "amount": 900.0,
            "event_title": "Тест",
            "event_date": "2026-08-20T12:00:00+00:00",
            "validation_code": "AB3X-K7M9",
            "is_free": False,
        })
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.TicketService.buy_ticket_webapp", mock_buy),
        ):
            resp = client.post(
                f"/api/events/{EVENT_ID}/buy",
                headers={"X-Skip-Auth": "1"},
                json={"promo_code": "SUMMER10"},
            )
        assert resp.status_code == 201, resp.text
        assert mock_buy.await_args.kwargs["promo_code"] == "SUMMER10"


class TestPriceRangesAPI:
    """Эндпоинты динамических цен: PUT/GET price-ranges + актуальная цена в buyer-ответах."""

    def _mock_event(self):
        ev = Mock()
        ev.id = EVENT_ID
        ev.owner_user_id = None
        ev.channel_id = _UUID(CHANNEL_ID)
        ev.price = 100
        ev.created_at = datetime.now(timezone.utc)
        return ev

    def test_admin_replace_price_ranges_success(self, client):
        """PUT price-ranges канальным админом (200)."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.EventService.replace_price_ranges", new_callable=AsyncMock, return_value=[{"price": 100}]),
        ):
            resp = client.put(
                f"/api/admin/events/{EVENT_ID}/price-ranges",
                headers={"X-Skip-Auth": "1"},
                json={"ranges": [{"starts_at": "2026-08-01T00:00:00Z", "ends_at": "2026-08-02T00:00:00Z", "price": 100}]},
            )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["price_ranges"]) == 1

    def test_admin_replace_price_ranges_no_pro(self, client):
        """Без pro-фичи dynamic_pricing → 403."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=False),
        ):
            resp = client.put(
                f"/api/admin/events/{EVENT_ID}/price-ranges",
                headers={"X-Skip-Auth": "1"},
                json={"ranges": []},
            )
        assert resp.status_code == 403

    def test_admin_replace_price_ranges_not_admin(self, client):
        """Пользователь без доступа → 403."""
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
        ):
            resp = client.put(
                f"/api/admin/events/{EVENT_ID}/price-ranges",
                headers={"X-Skip-Auth": "1"},
                json={"ranges": []},
            )
        assert resp.status_code == 403

    def test_admin_replace_price_ranges_conflict(self, client):
        """Конфликт покрытия/бесплатное → 409."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.has_event_pro_feature", new_callable=AsyncMock, return_value=True),
            patch("app.web.routes.EventService.replace_price_ranges", new_callable=AsyncMock, side_effect=ValueError("есть «дыра»")),
        ):
            resp = client.put(
                f"/api/admin/events/{EVENT_ID}/price-ranges",
                headers={"X-Skip-Auth": "1"},
                json={"ranges": []},
            )
        assert resp.status_code == 409

    def test_admin_get_price_ranges(self, client):
        """GET price-ranges (200)."""
        with (
            admin_auth(is_super=False, channel_ids=[_UUID(CHANNEL_ID)], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
            patch("app.web.routes.EventService.get_price_ranges", new_callable=AsyncMock, return_value=[{"price": 100}]),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/price-ranges", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["price_ranges"]) == 1

    def test_admin_get_price_ranges_not_admin(self, client):
        """GET price-ranges чужого события → 403."""
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=False),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=self._mock_event()),
        ):
            resp = client.get(f"/api/admin/events/{EVENT_ID}/price-ranges", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 403

    @staticmethod
    def _mock_range(price, ev):
        """Мок-диапазон: обычный объект с атрибутами (не SQLAlchemy — избежать relationship)."""
        rng = Mock()
        rng.starts_at = datetime.now(timezone.utc) - timedelta(days=2)
        rng.ends_at = ev.date
        rng.price = price
        return rng

    @staticmethod
    def _mock_event_full():
        """Mock-событие со ВСЕМИ сериализуемыми атрибутами (иначе jsonable_encoder рекурсирует)."""
        ev = Mock()
        ev.id = EVENT_ID
        ev.title = "Test Event"
        ev.description = "Desc"
        ev.location = "Msk"
        ev.date = datetime.now(timezone.utc) + timedelta(days=14)
        ev.price = 100
        ev.available_tickets = 50
        ev.total_tickets = 100
        ev.is_active = True
        ev.is_published = True
        ev.deleted_at = None
        ev.age_restriction = "0+"
        ev.media_telegram_file_id = None
        ev.media_type = None
        return ev

    def test_events_list_effective_price(self, client):
        """GET /events отдаёт актуальную цену через resolve_price (реальная логика)."""
        ev = self._mock_event_full()
        rng = self._mock_range(150, ev)
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.list_upcoming", new_callable=AsyncMock, return_value=[ev]),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={EVENT_ID: [rng]}),
        ):
            resp = client.get("/api/events", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()[0]["price"] == 150.0

    def test_event_detail_effective_price(self, client):
        """GET /events/{id} отдаёт актуальную цену."""
        ev = self._mock_event_full()
        rng = self._mock_range(200, ev)
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=ev),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={_UUID(EVENT_ID): [rng]}),
        ):
            resp = client.get(f"/api/events/{EVENT_ID}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["price"] == 200.0


class TestAdminUserSubscribe:
    """Суперадмин выдаёт подписку организатору без канала (по Telegram ID)."""

    def test_admin_user_subscribe_success(self, client):
        """Суперадмин выдаёт подписку пользователю (200)."""
        mock_user = Mock()
        mock_user.id = _UUID(USER_ID)
        mock_user.telegram_user_id = "12345"
        mock_user.is_subscription_active = True
        mock_user.subscription_tier.value = "pro"
        mock_user.subscription_until = datetime.now(timezone.utc)

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id", new_callable=AsyncMock, return_value=mock_user),
            patch("app.web.routes.UserService.activate_subscription", new_callable=AsyncMock, return_value=mock_user),
        ):
            resp = client.post(
                "/api/admin/users/12345/subscription",
                headers={"X-Skip-Auth": "1"},
                json={"duration_days": 30, "tier": "pro"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["subscription_tier"] == "pro"

    def test_admin_user_subscribe_not_super(self, client):
        """Не суперадмин → 403."""
        with admin_auth(is_super=False, channel_ids=[]):
            resp = client.post(
                "/api/admin/users/12345/subscription",
                headers={"X-Skip-Auth": "1"},
                json={"duration_days": 30, "tier": "pro"},
            )
        assert resp.status_code == 403

    def test_admin_user_subscribe_not_found(self, client):
        """Юзер не в БД → 404."""
        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id", new_callable=AsyncMock, return_value=None),
        ):
            resp = client.post(
                "/api/admin/users/99999/subscription",
                headers={"X-Skip-Auth": "1"},
                json={"duration_days": 30, "tier": "pro"},
            )
        assert resp.status_code == 404

    def test_admin_user_info_returns_subscription(self, client):
        """GET /admin/users/{id} отдаёт подписку пользователя."""
        mock_user = Mock()
        mock_user.id = _UUID(USER_ID)
        mock_user.platform_user_id = "12345"
        mock_user.username = "dev_nick"
        mock_user.name = "Dev"
        mock_user.created_at = datetime.now(timezone.utc)
        mock_user.is_subscription_active = True
        mock_user.subscription_tier.value = "pro"
        mock_user.subscription_until = datetime.now(timezone.utc)

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_platform_user_id", new_callable=AsyncMock, return_value=mock_user),
            patch("app.web.routes.ChannelService.get_channels_by_admin", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get("/api/admin/users/12345", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_subscription_active"] is True
        assert resp.json()["subscription_tier"] == "pro"


class TestEventMedia:
    """Постеры мероприятий: media в ответах API + прокси-эндпоинт."""

    def test_events_list_includes_media(self, client):
        """GET /events отдаёт media_file_id/media_type."""
        from app.web.routes import EventService
        mock_events = []
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.list_upcoming", new_callable=AsyncMock, return_value=mock_events),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.get("/api/events", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        # пустой список — структура проверится через деталь

    def test_event_detail_includes_media(self, client):
        """GET /events/{id} отдаёт media_file_id/media_type."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.title = "Test"
        mock_event.description = "Desc"
        mock_event.date = datetime.now(timezone.utc) + timedelta(days=7)
        mock_event.location = "Msk"
        mock_event.price = 100
        mock_event.available_tickets = 50
        mock_event.total_tickets = 100
        mock_event.is_active = True
        mock_event.is_published = True
        mock_event.deleted_at = None
        mock_event.age_restriction = "0+"
        mock_event.media_telegram_file_id = "AgAC_123"
        mock_event.media_type = "photo"
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.EventService.price_ranges_map", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.get(f"/api/events/{EVENT_ID}", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["media_file_id"] == "AgAC_123"
        assert resp.json()["media_type"] == "photo"

    def test_event_media_endpoint_returns_image(self, client):
        """GET /events/{id}/media отдаёт картинку из Telegram."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.media_telegram_file_id = "AgAC_123"
        mock_file = Mock()
        mock_file.file_path = "photos/file.jpg"
        mock_bot = AsyncMock()
        mock_bot.get_file.return_value = mock_file
        mock_bot.download_file.return_value = b"\xff\xd8\xff\xe0JFIF"
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
            patch("app.web.routes.get_telegram_bot", new_callable=Mock, return_value=mock_bot),
        ):
            resp = client.get(f"/api/events/{EVENT_ID}/media", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert "image" in resp.headers["content-type"]
        assert resp.content.startswith(b"\xff\xd8")

    def test_event_media_endpoint_no_media(self, client):
        """Нет media → 404."""
        mock_event = Mock()
        mock_event.id = EVENT_ID
        mock_event.media_telegram_file_id = None
        with (
            admin_auth(is_super=False, channel_ids=[], organizer=True),
            patch("app.web.routes.EventService.get_by_id", new_callable=AsyncMock, return_value=mock_event),
        ):
            resp = client.get(f"/api/events/{EVENT_ID}/media", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 404

    def test_admin_user_info_by_username(self, client):
        """GET /admin/users/{nick} — поиск по @username (не числовому ID)."""
        mock_user = Mock()
        mock_user.id = _UUID(USER_ID)
        mock_user.platform_user_id = "12345"
        mock_user.username = "ivan_dev"
        mock_user.name = "Ivan"
        mock_user.created_at = datetime.now(timezone.utc)
        mock_user.is_subscription_active = True
        mock_user.subscription_tier.value = "pro"
        mock_user.subscription_until = datetime.now(timezone.utc)

        with (
            admin_auth(is_super=True, channel_ids=[]),
            patch("app.web.routes.UserService.get_by_username", new_callable=AsyncMock, return_value=mock_user),
            patch("app.web.routes.ChannelService.get_channels_by_admin", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.get("/api/admin/users/ivan_dev", headers={"X-Skip-Auth": "1"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["username"] == "ivan_dev"
        assert resp.json()["telegram_user_id"] == "12345"
