"""
initData validation for Telegram Mini App.

Flow:
1. Mini App opens → gets raw initData from window.Telegram.WebApp.initData
2. Frontend sends it as X-Init-Data header on every API call
3. Backend validates HMAC-SHA256 signature using bot token
4. On success: returns validated user data
5. On failure: raises HTTP 401

Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status

from app.config import settings
from app.core.database import async_session_factory
from app.core.models import PlatformType
from app.core.services import ChannelService, EventService, UserService, VKGroupService

# Maximum age of initData in seconds (24 hours)
_MAX_INIT_DATA_AGE = 86400


def _extract_init_data(init_data: str) -> dict[str, list[str]]:
    """Parse raw initData query string into a dict of key→[values]."""
    return parse_qs(init_data, keep_blank_values=True)


def _build_data_check_string(params: dict[str, list[str]]) -> str:
    """Build the data_check_string from all params except 'hash'.

    Sorted alphabetically by key, joined as key=value\\n.
    """
    items = []
    for key, values in params.items():
        if key == "hash":
            continue
        # decode_url for values
        value = unquote(values[0])
        items.append(f"{key}={value}")
    items.sort()
    return "\n".join(items)


def _compute_signature(data_check_string: str, bot_token: str) -> str:
    """Compute HMAC-SHA256 signature of data_check_string using bot token.

    1. Derive secret_key: HMAC-SHA256(b"WebAppData", bot_token)
    2. Compute signature: HMAC-SHA256(secret_key, data_check_string)
    """
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()
    signature = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return signature


def validate_init_data(
    x_init_data: str | None = Header(None),
    x_vk_init_data: str | None = Header(None),
    x_skip_auth: str | None = Header(None),
) -> dict:
    """Validate platform auth: Telegram initData или VK launch params.

    TG: X-Init-Data (HMAC по bot token). VK: X-VK-Init-Data (launch params + sign).

    Returns parsed auth dict (with 'user' key and 'platform') on success.
    Raises 401 on invalid/missing/expired auth.

    If X-Skip-Auth header is set to any truthy value (for local dev/testing),
    returns a placeholder user dict. NEVER enable in production.
    """
    # Normalize: when called outside FastAPI request context,
    # Header(None) stays as a Header object (which is truthy).
    # Treat it the same as "header not present".
    if not isinstance(x_skip_auth, (str, type(None))):
        x_skip_auth = None
    if not isinstance(x_vk_init_data, (str, type(None))):
        x_vk_init_data = None

    # Skip auth for local development / testing — только если явно разрешено.
    # В production (allow_skip_auth=False по умолчанию) заголовок игнорируется,
    # чтобы клиент не мог обойти аутентификацию сам (X-Skip-Auth — не секрет).
    if x_skip_auth and settings.allow_skip_auth:
        return {
            "user": {"id": 12345, "first_name": "Dev", "last_name": "User"},
            "platform": "telegram",
            "auth_date": int(time.time()),
            "hash": "skip",
        }

    # VK Mini App: launch params (base64) в X-VK-Init-Data
    if x_vk_init_data:
        from app.web.vk_auth import validate_vk_init_data
        return validate_vk_init_data(x_vk_init_data)

    if not x_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Init-Data header",
        )

    # Check that bot token is available (needed for signature validation)
    bot_token = settings.telegram_token
    if not bot_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TELEGRAM_TOKEN not configured",
        )

    params = _extract_init_data(x_init_data)

    # Check hash presence
    if "hash" not in params:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing hash in initData",
        )

    provided_hash = params["hash"][0]

    # Build data_check_string and compute signature
    data_check_string = _build_data_check_string(params)
    computed_signature = _compute_signature(data_check_string, bot_token)

    # Compare signatures (constant-time comparison via hmac.compare_digest)
    if not hmac.compare_digest(computed_signature, provided_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid initData signature",
        )

    # Check auth_date freshness
    if "auth_date" in params:
        auth_date = int(params["auth_date"][0])
        if time.time() - auth_date > _MAX_INIT_DATA_AGE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="initData expired",
            )

    # Parse user field into a dict
    result = {"platform": "telegram"}
    for key, values in params.items():
        val = values[0]
        if key == "user":
            try:
                val = json.loads(unquote(val))
            except (json.JSONDecodeError, ValueError):
                # If user can't be parsed, keep it as string
                pass
        result[key] = val

    return result


# ═══════════════════════════════════════════════════════════════
# Current user + role resolution (web admin panel)
# ═══════════════════════════════════════════════════════════════

def _is_super_admin(telegram_user_id: str) -> bool:
    """Super-admin is a pure config check — Telegram ID in ADMIN_TELEGRAM_IDS."""
    if not settings.admin_telegram_ids:
        return False
    admin_ids = [x.strip() for x in settings.admin_telegram_ids.split(",") if x.strip()]
    return telegram_user_id in admin_ids


@dataclass
class CurrentUser:
    """Resolved current user with role info for the web app."""

    user_id: UUID
    telegram_user_id: str
    name: str | None
    is_super_admin: bool
    platform: PlatformType = PlatformType.telegram
    #: Каналы с активной подпиской, где пользователь — админ (channel_admins).
    managed_channel_ids: list[UUID] = field(default_factory=list)
    #: Организатор без канала — есть активная подписка пользователя.
    is_organizer: bool = False
    #: ID мероприятий, где пользователь — соработник (event_managers).
    manager_event_ids: list[UUID] = field(default_factory=list)
    #: ID VK-групп организатора (self-service).
    vk_group_ids: list[UUID] = field(default_factory=list)
    #: Есть ли у организатора площадка (TG-канал с активной подпиской ИЛИ VK-группа).
    has_group: bool = False

    @property
    def is_admin(self) -> bool:
        return self.is_super_admin or self.is_organizer or bool(self.managed_channel_ids)

    @property
    def role(self) -> str:
        if self.is_super_admin:
            return "super_admin"
        if self.is_organizer or self.managed_channel_ids or self.platform is PlatformType.vk:
            return "organizer"
        return "user"

    @property
    def is_creator(self) -> bool:
        """Может создавать/управлять мероприятиями. Организатор, НО не суперадмин.

        Суперадмин эксклюзивен — он не организатор (role=super_admin приоритетен).
        """
        return not self.is_super_admin and (
            self.is_organizer
            or bool(self.managed_channel_ids)
            or self.platform is PlatformType.vk
        )

    @property
    def organizer_with_group(self) -> bool:
        return self.role == "organizer" and self.has_group

    def can_manage(self, channel_id: UUID) -> bool:
        return channel_id in self.managed_channel_ids

    def can_manage_event(self, event_id: UUID) -> bool:
        """Является ли пользователь соработником (manager) мероприятия."""
        return event_id in self.manager_event_ids


async def get_current_user(auth_data: dict = Depends(validate_init_data)) -> CurrentUser:
    """Resolve the initData user into a CurrentUser with role.

    NOTE (deliberate simplification vs the bot): admin status is DB-only —
    channel_admins membership + active subscription. The bot additionally
    verifies via Telegram get_chat_member and auto-removes stale admins;
    the web cannot (no synchronous bot access). channel_admins stays fresh
    because the bot syncs it on subscribe / on_chat_member_update / change_admin.
    """
    user_data = auth_data.get("user", {})
    platform_user_id = str(user_data.get("id", "0"))
    name = user_data.get("first_name", "")
    username = user_data.get("username")  # Telegram ник (без @) — для поиска суперадмином

    # Платформа из auth_data: telegram (по умолчанию) или vk (launch params)
    platform_name = auth_data.get("platform", "telegram")
    platform = PlatformType(platform_name)

    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=platform,
            platform_user_id=platform_user_id,
            name=name,
            username=username,
        )

        channel_svc = ChannelService(session)
        raw_ids = await channel_svc.get_channel_ids_by_admin(platform_user_id)
        managed = [
            cid for cid in raw_ids
            if await channel_svc.is_subscription_valid(cid)
        ]
        # Организатор без канала: активная подписка пользователя
        is_organizer = await user_svc.is_subscription_valid(user.id)
        # Соработник: ID мероприятий, где пользователь — менеджер
        event_svc = EventService(session)
        manager_event_ids = await event_svc.get_manager_event_ids(user.id)
        # VK-группы организатора (self-service) — площадка
        group_svc = VKGroupService(session)
        vk_groups = await group_svc.list_vk_groups(user.id)
        vk_group_ids = [g.id for g in vk_groups]
        # Persist the row if get_or_create inserted a new user (read-only requests
        # still carry this dependency); commit is a no-op when nothing changed.
        await session.commit()

    # Super-admin is configured by Telegram IDs; a VK user with the same
    # numeric ID must never inherit Telegram global privileges.
    is_super = platform is PlatformType.telegram and _is_super_admin(platform_user_id)
    return CurrentUser(
        user_id=user.id,
        telegram_user_id=platform_user_id,
        name=user.name,
        platform=platform,
        is_super_admin=is_super,
        managed_channel_ids=managed,
        is_organizer=is_organizer,
        manager_event_ids=manager_event_ids,
        vk_group_ids=vk_group_ids,
        # Площадка: канал с активной подпиской ИЛИ VK-группа (суперадмин эксклюзивен)
        has_group=(not is_super) and (bool(managed) or bool(vk_group_ids)),
    )


async def require_admin(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к панели администратора",
        )
    return current


async def require_super_admin(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется супер-администратор",
        )
    return current
