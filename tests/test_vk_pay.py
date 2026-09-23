"""VK Pay merchant signature contract tests (VK Pay docs, 2026-09)."""

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.web.vk_pay import (
    build_notification_ack,
    _app_sign,
    build_open_pay_form_params,
    decode_notification,
    _merchant_sign,
    is_valid_notification_public_key,
    verify_notification_signature,
)


def test_vk_app_signature_matches_published_documentation_vector():
    params = {
        "amount": 1.5,
        "data": {
            "currency": "RUB",
            "merchant_data": "eyJvcmRlcl9pZCI6IjI1NTMxIiwidHMiOiIxNTM5MzI5NzcwIiwiYW1vdW50IjoxLjUsImN1cnJlbmN5IjoiUlVCIn0=",
            "merchant_sign": "63d5dce9d2c9d29198ba12ba3f8e270e6606a221",
            "order_id": "25531",
            "ts": "1539329770",
        },
        "description": "Test Payment",
        "merchant_id": 617001,
        "version": 2,
    }
    assert _app_sign(params, "ug3CQvCkfRyI6svLVpC") == "818964335a550e39d9a1dd0d752e60ab"


def test_merchant_signature_matches_published_documentation_vector():
    merchant_data = "eyJvcmRlcl9pZCI6IjE1NTQzODQ0NTEuODQ3NDc2NjYiLCJjYXNoYmFjayI6eyJwYXlfdGltZSI6MTU1NDM4NDU3MSwiYW1vdW50X3BlcmNlbnQiOiIzMCJ9LCJ0cyI6MTU1NDM4NDQ1MSwiYW1vdW50IjoiMSIsImN1cnJlbmN5IjoiUlVCIn0="
    key = "627fdbfa24232a5b62f9c295baa93f7db9752873"
    assert _merchant_sign(merchant_data, key) == "86ebbd9e89f81e62db6e724707ace59b27fc4756"


def test_build_open_pay_form_params_signs_server_values():
    params = build_open_pay_form_params(
        app_id=123456,
        app_secure_key="app-secure",
        merchant_id=654321,
        merchant_private_key="merchant-private",
        order_id="order-1",
        amount="15.50",
        user_id="5305539",
        description="Билет · Тест",
        timestamp=1_800_000_000,
    )

    assert params["app_id"] == 123456
    assert params["action"] == "pay-to-service"
    payment_params = params["params"]
    assert payment_params["merchant_id"] == 654321
    assert payment_params["version"] == 2
    assert payment_params["user_id"] == 5305539

    data = payment_params["data"]
    assert data["currency"] == "RUB"
    assert data["order_id"] == "order-1"
    assert data["ts"] == str(1_800_000_000)
    merchant_data = base64.b64decode(data["merchant_data"]).decode()
    merchant_payload = json.loads(merchant_data)
    assert merchant_payload == {
        "order_id": "order-1",
        "ts": 1_800_000_000,
        "amount": "15.50",
        "currency": "RUB",
    }
    assert data["merchant_sign"] == hashlib.sha1(
        (data["merchant_data"] + "merchant-private").encode()
    ).hexdigest()

    # VK Pay app signature covers canonical params (without action).
    signed = dict(payment_params)
    supplied = signed.pop("sign")
    canonical = "".join(
        f"{key}={json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) if isinstance(value, dict) else str(value)}"
        for key, value in sorted(signed.items())
    )
    expected = hashlib.md5((canonical + "app-secure").encode()).hexdigest()
    assert supplied == expected


def test_build_open_pay_form_params_rejects_invalid_amount():
    with pytest.raises(ValueError):
        build_open_pay_form_params(
            app_id=1,
            app_secure_key="key",
            merchant_id=2,
            merchant_private_key="merchant",
            order_id="order-1",
            amount="0.99",
            user_id="77",
            description="Ticket",
            timestamp=1_800_000_000,
        )


def test_verify_notification_signature_and_decode_payload():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    data = base64.b64encode(json.dumps({"body": {"notify_type": "TRANSACTION_STATUS"}}).encode()).decode()
    signature = base64.b64encode(
        private_key.sign(data.encode(), padding.PKCS1v15(), hashes.SHA1())
    ).decode()

    assert is_valid_notification_public_key(public_key)
    assert not is_valid_notification_public_key("invalid key")
    assert verify_notification_signature(data, signature, public_key)
    payload = decode_notification(data)
    assert payload["body"]["notify_type"] == "TRANSACTION_STATUS"
    assert not verify_notification_signature(data, base64.b64encode(b"invalid").decode(), public_key)


def test_decode_notification_rejects_invalid_payload():
    with pytest.raises(ValueError):
        decode_notification("not base64")


def test_notification_ack_is_signed_with_merchant_key():
    response = build_notification_ack(
        transaction_id="transaction-1",
        client_id="client-1",
        merchant_private_key="merchant-private",
        timestamp=1_800_000_000,
    )

    data = response["data"]
    decoded = json.loads(base64.b64decode(data))
    assert decoded["body"] == {
        "transaction_id": "transaction-1",
        "notify_type": "payment_delivered",
    }
    assert decoded["header"] == {
        "status": "OK",
        "ts": 1_800_000_000,
        "client_id": "client-1",
    }
    assert response["version"] == "2-03"
    assert response["signature"] == hashlib.sha1(
        (data + "merchant-private").encode()
    ).hexdigest()
