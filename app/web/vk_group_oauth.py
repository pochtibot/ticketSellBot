"""Small helpers for VK community Authorization Code Flow."""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode


VK_GROUP_OAUTH_SCOPES = "wall,stories,photos,app_widget,messages,docs,manage"
VK_GROUP_OAUTH_AUTHORIZE_URL = "https://oauth.vk.com/authorize"


def make_state(*, user_id: str, group_id: str, title: str | None, secret: str, now: int | None = None) -> str:
    """Create a short-lived authenticated state value (10-minute lifetime)."""
    payload = {
        "u": user_id,
        "g": group_id,
        "t": (title or "")[:255],
        "exp": (int(time.time()) if now is None else now) + 600,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
    ).rstrip(b"=").decode("ascii")
    signature = hmac.new(secret.encode(), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def read_state(state: str, *, secret: str, now: int | None = None) -> dict | None:
    """Verify state signature and expiry; return only its expected fields."""
    try:
        encoded, supplied_signature = state.split(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode("ascii"), hashlib.sha256).digest()
        padded_signature = supplied_signature + "=" * (-len(supplied_signature) % 4)
        if not hmac.compare_digest(
            expected, base64.urlsafe_b64decode(padded_signature.encode("ascii"))
        ):
            return None
        payload_bytes = base64.urlsafe_b64decode(
            (encoded + "=" * (-len(encoded) % 4)).encode("ascii")
        )
        payload = json.loads(payload_bytes)
        current_time = int(time.time()) if now is None else now
        if not isinstance(payload, dict) or int(payload.get("exp", 0)) < current_time:
            return None
        if not payload.get("u") or not str(payload.get("g", "")).isdigit():
            return None
        return {
            "user_id": str(payload["u"]),
            "group_id": str(payload["g"]),
            "title": str(payload.get("t") or "")[:255] or None,
        }
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def build_authorize_url(*, app_id: int, redirect_uri: str, group_id: str, state: str) -> str:
    """Build VK's documented community authorization-code URL."""
    params = {
        "client_id": app_id,
        "display": "page",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": VK_GROUP_OAUTH_SCOPES,
        "state": state,
        "group_ids": group_id,
    }
    return f"{VK_GROUP_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def extract_group_token(payload: dict, group_id: str) -> str | None:
    """Extract only the token mapped to the requested community."""
    access_tokens = payload.get("access_tokens")
    if isinstance(access_tokens, dict):
        candidate = access_tokens.get(group_id)
        if isinstance(candidate, dict):
            candidate = candidate.get("access_token")
        if isinstance(candidate, str) and candidate:
            return candidate
    if isinstance(access_tokens, list):
        for item in access_tokens:
            if isinstance(item, dict) and str(item.get("group_id")) == group_id:
                candidate = item.get("access_token")
                if isinstance(candidate, str) and candidate:
                    return candidate
    return None
