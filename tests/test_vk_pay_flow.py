"""VK Pay ticket-order API and settlement flow tests."""

import base64
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select

from app.core.models import Payment, Ticket, VKPayOrder
from app.core.vk_pay_service import VKPayOrderService
from app.web.vk_auth import compute_sign


def _vk_headers(user_id=5305539, app_id=123456, secret="test-vk-secret"):
    params = {
        "vk_app_id": str(app_id),
        "vk_user_id": str(user_id),
        "vk_ts": str(int(time.time())),
        "vk_ref": "catalog",
    }
    params["sign"] = compute_sign(params, secret)
    encoded = base64.b64encode(urlencode(sorted(params.items())).encode()).decode()
    return {"X-VK-Init-Data": encoded}


def _configure_vk(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vk_app_id", 123456)
    monkeypatch.setattr(settings, "vk_secret_key", "test-vk-secret")
    monkeypatch.setattr(settings, "vk_pay_enabled", True)
    monkeypatch.setattr(settings, "vk_pay_merchant_id", 654321)
    monkeypatch.setattr(settings, "vk_pay_client_id", "client-test")
    monkeypatch.setattr(settings, "vk_pay_app_secure_key", "app-secure-test")
    monkeypatch.setattr(settings, "vk_pay_merchant_private_key", "merchant-private-test")
    test_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = test_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setattr(settings, "vk_pay_notification_public_key", public_key)


async def test_vk_pay_disabled_fails_closed(db_client, sample_event, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vk_app_id", 123456)
    monkeypatch.setattr(settings, "vk_secret_key", "test-vk-secret")
    monkeypatch.setattr(settings, "vk_pay_enabled", False)

    response = db_client.post(
        f"/api/events/{sample_event.id}/vk-pay-order",
        headers=_vk_headers(),
        json={},
    )

    assert response.status_code == 503
    assert "VK Pay" in response.json()["detail"]


async def test_vk_paid_ticket_cannot_use_legacy_instant_purchase(db_client, sample_event, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vk_app_id", 123456)
    monkeypatch.setattr(settings, "vk_secret_key", "test-vk-secret")
    response = db_client.post(
        f"/api/events/{sample_event.id}/buy",
        headers=_vk_headers(),
        json={},
    )
    assert response.status_code == 409
    assert "VK Pay" in response.json()["detail"]


async def test_vk_only_order_reserves_seat_without_issuing_ticket(
    db_client, db_session, sample_event, monkeypatch,
):
    _configure_vk(monkeypatch)
    available_before = sample_event.available_tickets

    response = db_client.post(
        f"/api/events/{sample_event.id}/vk-pay-order",
        headers=_vk_headers(),
        json={},
    )

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["payment"]["action"] == "pay-to-service"
    assert result["payment"]["params"]["merchant_id"] == 654321
    order = (await db_session.execute(select(VKPayOrder))).scalar_one()
    assert order.status == "pending"
    assert order.amount == 1000
    assert sample_event.available_tickets == available_before
    reserved = await VKPayOrderService(db_session).reserved_seats_map([sample_event.id])
    assert reserved[sample_event.id] == 1
    assert (await db_session.execute(select(Ticket))).scalars().all() == []
    assert (await db_session.execute(select(Payment))).scalars().all() == []


async def test_vk_callback_rejects_bad_signature_without_issuing_ticket(
    db_client, db_session, sample_event, monkeypatch,
):
    _configure_vk(monkeypatch)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    from app.config import settings
    monkeypatch.setattr(settings, "vk_pay_notification_public_key", public_key)

    order_response = db_client.post(
        f"/api/events/{sample_event.id}/vk-pay-order",
        headers=_vk_headers(),
        json={},
    )
    assert order_response.status_code == 201, order_response.text
    payload = {
        "body": {
            "transaction_id": "8ce5fa9d-9ac9-4b9a-8be9-a1073017c860",
            "notify_type": "TRANSACTION_STATUS",
            "issuer_id": order_response.json()["order_id"],
            "amount": "1000.00",
            "currency": "RUB",
            "merchant_id": 654321,
            "user_info": {"user_id": "5305539"},
            "status": "paid",
        },
        "header": {"status": "OK", "ts": str(int(time.time())), "client_id": "client-test"},
    }
    data = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    response = db_client.post(
        "/api/vk-pay/notifications",
        data={"data": data, "signature": base64.b64encode(b"forged").decode(), "version": "2-03"},
    )
    assert response.status_code == 400
    assert (await db_session.execute(select(Ticket))).scalars().all() == []
    order = (await db_session.execute(select(VKPayOrder))).scalar_one()
    assert order.status == "pending"


async def test_verified_vk_callback_fulfills_exactly_once(
    db_client, db_session, sample_event, monkeypatch,
):
    _configure_vk(monkeypatch)
    vk_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = vk_private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    from app.config import settings
    monkeypatch.setattr(settings, "vk_pay_notification_public_key", public_key)

    order_response = db_client.post(
        f"/api/events/{sample_event.id}/vk-pay-order",
        headers=_vk_headers(),
        json={},
    )
    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["order_id"]
    order = (await db_session.execute(select(VKPayOrder))).scalar_one()
    order.status = "expired"
    order.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.flush()
    # Turning off new checkout must not block settlement of an in-flight order.
    monkeypatch.setattr(settings, "vk_pay_enabled", False)

    payload = {
        "body": {
            "transaction_id": "8ce5fa9d-9ac9-4b9a-8be9-a1073017c860",
            "notify_type": "TRANSACTION_STATUS",
            "issuer_id": order_id,
            "amount": "1000.00",
            "currency": "RUB",
            "merchant_id": 654321,
            "user_info": {"user_id": "5305539"},
            "status": "paid",
        },
        "header": {"status": "OK", "ts": str(int(time.time())), "client_id": "client-test"},
    }
    data = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    signature = base64.b64encode(
        vk_private_key.sign(data.encode(), padding.PKCS1v15(), hashes.SHA1())
    ).decode()

    callback = db_client.post(
        "/api/vk-pay/notifications",
        data={"data": data, "signature": signature, "version": "2-03"},
    )
    assert callback.status_code == 200, callback.text
    ack = callback.json()
    assert ack["version"] == "2-03"
    ack_payload = json.loads(base64.b64decode(ack["data"]))
    assert ack_payload["body"]["notify_type"] == "payment_delivered"
    assert ack["signature"] == hashlib.sha1(
        (ack["data"] + "merchant-private-test").encode()
    ).hexdigest()

    duplicate = db_client.post(
        "/api/vk-pay/notifications",
        data={"data": data, "signature": signature, "version": "2-03"},
    )
    assert duplicate.status_code == 200
    assert len((await db_session.execute(select(Ticket))).scalars().all()) == 1
    payment = (await db_session.execute(select(Payment))).scalar_one()
    assert payment.status.value == "completed"
    assert payment.provider == "vk_pay"
    assert payment.provider_transaction_id == payload["body"]["transaction_id"]

    cancel = db_client.post(
        f"/api/tickets/{payment.ticket_id}/cancel",
        headers=_vk_headers(),
    )
    assert cancel.status_code == 409
    assert "VK Pay" in cancel.json()["detail"]
    assert payment.status.value == "completed"
