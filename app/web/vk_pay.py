"""VK Pay request signing and signed notification helpers."""

import base64
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


VK_PAY_VERSION = 2
VK_PAY_NOTIFICATION_VERSION = "2-03"


def _canonical_value(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _app_sign(params: dict[str, Any], app_secure_key: str) -> str:
    # VK's canonical parameter string sorts keys, omits action, and emits
    # string values without quotes. Nested objects use compact sorted JSON.
    canonical = "".join(
        f"{key}={_canonical_value(value)}"
        for key, value in sorted(params.items())
        if key != "action"
    )
    return hashlib.md5((canonical + app_secure_key).encode("utf-8")).hexdigest()


def _merchant_sign(merchant_data: str, merchant_private_key: str) -> str:
    # VK's published signing vector is a 40-character SHA-1 digest, despite
    # the prose in the current page referring to SHA-256.
    return hashlib.sha1((merchant_data + merchant_private_key).encode("utf-8")).hexdigest()


def build_open_pay_form_params(
    *,
    app_id: int,
    app_secure_key: str,
    merchant_id: int,
    merchant_private_key: str,
    order_id: str,
    amount: str | Decimal,
    user_id: int,
    description: str,
    timestamp: int,
) -> dict[str, Any]:
    """Build server-signed VKWebAppOpenPayForm parameters (pay-to-service)."""
    try:
        money = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Некорректная сумма платежа") from exc
    if money < Decimal("1.00"):
        raise ValueError("Минимальная сумма оплаты через VK Pay — 1 ₽")
    if not money.is_finite() or len(description) > 50:
        raise ValueError("Некорректные параметры платежа")

    merchant_payload = {
        "amount": format(money, ".2f"),
        "currency": "RUB",
        "order_id": str(order_id),
        "ts": int(timestamp),
    }
    merchant_data = base64.b64encode(
        json.dumps(merchant_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).decode("ascii")
    merchant_sign = _merchant_sign(merchant_data, merchant_private_key)
    data = {
        "currency": "RUB",
        "merchant_data": merchant_data,
        "merchant_sign": merchant_sign,
        "order_id": str(order_id),
        "ts": str(int(timestamp)),
    }
    params: dict[str, Any] = {
        "amount": format(money, ".2f"),
        "data": data,
        "description": description,
        "merchant_id": int(merchant_id),
        "user_id": int(user_id),
        "version": VK_PAY_VERSION,
    }
    params["sign"] = _app_sign(params, app_secure_key)
    return {
        "app_id": int(app_id),
        "action": "pay-to-service",
        "params": params,
    }


def is_valid_notification_public_key(public_key_pem: str) -> bool:
    try:
        key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        return isinstance(key, rsa.RSAPublicKey)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return False


def verify_notification_signature(data: str, signature: str, public_key_pem: str) -> bool:
    """Verify VK Pay's RSA/SHA-1 signature over the original base64 data field."""
    try:
        key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(key, rsa.RSAPublicKey):
            return False
        decoded_signature = base64.b64decode(signature, validate=True)
        key.verify(decoded_signature, data.encode("ascii"), padding.PKCS1v15(), hashes.SHA1())
        return True
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
        return False


def decode_notification(data: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(data, validate=True)
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Некорректное VK Pay уведомление") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), dict):
        raise ValueError("Некорректная структура VK Pay уведомления")
    return payload


def build_notification_ack(
    *,
    transaction_id: str,
    client_id: str,
    merchant_private_key: str,
    timestamp: int,
    notification: str = "payment_delivered",
) -> dict[str, str]:
    """Build and sign the response VK Pay expects for a transaction notification."""
    data_payload = {
        "body": {
            "transaction_id": transaction_id,
            "notify_type": notification,
        },
        "header": {
            "status": "OK",
            "ts": int(timestamp),
            "client_id": str(client_id),
        },
    }
    data = base64.b64encode(
        json.dumps(data_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    signature = _merchant_sign(data, merchant_private_key)
    return {"version": VK_PAY_NOTIFICATION_VERSION, "data": data, "signature": signature}
