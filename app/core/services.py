import uuid
import logging
import time
import secrets
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select, func, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import (
    User, UserIdentity, LinkCode, Event, EventManager, EventPublication, Ticket, Payment, VKPayOrder,
    Channel, ChannelAdmin, VKGroup, EventUpgrade, PromoCode, DiscountType, EventPriceRange,
    TicketStatus, PaymentStatus, PlatformType, SubscriptionTier, PeriodUnit,
)
from dateutil.relativedelta import relativedelta
from app.core.schemas import EventOut, EventShortOut, TicketOut, UserOut

logger = logging.getLogger("ticketbot.services")


def _ms(start: float) -> int:
    """Convert perf_counter start to integer milliseconds."""
    return int((time.perf_counter() - start) * 1000)


# ─── User Service ────────────────────────────────────────────────────────────

class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, platform: PlatformType, platform_user_id: str, name: Optional[str] = None, username: Optional[str] = None) -> User:
        """Find existing user by platform+id, or create a new one.

        Резолвинг через user_identities: если (platform, platform_user_id) привязана
        к каноническому пользователю (линковка организатора TG↔VK), возвращаем канон.
        Для "legacy" пользователей (созданных до фичи user_identities) — backfill identity.

        username: ник (Telegram username без @) — сохраняется при создании или
        дописывается, если пришёл новый (поиск суперадмином по нику).
        """
        start = time.perf_counter()

        # 1. Канонический резолвинг через identity
        identity = await self._find_identity(platform, platform_user_id)
        if identity is not None:
            user = await self.session.get(User, identity.user_id)
            if user is not None and user.deleted_at is None:
                self._maybe_set_username(user, username)
                logger.info("", extra={
                    "event_type": "user.found",
                    "platform": platform.value,
                    "user_id": platform_user_id,
                    "canonical_user_id": str(user.id),
                    "status": "success",
                    "duration_ms": _ms(start),
                })
                return user

        # 2. Legacy-пользователь (без identity) — backfill
        stmt = select(User).where(
            and_(User.platform == platform, User.platform_user_id == platform_user_id)
        )
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is not None and user.deleted_at is None:
            await self._ensure_identity(user.id, platform, platform_user_id)
            self._maybe_set_username(user, username)
            return user

        # 2а. Удалённый пользователь (deleted_at задан) — создаём ЧИСТЫЙ аккаунт.
        #     Старый пользователь остаётся удалённым; identity переназначается на нового.
        if user is not None and user.deleted_at is not None:
            logger.info("", extra={
                "event_type": "user.recreated_after_delete",
                "platform": platform.value,
                "user_id": platform_user_id,
                "old_user_id": str(user.id),
                "status": "success",
                "duration_ms": _ms(start),
            })
            return await self._create_fresh_user(
                platform=platform,
                platform_user_id=platform_user_id,
                name=name,
                username=username,
                start=start,
            )

        # 3. Новый пользователь: user + identity
        return await self._create_fresh_user(
            platform=platform,
            platform_user_id=platform_user_id,
            name=name,
            username=username,
            start=start,
        )

    async def _create_fresh_user(
        self,
        platform: PlatformType,
        platform_user_id: str,
        name: Optional[str],
        username: Optional[str],
        start: float,
    ) -> User:
        """Создать нового пользователя и переназначить identity на него.

        Используется для нового пользователя и для «пересоздания» после удаления
        аккаунта (п.1.1.10): старый (удалённый) пользователь остаётся в БД,
        identity (platform, platform_user_id) привязывается к новому аккаунту.
        """
        # Переназначить существующую identity (если была привязана к удалённому)
        identity = await self._find_identity(platform, platform_user_id)
        user = User(
            platform=platform,
            platform_user_id=platform_user_id,
            name=name,
            username=username or None,
        )
        self.session.add(user)
        await self.session.flush()
        if identity is not None:
            identity.user_id = user.id
            await self.session.flush()
        else:
            await self._ensure_identity(user.id, platform, platform_user_id)
        logger.info("", extra={
            "event_type": "user.created",
            "platform": platform.value,
            "user_id": platform_user_id,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return user

    @staticmethod
    def _maybe_set_username(user: User, username: str | None) -> None:
        """Дописать ник пользователю, если пришёл и отличается (иначе не трогаем)."""
        if username and username != user.username:
            user.username = username

    async def get_by_platform_user_id(self, platform: PlatformType, platform_user_id: str) -> User | None:
        """Find a user by platform+id WITHOUT creating (unlike get_or_create).

        Used for admin lookups — do not create a side-effect user on view.
        Резолвит через identity: привязанная площадка ведёт на канонического пользователя.
        """
        identity = await self._find_identity(platform, platform_user_id)
        if identity is not None:
            return await self.session.get(User, identity.user_id)
        stmt = select(User).where(
            and_(User.platform == platform, User.platform_user_id == platform_user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        """Find a user by Telegram username (без @), WITHOUT creating.

        Для поиска суперадмином организатора по нику.
        """
        uname = username.lstrip("@").strip().lower()
        if not uname:
            return None
        stmt = select(User).where(
            func.lower(User.username) == uname,
            User.deleted_at.is_(None),
        ).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ─── Каноническая идентичность (user_identities) ─────────────

    async def _find_identity(self, platform: PlatformType, platform_user_id: str) -> UserIdentity | None:
        stmt = select(UserIdentity).where(
            and_(
                UserIdentity.platform == platform,
                UserIdentity.platform_user_id == platform_user_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _ensure_identity(
        self, user_id: uuid.UUID, platform: PlatformType, platform_user_id: str
    ) -> UserIdentity:
        """Создать identity для пользователя, если её нет (идемпотентно)."""
        existing = await self._find_identity(platform, platform_user_id)
        if existing is not None:
            return existing
        identity = UserIdentity(
            user_id=user_id,
            platform=platform,
            platform_user_id=platform_user_id,
        )
        self.session.add(identity)
        await self.session.flush()
        return identity

    async def link_identity(
        self,
        canonical_user_id: uuid.UUID,
        platform: PlatformType,
        platform_user_id: str,
    ) -> None:
        """Привязать площадку (platform, platform_user_id) к каноническому пользователю.

        Идемпотентно: повторная привязка той же identity к тому же канону — не ошибка.
        ValueError: identity уже привязана к другому пользователю.
        """
        existing = await self._find_identity(platform, platform_user_id)
        if existing is not None:
            if existing.user_id == canonical_user_id:
                return
            raise ValueError(
                "Эта площадка уже привязана к другому пользователю"
            )
        identity = UserIdentity(
            user_id=canonical_user_id,
            platform=platform,
            platform_user_id=platform_user_id,
        )
        self.session.add(identity)
        await self.session.flush()
        logger.info("", extra={
            "event_type": "user.identity_linked",
            "canonical_user_id": str(canonical_user_id),
            "platform": platform.value,
            "platform_user_id": platform_user_id,
            "status": "success",
            "duration_ms": _ms(time.perf_counter()),
        })

    async def list_identities(self, user_id: uuid.UUID) -> list[UserIdentity]:
        """Все способы входа пользователя (TG/VK/...)."""
        result = await self.session.execute(
            select(UserIdentity).where(UserIdentity.user_id == user_id)
        )
        return list(result.scalars().all())

    # ─── Коды линковки (organizer-only) ──────────────────────────

    async def create_link_code(
        self,
        canonical_user_id: uuid.UUID,
        target_platform: PlatformType,
        ttl_minutes: int = 10,
    ) -> str:
        """Создать одноразовый короткоживущий код для привязки площадки.

        Код вводится на целевой площадке (VK), привязывает её identity к канону.
        """
        code = secrets.token_hex(4).upper()  # 8 символов
        link = LinkCode(
            code=code,
            user_id=canonical_user_id,
            target_platform=target_platform,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        )
        self.session.add(link)
        await self.session.flush()
        return code

    async def consume_link_code(
        self,
        code: str,
        platform: PlatformType,
        platform_user_id: str,
        current_user_id: uuid.UUID | None = None,
    ) -> bool:
        """Использовать код линковки: привязать (platform, platform_user_id) к канону кода.

        Правила:
        - identity свободна → создаём привязку к канону кода (link.user_id).
        - identity уже у канона кода → идемпотентно (код помечается использованным).
        - identity занята текущим пользователем (например, VK-вход создал ему свою
          identity) → ре-биндинг: переназначаем identity на канон кода.
        - identity занята третьим лицом (не current_user_id) → отказ.

        ValueError: код не найден / истёк / уже использован / не для этой площадки,
        либо identity занята чужим пользователем.
        """
        code = code.strip().upper()
        result = await self.session.execute(
            select(LinkCode).where(LinkCode.code == code)
        )
        link = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)

        if link is None:
            raise ValueError("Код не найден")
        if link.consumed_at is not None:
            raise ValueError("Код уже использован")
        if link.expires_at < now:
            raise ValueError("Код истёк")
        if link.target_platform != platform:
            raise ValueError("Код предназначен для другой площадки")

        existing = await self._find_identity(platform, platform_user_id)
        if existing is not None:
            if existing.user_id == link.user_id:
                # Уже привязана к канону кода — идемпотентно
                link.consumed_at = now
                await self.session.flush()
                return True
            if current_user_id is not None and existing.user_id == current_user_id:
                # identity принадлежит текущему пользователю → ре-биндинг на канон кода
                existing.user_id = link.user_id
                link.consumed_at = now
                await self.session.flush()
                logger.info("", extra={
                    "event_type": "user.identity_rebound_by_code",
                    "canonical_user_id": str(link.user_id),
                    "platform": platform.value,
                    "platform_user_id": platform_user_id,
                    "status": "success",
                    "duration_ms": _ms(time.perf_counter()),
                })
                return True
            raise ValueError("Эта площадка уже привязана к другому пользователю")

        identity = UserIdentity(
            user_id=link.user_id,
            platform=platform,
            platform_user_id=platform_user_id,
        )
        self.session.add(identity)
        link.consumed_at = now
        await self.session.flush()
        logger.info("", extra={
            "event_type": "user.identity_linked_by_code",
            "canonical_user_id": str(link.user_id),
            "platform": platform.value,
            "platform_user_id": platform_user_id,
            "status": "success",
            "duration_ms": _ms(time.perf_counter()),
        })
        return True

    async def update_name(self, user_id: uuid.UUID, name: str | None) -> User | None:
        """Update a user's display name. Returns updated user or None."""
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        user.name = name
        await self.session.flush()
        return user

    # ─── Подписка пользователя (организатор без канала) ───────────

    async def activate_subscription(
        self,
        user_id: uuid.UUID,
        days: int = 30,
        tier: SubscriptionTier | None = None,
    ) -> User | None:
        """Активировать подписку пользователя (срок от текущей даты)."""
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        user.is_subscription_active = True
        user.subscription_until = datetime.now(timezone.utc) + timedelta(days=days)
        if tier is not None:
            user.subscription_tier = tier
        await self.session.flush()
        return user

    async def deactivate_subscription(self, user_id: uuid.UUID) -> User | None:
        """Отключить подписку пользователя."""
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        user.is_subscription_active = False
        user.subscription_until = None
        await self.session.flush()
        return user

    async def is_subscription_valid(self, user_id: uuid.UUID) -> bool:
        """Активна ли подписка пользователя (не просрочена)."""
        user = await self.session.get(User, user_id)
        if user is None or not user.is_subscription_active:
            return False
        if user.subscription_until and user.subscription_until < datetime.now(timezone.utc):
            user.is_subscription_active = False
            user.subscription_until = None
            await self.session.flush()
            return False
        return True

    async def get_subscription_tier(self, user_id: uuid.UUID) -> SubscriptionTier | None:
        """Уровень подписки пользователя."""
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        return user.subscription_tier

    async def require_feature(self, user_id: uuid.UUID, feature: str) -> bool:
        """Проверить фичу подписки пользователя (аналог ChannelService.require_feature)."""
        if not await self.is_subscription_valid(user_id):
            return False
        tier = await self.get_subscription_tier(user_id)
        if tier is None:
            return False
        FEATURES = {
            "free_events": {SubscriptionTier.basic, SubscriptionTier.pro},
            "paid_events": {SubscriptionTier.pro},
            "qr_codes": {SubscriptionTier.pro},
            "invite_tickets": {SubscriptionTier.pro},
            "promo_codes": {SubscriptionTier.pro},
            "dynamic_pricing": {SubscriptionTier.pro},
        }
        return tier in FEATURES.get(feature, set())

    async def is_organizer(self, user_id: uuid.UUID) -> bool:
        """Является ли пользователь организатором (есть активная подписка)."""
        return await self.is_subscription_valid(user_id)

    async def soft_delete(self, user_id: uuid.UUID) -> User | None:
        """Мягкое удаление пользователя — выставляет deleted_at."""
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        user.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return user

    async def delete_account(self, user_id: uuid.UUID) -> User | None:
        """Удаление аккаунта самим пользователем (п.1.1.10 Правил VK).

        Анонимизация + деактивация (билеты сохраняются — нужны для входа):
        - deleted_at = now (аккаунт удалён);
        - name/username = None (стирание персональных данных);
        - подписка пользователя деактивируется (is_subscription_active=False,
          subscription_until=None).
        Идемпотентно: повторный вызов возвращает уже удалённого пользователя.
        """
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        user.deleted_at = datetime.now(timezone.utc)
        user.name = None
        user.username = None
        user.is_subscription_active = False
        user.subscription_until = None
        await self.session.flush()
        logger.info("", extra={
            "event_type": "user.account_deleted",
            "user_id": str(user.id),
            "status": "success",
            "duration_ms": _ms(time.perf_counter()),
        })
        return user

    async def list_all(self) -> list[User]:
        """Список всех пользователей (не удалённых), newest first."""
        stmt = select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


# ─── Channel Service ─────────────────────────────────────────────────────────

class ChannelService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_telegram_id(self, telegram_channel_id: str) -> Channel | None:
        """Look up a channel by its Telegram chat_id or @username."""
        start = time.perf_counter()
        stmt = select(Channel).where(Channel.telegram_channel_id == telegram_channel_id)
        result = await self.session.execute(stmt)
        channel = result.scalar_one_or_none()
        logger.info("", extra={
            "event_type": "channel.get_by_telegram_id",
            "telegram_channel_id": telegram_channel_id,
            "found": channel is not None,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channel

    async def get_by_id(self, channel_id: uuid.UUID) -> Channel | None:
        """Look up a channel by its UUID."""
        start = time.perf_counter()
        channel = await self.session.get(Channel, channel_id)
        logger.info("", extra={
            "event_type": "channel.get_by_id",
            "channel_id": str(channel_id),
            "found": channel is not None,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channel

    async def create(
        self,
        telegram_channel_id: str,
        admin_telegram_user_id: str,
        title: str | None = None,
    ) -> Channel:
        """Create a new channel record."""
        start = time.perf_counter()
        channel = Channel(
            telegram_channel_id=telegram_channel_id,
            title=title,
            admin_telegram_user_id=admin_telegram_user_id,
            is_subscription_active=False,
        )
        self.session.add(channel)
        await self.session.flush()
        logger.info("", extra={
            "event_type": "channel.created",
            "channel_id": str(channel.id),
            "telegram_channel_id": telegram_channel_id,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channel

    async def activate_subscription(
        self,
        channel_id: uuid.UUID,
        duration_days: int = 30,
        tier: SubscriptionTier | None = None,
    ) -> Channel | None:
        """Manually activate subscription for a channel.

        Args:
            channel_id: UUID канала.
            duration_days: Срок подписки в днях.
            tier: Уровень подписки (basic/pro). Если None — остаётся текущий.
        """
        start = time.perf_counter()
        channel = await self.session.get(Channel, channel_id)
        if channel is None:
            logger.warning("", extra={
                "event_type": "subscription.activate_failed",
                "channel_id": str(channel_id),
                "status": "error",
                "error": "Channel not found",
                "duration_ms": _ms(start),
            })
            return None
        channel.is_subscription_active = True
        channel.subscription_until = datetime.now(timezone.utc) + timedelta(days=duration_days)
        if tier is not None:
            channel.subscription_tier = tier
        await self.session.flush()
        logger.info("", extra={
            "event_type": "subscription.activated",
            "channel_id": str(channel.id),
            "telegram_channel_id": channel.telegram_channel_id,
            "duration_days": duration_days,
            "tier": channel.subscription_tier.value,
            "subscription_until": channel.subscription_until.isoformat(),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channel

    async def deactivate_subscription(self, channel_id: uuid.UUID) -> Channel | None:
        """Deactivate subscription."""
        start = time.perf_counter()
        channel = await self.session.get(Channel, channel_id)
        if channel is None:
            logger.warning("", extra={
                "event_type": "subscription.deactivate_failed",
                "channel_id": str(channel_id),
                "status": "error",
                "error": "Channel not found",
                "duration_ms": _ms(start),
            })
            return None
        channel.is_subscription_active = False
        channel.subscription_until = None
        await self.session.flush()
        logger.info("", extra={
            "event_type": "subscription.deactivated",
            "channel_id": str(channel.id),
            "telegram_channel_id": channel.telegram_channel_id,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channel

    async def is_subscription_valid(self, channel_id: uuid.UUID) -> bool:
        """Check if the channel has an active, non-expired subscription."""
        start = time.perf_counter()
        channel = await self.session.get(Channel, channel_id)
        if channel is None:
            logger.info("", extra={
                "event_type": "channel.subscription_check",
                "channel_id": str(channel_id),
                "valid": False,
                "reason": "not_found",
                "status": "success",
                "duration_ms": _ms(start),
            })
            return False
        if not channel.is_subscription_active:
            logger.info("", extra={
                "event_type": "channel.subscription_check",
                "channel_id": str(channel_id),
                "valid": False,
                "reason": "not_active",
                "status": "success",
                "duration_ms": _ms(start),
            })
            return False
        if channel.subscription_until and channel.subscription_until < datetime.now(timezone.utc):
            # Auto-deactivate expired subscriptions
            channel.is_subscription_active = False
            channel.subscription_until = None
            await self.session.flush()
            logger.info("", extra={
                "event_type": "subscription.auto_expired",
                "channel_id": str(channel.id),
                "telegram_channel_id": channel.telegram_channel_id,
                "status": "success",
                "duration_ms": _ms(start),
            })
            return False
        logger.info("", extra={
            "event_type": "channel.subscription_check",
            "channel_id": str(channel_id),
            "valid": True,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return True

    async def get_subscription_tier(self, channel_id: uuid.UUID) -> SubscriptionTier | None:
        """Получить уровень подписки канала."""
        channel = await self.session.get(Channel, channel_id)
        if channel is None:
            return None
        return channel.subscription_tier

    async def require_feature(self, channel_id: uuid.UUID, feature: str) -> bool:
        """Проверить, поддерживает ли канал указанную фичу.

        Args:
            channel_id: UUID канала.
            feature: Название фичи ('free_events', 'paid_events', 'qr_codes', 'promo_codes').

        Returns:
            True если фича доступна, False если нет.
        """
        if not await self.is_subscription_valid(channel_id):
            return False

        tier = await self.get_subscription_tier(channel_id)
        if tier is None:
            return False

        # Матрица фич по уровням подписки
        FEATURES = {
            "free_events": {SubscriptionTier.basic, SubscriptionTier.pro},
            "paid_events": {SubscriptionTier.pro},
            "qr_codes": {SubscriptionTier.pro},
            "invite_tickets": {SubscriptionTier.pro},
            "promo_codes": {SubscriptionTier.pro},
            "dynamic_pricing": {SubscriptionTier.pro},
        }

        return tier in FEATURES.get(feature, set())

    async def get_active_unassigned_channel(self) -> Channel | None:
        """Get a channel with active subscription but no admin assigned."""
        start = time.perf_counter()
        stmt = (
            select(Channel)
            .where(
                and_(
                    Channel.admin_telegram_user_id == "",
                    Channel.is_subscription_active == True,
                )
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        channel = result.scalar_one_or_none()
        logger.info("", extra={
            "event_type": "channel.get_active_unassigned",
            "found": channel is not None,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channel

    async def get_channel_ids_by_admin(self, telegram_user_id: str) -> list[uuid.UUID]:
        """Получить ID всех каналов, где пользователь — админ (из channel_admins)."""
        start = time.perf_counter()
        stmt = select(ChannelAdmin.channel_id).where(ChannelAdmin.telegram_user_id == telegram_user_id)
        result = await self.session.execute(stmt)
        ids = list(result.scalars().all())
        logger.info("", extra={
            "event_type": "channel.get_ids_by_admin",
            "admin_id": telegram_user_id,
            "count": len(ids),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return ids

    async def get_channels_by_admin(self, telegram_user_id: str) -> list[Channel]:
        """Получить все каналы, где пользователь — админ (через channel_admins)."""
        start = time.perf_counter()
        channel_ids = await self.get_channel_ids_by_admin(telegram_user_id)
        if not channel_ids:
            logger.info("", extra={
                "event_type": "channel.get_channels_by_admin",
                "admin_id": telegram_user_id,
                "count": 0,
                "status": "success",
                "duration_ms": _ms(start),
            })
            return []
        stmt = select(Channel).where(Channel.id.in_(channel_ids))
        result = await self.session.execute(stmt)
        channels = list(result.scalars().all())
        logger.info("", extra={
            "event_type": "channel.get_channels_by_admin",
            "admin_id": telegram_user_id,
            "count": len(channels),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channels

    async def change_admin(self, channel_telegram_id: str, new_admin_id: str) -> tuple[Channel, list[str]]:
        """Сменить администратора канала.

        Обновляет channel_admins (M2M) и legacy-поле admin_telegram_user_id.
        Использует ChannelAdminService внутренне для синхронизации.

        Args:
            channel_telegram_id: @username или ID канала.
            new_admin_id: Telegram ID нового администратора.

        Returns:
            tuple[Channel, list[str]]: (канал, список старых admin_id).

        Raises:
            ValueError: если канал с таким channel_telegram_id не найден.
        """
        start = time.perf_counter()
        channel = await self.get_by_telegram_id(channel_telegram_id)
        if not channel:
            logger.warning("", extra={
                "event_type": "channel.change_admin_failed",
                "telegram_channel_id": channel_telegram_id,
                "new_admin_id": new_admin_id,
                "status": "error",
                "error": "Channel not found",
                "duration_ms": _ms(start),
            })
            raise ValueError(f"Канал {channel_telegram_id} не найден")

        admin_svc = ChannelAdminService(self.session)
        old_admin_ids = await admin_svc.get_admin_ids(channel.id)
        await admin_svc.sync_admins(channel.id, [new_admin_id])
        channel.admin_telegram_user_id = new_admin_id
        await self.session.flush()

        logger.info("", extra={
            "event_type": "channel.admin_changed",
            "channel_id": str(channel.id),
            "telegram_channel_id": channel.telegram_channel_id,
            "old_admin_ids": old_admin_ids,
            "new_admin_id": new_admin_id,
            "status": "success",
            "duration_ms": _ms(start),
        })

        return channel, old_admin_ids

    async def list_all(self) -> list[Channel]:
        """Get all channels, newest first (super-admin view)."""
        start = time.perf_counter()
        stmt = select(Channel).order_by(Channel.created_at.desc())
        result = await self.session.execute(stmt)
        channels = list(result.scalars().all())
        logger.info("", extra={
            "event_type": "channel.list_all",
            "count": len(channels),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channels

    async def get_channel_summary(self, channel_id: uuid.UUID) -> dict | None:
        """Aggregated channel info for the admin panel.

        Returns dict with channel fields + admins, events_count,
        upcoming_count, tickets_sold; None if channel missing.
        """
        channel = await self.session.get(Channel, channel_id)
        if channel is None:
            return None

        now = datetime.now(timezone.utc)

        events_result = await self.session.execute(
            select(func.count()).select_from(Event).where(Event.channel_id == channel.id)
        )
        events_count = events_result.scalar() or 0

        upcoming_result = await self.session.execute(
            select(func.count()).select_from(Event).where(
                and_(Event.channel_id == channel.id, Event.date >= now)
            )
        )
        upcoming = upcoming_result.scalar() or 0

        tickets_result = await self.session.execute(
            select(func.count()).select_from(Ticket)
            .join(Event, Ticket.event_id == Event.id)
            .where(Event.channel_id == channel.id, Ticket.status == TicketStatus.active)
        )
        tickets_sold = tickets_result.scalar() or 0

        admin_svc = ChannelAdminService(self.session)
        admins = await admin_svc.get_admin_ids(channel.id)

        return {
            "id": str(channel.id),
            "telegram_channel_id": channel.telegram_channel_id,
            "title": channel.title,
            "admin_telegram_user_id": channel.admin_telegram_user_id,
            "is_subscription_active": channel.is_subscription_active,
            "subscription_until": channel.subscription_until.isoformat() if channel.subscription_until else None,
            "subscription_tier": channel.subscription_tier.value,
            "admins": admins,
            "events_count": events_count,
            "upcoming_count": upcoming,
            "tickets_sold": tickets_sold,
        }

    # ─── Управление подпиской (смена типа + срока) ────────────────

    @staticmethod
    def _add_period(now: datetime, period: int, unit: PeriodUnit) -> datetime:
        """Прибавить период (дни/месяцы/годы) к моменту времени.

        Календарно-корректно для месяцев/лет (relativedelta): 31 янв + 1 мес = 28 фев.
        """
        if unit == PeriodUnit.days:
            return now + timedelta(days=period)
        if unit == PeriodUnit.months:
            return now + relativedelta(months=period)
        return now + relativedelta(years=period)

    async def change_subscription(
        self,
        channel_id: uuid.UUID,
        tier: SubscriptionTier,
        period: int,
        period_unit: PeriodUnit,
    ) -> Channel | None:
        """Сменить подписку канала: тип + срок (от текущей даты).

        Args:
            channel_id: UUID канала.
            tier: Новый уровень (basic/pro).
            period: Количество периодов.
            period_unit: Единица (days/months/years).

        Returns:
            Channel | None: обновлённый канал или None, если канал не найден.
        """
        start = time.perf_counter()
        channel = await self.session.get(Channel, channel_id)
        if channel is None:
            logger.warning("", extra={
                "event_type": "subscription.change_failed",
                "channel_id": str(channel_id),
                "status": "error",
                "error": "Channel not found",
                "duration_ms": _ms(start),
            })
            return None

        channel.subscription_tier = tier
        channel.is_subscription_active = True
        channel.subscription_until = self._add_period(
            datetime.now(timezone.utc), period, period_unit,
        )
        await self.session.flush()

        logger.info("", extra={
            "event_type": "subscription.changed",
            "channel_id": str(channel.id),
            "telegram_channel_id": channel.telegram_channel_id,
            "tier": channel.subscription_tier.value,
            "period": period,
            "period_unit": period_unit.value,
            "subscription_until": channel.subscription_until.isoformat(),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channel

    async def change_tier(self, channel_id: uuid.UUID, tier: SubscriptionTier) -> Channel | None:
        """Сменить ТОЛЬКО тип подписки (срок не трогаем).

        Returns:
            Channel | None: обновлённый канал или None, если канал не найден.
        """
        start = time.perf_counter()
        channel = await self.session.get(Channel, channel_id)
        if channel is None:
            logger.warning("", extra={
                "event_type": "subscription.tier_change_failed",
                "channel_id": str(channel_id),
                "status": "error",
                "error": "Channel not found",
                "duration_ms": _ms(start),
            })
            return None

        channel.subscription_tier = tier
        await self.session.flush()

        logger.info("", extra={
            "event_type": "subscription.tier_changed",
            "channel_id": str(channel.id),
            "telegram_channel_id": channel.telegram_channel_id,
            "tier": channel.subscription_tier.value,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return channel


# ─── Channel Admin Service ──────────────────────────────────────────────────

class ChannelAdminService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_admin_ids(self, channel_id: uuid.UUID) -> list[str]:
        """Получить список Telegram ID админов канала."""
        start = time.perf_counter()
        stmt = select(ChannelAdmin.telegram_user_id).where(ChannelAdmin.channel_id == channel_id)
        result = await self.session.execute(stmt)
        ids = list(result.scalars().all())
        logger.info("", extra={
            "event_type": "channel_admin.get_ids",
            "channel_id": str(channel_id),
            "count": len(ids),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return ids

    async def sync_admins(self, channel_id: uuid.UUID, admin_ids: list[str]):
        """Синхронизировать список админов канала с Telegram.

        Удаляет отсутствующих, добавляет новых, существующих не трогает.
        """
        start = time.perf_counter()
        existing = await self.get_admin_ids(channel_id)
        existing_set = set(existing)
        new_set = set(admin_ids)

        # Добавить новых
        added = []
        for uid in new_set - existing_set:
            self.session.add(ChannelAdmin(channel_id=channel_id, telegram_user_id=uid))
            added.append(uid)

        # Удалить отсутствующих
        removed = []
        to_remove = existing_set - new_set
        if to_remove:
            removed = list(to_remove)
            stmt = delete(ChannelAdmin).where(
                ChannelAdmin.channel_id == channel_id,
                ChannelAdmin.telegram_user_id.in_(to_remove),
            )
            await self.session.execute(stmt)

        await self.session.flush()

        if added or removed:
            logger.info("", extra={
                "event_type": "channel_admins.synced",
                "channel_id": str(channel_id),
                "added": added,
                "removed": removed,
                "status": "success",
                "duration_ms": _ms(start),
            })

    async def user_is_admin(self, channel_id: uuid.UUID, telegram_user_id: str) -> bool:
        """Проверить, является ли пользователь админом канала."""
        start = time.perf_counter()
        stmt = select(ChannelAdmin).where(
            ChannelAdmin.channel_id == channel_id,
            ChannelAdmin.telegram_user_id == telegram_user_id,
        ).limit(1)
        result = await self.session.execute(stmt)
        is_admin = result.scalar_one_or_none() is not None
        logger.info("", extra={
            "event_type": "channel_admin.user_is_admin",
            "channel_id": str(channel_id),
            "user_id": telegram_user_id,
            "is_admin": is_admin,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return is_admin

    async def remove_admin(self, channel_id: uuid.UUID, telegram_user_id: str):
        """Удалить пользователя из админов канала."""
        start = time.perf_counter()
        stmt = delete(ChannelAdmin).where(
            ChannelAdmin.channel_id == channel_id,
            ChannelAdmin.telegram_user_id == telegram_user_id,
        )
        await self.session.execute(stmt)
        await self.session.flush()
        logger.info("", extra={
            "event_type": "channel_admin.removed",
            "channel_id": str(channel_id),
            "user_id": telegram_user_id,
            "status": "success",
            "duration_ms": _ms(start),
        })


# ─── VKGroup Service ─────────────────────────────────────────────────────────

class VKGroupService:
    """VK-группы как цели публикации (self-service организатора)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_group_id(self, group_id: str) -> VKGroup | None:
        stmt = select(VKGroup).where(VKGroup.group_id == group_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def register_vk_group(
        self,
        owner_user_id: uuid.UUID,
        group_id: str,
        title: str | None = None,
        community_token: str | None = None,
    ) -> VKGroup:
        """Зарегистрировать VK-группу для организатора (community token шифруется).

        ValueError: group_id уже зарегистрирован другим организатором (анти-захват).
        """
        existing = await self.get_by_group_id(group_id)
        if existing is not None:
            if existing.owner_user_id == owner_user_id:
                # Идемпотентно: группа уже у этого организатора — обновить token/title
                if community_token:
                    from app.core.crypto import encrypt_token
                    existing.community_token = encrypt_token(community_token)
                if title:
                    existing.title = title
                await self.session.flush()
                return existing
            raise ValueError("Группа уже зарегистрирована другим организатором")

        from app.core.crypto import encrypt_token
        group = VKGroup(
            owner_user_id=owner_user_id,
            group_id=group_id,
            title=title,
            community_token=encrypt_token(community_token) if community_token else None,
        )
        self.session.add(group)
        await self.session.flush()
        logger.info("", extra={
            "event_type": "vk_group.registered",
            "group_id": group_id,
            "owner_user_id": str(owner_user_id),
            "status": "success",
        })
        return group

    async def list_vk_groups(self, owner_user_id: uuid.UUID) -> list[VKGroup]:
        result = await self.session.execute(
            select(VKGroup).where(VKGroup.owner_user_id == owner_user_id)
        )
        return list(result.scalars().all())

    async def remove_vk_group(self, owner_user_id: uuid.UUID, group_id: str) -> bool:
        stmt = select(VKGroup).where(
            and_(VKGroup.owner_user_id == owner_user_id, VKGroup.group_id == group_id)
        )
        result = await self.session.execute(stmt)
        group = result.scalar_one_or_none()
        if group is None:
            return False
        await self.session.delete(group)
        await self.session.flush()
        return True


# ─── Event Service ───────────────────────────────────────────────────────────

class EventService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_upcoming(self, channel_id: uuid.UUID | None = None) -> list[Event]:
        """Get all active, published events that haven't passed yet, optionally filtered by channel."""
        start = time.perf_counter()
        now = datetime.now(timezone.utc)
        stmt = (
            select(Event)
            .where(
                and_(
                    Event.is_active == True,
                    Event.is_published == True,
                    Event.date >= now,
                    Event.deleted_at.is_(None),
                )
            )
            .order_by(Event.date.asc())
        )
        if channel_id is not None:
            stmt = stmt.where(Event.channel_id == channel_id)
        result = await self.session.execute(stmt)
        events = list(result.scalars().all())
        logger.info("", extra={
            "event_type": "event.list_upcoming",
            "channel_id": str(channel_id) if channel_id else "all",
            "count": len(events),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return events

    # ═══════════════════════════════════════════════════════════════
    # B: лимит «1 опубликованное будущее» для бесплатного организатора
    # ═══════════════════════════════════════════════════════════════

    async def count_published_future(
        self,
        owner_user_id: uuid.UUID | None = None,
        channel_id: uuid.UUID | None = None,
    ) -> int:
        """Сколько опубликованных будущих мероприятий у владельца (owner ИЛИ канал).

        Считаются: is_published=True AND date >= now AND deleted_at IS NULL.
        Прошедшие и черновики не считаются.
        """
        now = datetime.now(timezone.utc)
        stmt = select(func.count(Event.id)).where(
            and_(
                Event.is_published == True,
                Event.date >= now,
                Event.deleted_at.is_(None),
            )
        )
        if owner_user_id is not None:
            stmt = stmt.where(Event.owner_user_id == owner_user_id)
        if channel_id is not None:
            stmt = stmt.where(Event.channel_id == channel_id)
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def _is_free_owner(self, owner_user_id: uuid.UUID) -> bool:
        """Free-владелец: без активной подписки ИЛИ basic."""
        user_svc = UserService(self.session)
        if not await user_svc.is_subscription_valid(owner_user_id):
            return True
        tier = await user_svc.get_subscription_tier(owner_user_id)
        return tier == SubscriptionTier.basic

    async def _is_free_channel(self, channel_id: uuid.UUID) -> bool:
        """Free-канал: без активной подписки ИЛИ basic."""
        channel_svc = ChannelService(self.session)
        if not await channel_svc.is_subscription_valid(channel_id):
            return True
        tier = await channel_svc.get_subscription_tier(channel_id)
        return tier == SubscriptionTier.basic

    async def ensure_free_slot(self, event: Event) -> None:
        """Проверить лимит «1 опубликованное будущее» для бесплатного организатора.

        Вызывается перед публикацией (is_published False→True). Если событие уже
        опубликовано (re-publish) — не блокирует. Pro-владельцы не ограничены.
        Черновики и прошедшие не занимают слот.

        Raises:
            ValueError: бесплатный организатор уже имеет 1 опубликованное будущее.
        """
        if event.is_published:
            return  # повторная публикация уже опубликованного — не блокируем
        if event.owner_user_id is not None:
            if not await self._is_free_owner(event.owner_user_id):
                return  # pro-организатор не ограничен
            count = await self.count_published_future(owner_user_id=event.owner_user_id)
        elif event.channel_id is not None:
            if not await self._is_free_channel(event.channel_id):
                return
            count = await self.count_published_future(channel_id=event.channel_id)
        else:
            return  # событие без владельца — не гейтим
        if count >= 1:
            raise ValueError(
                "Бесплатный организатор может опубликовать только одно мероприятие одновременно"
            )

    async def get_by_id(self, event_id: uuid.UUID, channel_id: uuid.UUID | None = None) -> Event | None:
        """Get event by ID, optionally scoped to a channel."""
        start = time.perf_counter()
        stmt = select(Event).where(Event.id == event_id)
        if channel_id is not None:
            stmt = stmt.where(Event.channel_id == channel_id)
        result = await self.session.execute(stmt)
        event = result.scalar_one_or_none()
        logger.info("", extra={
            "event_type": "event.get_by_id",
            "event_id": str(event_id),
            "found": event is not None,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return event

    async def create(self, title: str, description: Optional[str], date: datetime,
                     location: Optional[str], price: float,
                     total_tickets: int, channel_id: uuid.UUID | None,
                     invites_quota: int = 0, owner_user_id: uuid.UUID | None = None,
                     age_restriction: str = "0+") -> Event:
        start = time.perf_counter()

        # Мероприятие принадлежит каналу ИЛИ организатору-пользователю (не оба, не никого)
        if (channel_id is None) == (owner_user_id is None):
            raise ValueError("Мероприятие должно принадлежать каналу или организатору")

        # Проверка: если мероприятие платное, нужна Pro-подписка (канала ИЛИ пользователя)
        if price > 0:
            if channel_id is not None:
                channel_svc = ChannelService(self.session)
                has_feature = await channel_svc.require_feature(channel_id, "paid_events")
            else:
                user_svc = UserService(self.session)
                has_feature = await user_svc.require_feature(owner_user_id, "paid_events")
            if not has_feature:
                raise ValueError(
                    "Ваш тариф поддерживает только бесплатные мероприятия. "
                    "Для платных мероприятий необходима подписка Pro."
                )

        is_free = (price == 0)
        event = Event(
            title=title,
            description=description,
            date=date,
            location=location,
            price=price,
            total_tickets=total_tickets,
            available_tickets=total_tickets,
            is_active=True,
            is_free=is_free,
            invites_quota=invites_quota,
            channel_id=channel_id,
            owner_user_id=owner_user_id,
            age_restriction=age_restriction,
        )
        self.session.add(event)
        await self.session.flush()
        logger.info("", extra={
            "event_type": "event.created",
            "event_id": str(event.id),
            "event_title": title,
            "channel_id": str(channel_id) if channel_id else None,
            "owner_user_id": str(owner_user_id) if owner_user_id else None,
            "price": price,
            "total_tickets": total_tickets,
            "is_free": is_free,
            "age_restriction": age_restriction,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return event

    # ─── Admin methods ───────────────────────────────────────────────────

    async def list_all(
        self,
        channel_id: uuid.UUID | None = None,
        owner_user_id: uuid.UUID | None = None,
    ) -> list[Event]:
        """Get ALL events that are not deleted, newest first.
        Optionally filtered by channel or owner."""
        start = time.perf_counter()
        stmt = select(Event).where(Event.deleted_at.is_(None)).order_by(Event.date.desc())
        if channel_id is not None:
            stmt = stmt.where(Event.channel_id == channel_id)
        if owner_user_id is not None:
            stmt = stmt.where(Event.owner_user_id == owner_user_id)
        result = await self.session.execute(stmt)
        events = list(result.scalars().all())
        logger.info("", extra={
            "event_type": "event.list_all",
            "channel_id": str(channel_id) if channel_id else "all",
            "owner_user_id": str(owner_user_id) if owner_user_id else "all",
            "count": len(events),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return events

    async def update(self, event_id: uuid.UUID, **data) -> Event | None:
        """Partially update an event. Returns updated event or None."""
        start = time.perf_counter()
        event = await self.session.get(Event, event_id)
        if event is None:
            logger.warning("", extra={
                "event_type": "event.update_failed",
                "event_id": str(event_id),
                "status": "error",
                "error": "Event not found",
                "duration_ms": _ms(start),
            })
            return None

        # Снимок старых значений ДО setattr (нужен для корректировки available)
        before = {k: getattr(event, k) for k in data if hasattr(event, k)}

        changed = {}
        for key, value in data.items():
            if hasattr(event, key):
                old = getattr(event, key)
                if old != value:
                    changed[key] = {"from": str(old), "to": str(value)}
                setattr(event, key, value)

        # C/баг #075: платное мероприятие требует pro (подписка ИЛИ per-event премиум).
        if "price" in data and data["price"] > 0:
            has_feature = await self.has_event_pro_feature(event_id, "paid_events")
            if not has_feature:
                raise ValueError(
                    "Ваш тариф поддерживает только бесплатные мероприятия. "
                    "Для платных мероприятий нужна подписка Pro или премиум на событие."
                )
        # Инвариант динамики цен: при смене цены на 0 диапазоны удаляются.
        if "price" in data and data["price"] <= 0:
            await self.session.execute(
                delete(EventPriceRange).where(EventPriceRange.event_id == event_id)
            )

        # C: при переносе даты обновляем expires_at премиума события
        if "date" in data:
            upgrade = await self._get_upgrade(event_id)
            if upgrade is not None:
                upgrade.expires_at = event.date

        # C: дата публикации (начало покрытия динамических цен) — ставится один раз
        if data.get("is_published") and event.published_at is None:
            event.published_at = datetime.now(timezone.utc)

        # Sync available_tickets when total_tickets changes
        if "total_tickets" in data:
            old_total = before["total_tickets"]
            new_total = data["total_tickets"]
            diff = new_total - old_total
            event.available_tickets = max(0, event.available_tickets + diff)

        # Изменение квоты пригласительных выделяет/возвращает места из непроданных:
        # увеличение quota → резервируем места (available -= diff),
        # уменьшение → возвращаем в непроданные (available += diff).
        if "invites_quota" in data:
            old_quota = before["invites_quota"]
            new_quota = data["invites_quota"]
            diff = new_quota - old_quota
            event.available_tickets = max(0, event.available_tickets - diff)

        await self.session.flush()
        logger.info("", extra={
            "event_type": "event.updated",
            "event_id": str(event.id),
            "event_title": event.title,
            "changed": changed,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return event

    async def set_active(self, event_id: uuid.UUID, is_active: bool) -> Event | None:
        """Enable or disable an event. Returns updated event or None."""
        start = time.perf_counter()
        event = await self.session.get(Event, event_id)
        if event is None:
            logger.warning("", extra={
                "event_type": "event.toggle_failed",
                "event_id": str(event_id),
                "status": "error",
                "error": "Event not found",
                "duration_ms": _ms(start),
            })
            return None
        event.is_active = is_active
        await self.session.flush()
        logger.info("", extra={
            "event_type": "event.toggled",
            "event_id": str(event.id),
            "event_title": event.title,
            "is_active": is_active,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return event

    async def soft_delete(self, event_id: uuid.UUID) -> Event | None:
        """Soft delete an event — hides it from all lists.
        Sets is_active=False and deleted_at=now()."""
        start = time.perf_counter()
        event = await self.session.get(Event, event_id)
        if event is None:
            logger.warning("", extra={
                "event_type": "event.delete_failed",
                "event_id": str(event_id),
                "status": "error",
                "error": "Event not found",
                "duration_ms": _ms(start),
            })
            return None
        event.is_active = False
        event.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        logger.info("", extra={
            "event_type": "event.deleted",
            "event_id": str(event.id),
            "event_title": event.title,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return event

    # ─── Соработники (event_managers) ────────────────────────────

    async def add_manager(self, event_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Добавить соработника мероприятия (идемпотентно)."""
        event = await self.session.get(Event, event_id)
        if event is None:
            raise ValueError("Мероприятие не найдено")
        user = await self.session.get(User, user_id)
        if user is None:
            raise ValueError("Пользователь не найден")

        existing = await self.is_manager(event_id, user_id)
        if existing:
            return False
        self.session.add(EventManager(event_id=event_id, user_id=user_id))
        await self.session.flush()
        logger.info("", extra={
            "event_type": "event.manager_added",
            "event_id": str(event_id),
            "user_id": str(user_id),
            "status": "success",
        })
        return True

    async def remove_manager(self, event_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Убрать соработника мероприятия."""
        result = await self.session.execute(
            select(EventManager).where(
                and_(EventManager.event_id == event_id, EventManager.user_id == user_id)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        logger.info("", extra={
            "event_type": "event.manager_removed",
            "event_id": str(event_id),
            "user_id": str(user_id),
            "status": "success",
        })
        return True

    async def is_manager(self, event_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            select(EventManager.id).where(
                and_(EventManager.event_id == event_id, EventManager.user_id == user_id)
            )
        )
        return result.scalar_one_or_none() is not None

    async def list_managers(self, event_id: uuid.UUID) -> list[User]:
        """Соработники мероприятия (канонические пользователи)."""
        result = await self.session.execute(
            select(User)
            .join(EventManager, EventManager.user_id == User.id)
            .where(EventManager.event_id == event_id)
        )
        return list(result.scalars().all())

    async def get_manager_event_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """ID мероприятий, где пользователь — соработник."""
        result = await self.session.execute(
            select(EventManager.event_id).where(EventManager.user_id == user_id)
        )
        return list(result.scalars().all())

    # ─── Публикации (event_publications, placements) ─────────────

    async def add_publication(
        self,
        event_id: uuid.UUID,
        platform: PlatformType,
        target_type: str,
        target_id: str,
        created_by: uuid.UUID | None = None,
        status: str = "posted",
        last_error: str | None = None,
    ) -> EventPublication:
        """Зафиксировать публикацию события в цель (идемпотентно по уникальному ключу)."""
        existing = await self.get_publication(event_id, platform, target_type, target_id)
        if existing is not None:
            existing.status = status
            existing.last_error = last_error
            await self.session.flush()
            return existing

        pub = EventPublication(
            event_id=event_id,
            platform=platform,
            target_type=target_type,
            target_id=target_id,
            created_by=created_by,
            status=status,
            last_error=last_error,
        )
        self.session.add(pub)
        await self.session.flush()
        return pub

    async def get_publication(
        self,
        event_id: uuid.UUID,
        platform: PlatformType,
        target_type: str,
        target_id: str,
    ) -> EventPublication | None:
        result = await self.session.execute(
            select(EventPublication).where(
                and_(
                    EventPublication.event_id == event_id,
                    EventPublication.platform == platform,
                    EventPublication.target_type == target_type,
                    EventPublication.target_id == target_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def list_publications(self, event_id: uuid.UUID) -> list[EventPublication]:
        result = await self.session.execute(
            select(EventPublication)
            .where(EventPublication.event_id == event_id)
            .order_by(EventPublication.created_at.desc())
        )
        return list(result.scalars().all())

    async def remove_publication(self, publication_id: uuid.UUID) -> bool:
        pub = await self.session.get(EventPublication, publication_id)
        if pub is None:
            return False
        await self.session.delete(pub)
        await self.session.flush()
        return True

    async def get_event_stats(self, event_id: uuid.UUID) -> dict:
        """Get sales stats for an event."""
        start = time.perf_counter()
        event = await self.session.get(Event, event_id)
        if event is None:
            raise ValueError("Мероприятие не найдено")

        # Sold = активные ОПЛАЧЕННЫЕ билеты (не пригласительные)
        sold_stmt = select(func.count(Ticket.id)).where(
            and_(
                Ticket.event_id == event_id,
                Ticket.status == TicketStatus.active,
                Ticket.is_invite == False,
            )
        )
        sold_result = await self.session.execute(sold_stmt)
        sold = sold_result.scalar() or 0

        # Refunded = возвращённые билеты
        refunded_stmt = select(func.count(Ticket.id)).where(
            and_(Ticket.event_id == event_id, Ticket.status == TicketStatus.refunded)
        )
        refunded_result = await self.session.execute(refunded_stmt)
        refunded = refunded_result.scalar() or 0

        # Пригласительные: выдано (все) и использовано (checked_in)
        invites_stmt = select(func.count(Ticket.id)).where(
            and_(Ticket.event_id == event_id, Ticket.is_invite == True)
        )
        invites_issued = (await self.session.execute(invites_stmt)).scalar() or 0

        invites_used_stmt = select(func.count(Ticket.id)).where(
            and_(
                Ticket.event_id == event_id,
                Ticket.is_invite == True,
                Ticket.status == TicketStatus.checked_in,
            )
        )
        invites_used = (await self.session.execute(invites_used_stmt)).scalar() or 0

        sold_pct = round((sold / event.total_tickets * 100), 1) if event.total_tickets > 0 else 0
        # Выручка — по фактическим Payment.amount (учитывает скидки и динамические цены).
        revenue_stmt = (
            select(func.coalesce(func.sum(Payment.amount), 0))
            .join(Ticket, Payment.ticket_id == Ticket.id)
            .where(
                and_(
                    Ticket.event_id == event_id,
                    Ticket.status == TicketStatus.active,
                    Ticket.is_invite == False,
                )
            )
        )
        revenue = float((await self.session.execute(revenue_stmt)).scalar() or 0)

        logger.info("", extra={
            "event_type": "event.get_stats",
            "event_id": str(event_id),
            "total_tickets": event.total_tickets,
            "sold": sold,
            "refunded": refunded,
            "invites_issued": invites_issued,
            "invites_used": invites_used,
            "revenue": revenue,
            "status": "success",
            "duration_ms": _ms(start),
        })

        return {
            "total_tickets": event.total_tickets,
            "available": event.available_tickets,
            "sold": sold,
            "refunded": refunded,
            "sold_pct": sold_pct,
            "revenue": revenue,
            "invites_quota": event.invites_quota,
            "invites_issued": invites_issued,
            "invites_used": invites_used,
        }

    # ═══════════════════════════════════════════════════════════════
    # B2: динамические цены по дате (early bird, pro-фича)
    # ═══════════════════════════════════════════════════════════════

    async def effective_price_at(self, event: Event, dt: datetime) -> Decimal:
        """Актуальная цена события на момент dt (динамические цены по дате).

        Диапазоны [starts, ends); последний (ends == event.date) — включительно.
        Если диапазонов нет или событие бесплатное — базовая цена event.price.
        """
        if event.price <= 0:
            return Decimal(str(event.price))
        stmt = select(EventPriceRange).where(
            EventPriceRange.event_id == event.id
        ).order_by(EventPriceRange.starts_at)
        ranges = list((await self.session.execute(stmt)).scalars().all())
        for i, r in enumerate(ranges):
            if r.starts_at <= dt < r.ends_at:
                return Decimal(str(r.price))
            if i == len(ranges) - 1 and r.ends_at == event.date and r.starts_at <= dt <= r.ends_at:
                return Decimal(str(r.price))
        return Decimal(str(event.price))

    async def get_price_ranges(self, event_id: uuid.UUID) -> list[dict]:
        """Список ценовых диапазонов мероприятия (sorted by starts_at)."""
        stmt = select(EventPriceRange).where(
            EventPriceRange.event_id == event_id
        ).order_by(EventPriceRange.starts_at)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(r.id),
                "starts_at": r.starts_at.isoformat(),
                "ends_at": r.ends_at.isoformat(),
                "price": float(r.price),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    async def replace_price_ranges(self, event_id: uuid.UUID, ranges: list[dict]) -> list[dict]:
        """Заменить весь набор ценовых диапазонов (PUT-стиль).

        Диапазоны обязаны сплошь покрывать [event.published_at, event.date]
        без дыр и пересечений. Только для платных событий (price > 0).
        """
        event = await self.session.get(Event, event_id)
        if event is None:
            raise ValueError("Мероприятие не найдено")
        if event.price <= 0:
            raise ValueError("Динамические цены доступны только для платных мероприятий")

        # Пустой список = выключить динамику (просто очистить, без валидации покрытия)
        if not ranges:
            await self.session.execute(
                delete(EventPriceRange).where(EventPriceRange.event_id == event_id)
            )
            await self.session.flush()
            return []

        start_bound = event.published_at or event.created_at
        parsed = []
        for r in ranges:
            start = r["starts_at"]
            end = r["ends_at"]
            price = r["price"]
            if not start.tzinfo or not end.tzinfo:
                raise ValueError("Даты диапазонов должны быть с таймзоной")
            if end <= start:
                raise ValueError("Дата окончания должна быть позже даты начала")
            if price < 0:
                raise ValueError("Цена не может быть отрицательной")
            if start < start_bound:
                raise ValueError("Диапазон не может начинаться раньше публикации мероприятия")
            if end > event.date:
                raise ValueError("Диапазон не может заканчиваться позже даты мероприятия")
            parsed.append({"starts_at": start, "ends_at": end, "price": price})

        parsed.sort(key=lambda x: x["starts_at"])
        cursor = start_bound
        for p in parsed:
            if p["starts_at"] > cursor:
                raise ValueError("Ценовые диапазоны не покрывают весь период (есть «дыра»)")
            if p["starts_at"] < cursor:
                raise ValueError("Ценовые диапазоны пересекаются")
            cursor = p["ends_at"]
        if cursor < event.date:
            raise ValueError("Ценовые диапазоны не покрывают период до даты мероприятия")

        await self.session.execute(
            delete(EventPriceRange).where(EventPriceRange.event_id == event_id)
        )
        for p in parsed:
            self.session.add(EventPriceRange(event_id=event_id, **p))
        await self.session.flush()
        return await self.get_price_ranges(event_id)

    async def price_ranges_map(self, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[EventPriceRange]]:
        """Все диапазоны для набора событий одним SELECT (без N+1 в GET /events)."""
        if not event_ids:
            return {}
        stmt = (
            select(EventPriceRange)
            .where(EventPriceRange.event_id.in_(event_ids))
            .order_by(EventPriceRange.starts_at)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        out: dict[uuid.UUID, list[EventPriceRange]] = {}
        for r in rows:
            out.setdefault(r.event_id, []).append(r)
        return out

    @staticmethod
    def resolve_price(ranges: list[EventPriceRange] | None, event, dt: datetime) -> float:
        """Актуальная цена для buyer-ответа (граничная логика effective_price_at)."""
        if not ranges:
            return float(event.price)
        for i, r in enumerate(ranges):
            if r.starts_at <= dt < r.ends_at:
                return float(r.price)
            if i == len(ranges) - 1 and r.ends_at == event.date and r.starts_at <= dt <= r.ends_at:
                return float(r.price)
        return float(event.price)

    # ═══════════════════════════════════════════════════════════════
    # C: per-event премиум (единовременная оплата за мероприятие)
    # ═══════════════════════════════════════════════════════════════

    async def _get_upgrade(self, event_id: uuid.UUID) -> EventUpgrade | None:
        """Запись премиума события (или None)."""
        stmt = select(EventUpgrade).where(EventUpgrade.event_id == event_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_event_is_premium(self, event_id: uuid.UUID) -> bool:
        """Премиум ли событие (активная запись EventUpgrade, не истекла)."""
        upgrade = await self._get_upgrade(event_id)
        if upgrade is None or upgrade.status != PaymentStatus.completed:
            return False
        if upgrade.expires_at and upgrade.expires_at < datetime.now(timezone.utc):
            return False
        return True

    async def purchase_event_premium(
        self,
        event_id: uuid.UUID,
        user_id: uuid.UUID,
        amount: float = 0,
        provider: str | None = None,
    ) -> EventUpgrade:
        """Купить премиум на одно мероприятие (владелец события).

        Stub-оплата (как подписка): сразу completed. Модель под будущий
        провайдер (Telegram Stars / YooKassa). expires_at = event.date.

        Raises:
            ValueError: событие не найдено, пользователь не владелец.
        """
        event = await self.session.get(Event, event_id)
        if event is None:
            raise ValueError("Мероприятие не найдено")
        if event.owner_user_id is None or event.owner_user_id != user_id:
            raise ValueError("Премиум на событие покупает только его владелец")

        upgrade = await self._get_upgrade(event_id)
        if upgrade is None:
            upgrade = EventUpgrade(event_id=event_id)
            self.session.add(upgrade)
        upgrade.status = PaymentStatus.completed
        upgrade.amount = amount
        upgrade.provider = provider
        upgrade.completed_at = datetime.now(timezone.utc)
        upgrade.expires_at = event.date
        await self.session.flush()

        logger.info("", extra={
            "event_type": "event.premium_purchased",
            "event_id": str(event_id),
            "user_id": str(user_id),
            "status": "success",
            "provider": provider,
        })
        return upgrade

    async def has_event_pro_feature(self, event_id: uuid.UUID, feature: str) -> bool:
        """Есть ли у события pro-фича (paid_events/qr_codes/invite_tickets/promo_codes).

        Сначала per-event премиум; иначе fallback на подписку канала/владельца.
        """
        if await self.get_event_is_premium(event_id):
            return feature in ("paid_events", "qr_codes", "invite_tickets", "promo_codes", "dynamic_pricing")

        event = await self.session.get(Event, event_id)
        if event is None:
            return False
        if event.channel_id is not None:
            channel_svc = ChannelService(self.session)
            return await channel_svc.require_feature(event.channel_id, feature)
        if event.owner_user_id is not None:
            user_svc = UserService(self.session)
            return await user_svc.require_feature(event.owner_user_id, feature)
        return False

    async def get_event_premium_map(self, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, bool]:
        """Батч: какие события премиум (для списка)."""
        if not event_ids:
            return {}
        now = datetime.now(timezone.utc)
        stmt = select(EventUpgrade).where(
            and_(
                EventUpgrade.event_id.in_(event_ids),
                EventUpgrade.status == PaymentStatus.completed,
                (EventUpgrade.expires_at.is_(None) | (EventUpgrade.expires_at >= now)),
            )
        )
        result = await self.session.execute(stmt)
        return {u.event_id: True for u in result.scalars().all()}


# ─── Ticket Service ──────────────────────────────────────────────────────────

class TicketService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    async def generate_validation_code() -> str:
        """Сгенерировать уникальный короткий код для входа.

        Формат: XXXX-XXXX (hex, 8 символов + дефис).
        Пример: AB3X-K7M9
        """
        raw = secrets.token_hex(4).upper()
        return f"{raw[:4]}-{raw[4:]}"

    async def _reserved_vk_seats(self, event_id: uuid.UUID, now: datetime) -> int:
        result = await self.session.execute(
            select(func.count(VKPayOrder.id)).where(
                VKPayOrder.event_id == event_id,
                VKPayOrder.status == "pending",
                VKPayOrder.expires_at > now,
            )
        )
        return int(result.scalar_one())

    async def _ensure_inventory_available(self, event: Event, now: datetime) -> None:
        reserved = await self._reserved_vk_seats(event.id, now)
        if event.available_tickets - reserved <= 0:
            raise ValueError("Билеты закончились")

    async def _ensure_no_pending_vk_order(self, user_id: uuid.UUID, event_id: uuid.UUID, now: datetime) -> None:
        result = await self.session.execute(
            select(VKPayOrder.id).where(
                VKPayOrder.user_id == user_id,
                VKPayOrder.event_id == event_id,
                VKPayOrder.status == "pending",
                VKPayOrder.expires_at > now,
            )
        )
        if result.scalar_one_or_none() is not None:
            raise ValueError("У вас уже есть ожидающий оплаты заказ")

    async def buy_ticket(self, user_id: uuid.UUID, event_id: uuid.UUID, promo_code: str | None = None) -> Ticket:
        """Purchase a ticket for an event.

        promo_code: опциональный промокод — применяет скидку к цене билета.
        """
        start = time.perf_counter()
        now = datetime.now(timezone.utc)  # единый момент: валидация и цена по дате
        event_result = await self.session.execute(
            select(Event).where(Event.id == event_id).with_for_update()
        )
        event = event_result.scalar_one_or_none()
        if event is None:
            logger.warning("", extra={
                "event_type": "ticket.purchase_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "status": "error",
                "error": "Event not found",
                "duration_ms": _ms(start),
            })
            raise ValueError("Мероприятие не найдено")
        if not event.is_published:
            logger.warning("", extra={
                "event_type": "ticket.purchase_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Event not published",
                "duration_ms": _ms(start),
            })
            raise ValueError("Мероприятие не опубликовано")
        if not event.is_active:
            logger.warning("", extra={
                "event_type": "ticket.purchase_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Event not active",
                "duration_ms": _ms(start),
            })
            raise ValueError("Мероприятие неактивно")
        if event.date < now:
            logger.warning("", extra={
                "event_type": "ticket.purchase_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Event already passed",
                "duration_ms": _ms(start),
            })
            raise ValueError("Мероприятие уже прошло")
        try:
            await self._ensure_inventory_available(event, now)
        except ValueError:
            logger.warning("", extra={
                "event_type": "ticket.purchase_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Sold out",
                "duration_ms": _ms(start),
            })
            raise ValueError("Билеты закончились")

        await self._ensure_no_pending_vk_order(user_id, event_id, now)

        # Check if user already has an active ticket for this event
        existing_stmt = select(Ticket).where(
            and_(
                Ticket.user_id == user_id,
                Ticket.event_id == event_id,
                Ticket.status == TicketStatus.active,
            )
        )
        existing = await self.session.execute(existing_stmt)
        if existing.scalar_one_or_none() is not None:
            logger.warning("", extra={
                "event_type": "ticket.purchase_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Already has ticket",
                "duration_ms": _ms(start),
            })
            raise ValueError("У вас уже есть активный билет на это мероприятие")

        # Create ticket
        ticket = Ticket(
            id=uuid.uuid4(),  # явно, чтобы ticket.id не был None до flush
            event_id=event_id,
            user_id=user_id,
            status=TicketStatus.active,
            validation_code=await self.generate_validation_code(),
            is_free=event.is_free,
        )
        self.session.add(ticket)

        # Decrease available tickets
        event.available_tickets -= 1

        # Актуальная цена по дате (динамические цены) → фиксируется в base_amount
        event_svc = EventService(self.session)
        base = await event_svc.effective_price_at(event, now)

        # Применение промокода (если передан)
        discount = Decimal("0")
        pcode = None
        if promo_code:
            promo = await self._validate_promo(event_id, promo_code)
            discount = self._compute_discount(promo, base)
            promo.used_count += 1
            pcode = promo.code
        amount = max(base - discount, Decimal("0"))

        # Create payment stub
        payment = Payment(
            ticket_id=ticket.id,
            amount=amount,
            status=PaymentStatus.completed,  # stub — mark as completed
            base_amount=base,
            discount_amount=discount,
            promo_code=pcode,
        )
        self.session.add(payment)

        await self.session.flush()

        logger.info("", extra={
            "event_type": "ticket.purchased",
            "ticket_id": str(ticket.id),
            "event_id": str(event_id),
            "event_title": event.title,
            "user_id": str(user_id),
            "amount": float(amount),
            "base_amount": float(base),
            "discount_amount": float(discount),
            "promo_code": pcode,
            "is_free": ticket.is_free,
            "validation_code": ticket.validation_code,
            "payment_status": "completed",
            "platform": "telegram",
            "status": "success",
            "duration_ms": _ms(start),
        })
        return ticket

    async def buy_ticket_webapp(self, user_id: uuid.UUID, event_id: uuid.UUID, promo_code: str | None = None) -> dict:
        """Purchase a ticket through the Mini App.

        Same validation as buy_ticket(), but creates Payment with
        status=pending (for future YooKassa integration) and returns
        a dict for the API response.
        """
        start = time.perf_counter()
        now = datetime.now(timezone.utc)  # единый момент: валидация и цена по дате
        event_result = await self.session.execute(
            select(Event).where(Event.id == event_id).with_for_update()
        )
        event = event_result.scalar_one_or_none()
        if event is None:
            logger.warning("", extra={
                "event_type": "ticket.purchase_webapp_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "status": "error",
                "error": "Event not found",
                "duration_ms": _ms(start),
            })
            raise ValueError("Мероприятие не найдено")
        if not event.is_published:
            logger.warning("", extra={
                "event_type": "ticket.purchase_webapp_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Event not published",
                "duration_ms": _ms(start),
            })
            raise ValueError("Мероприятие не опубликовано")
        if not event.is_active:
            logger.warning("", extra={
                "event_type": "ticket.purchase_webapp_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Event not active",
                "duration_ms": _ms(start),
            })
            raise ValueError("Мероприятие неактивно")
        if event.date < now:
            logger.warning("", extra={
                "event_type": "ticket.purchase_webapp_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Event already passed",
                "duration_ms": _ms(start),
            })
            raise ValueError("Мероприятие уже прошло")
        try:
            await self._ensure_inventory_available(event, now)
        except ValueError:
            logger.warning("", extra={
                "event_type": "ticket.purchase_webapp_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Sold out",
                "duration_ms": _ms(start),
            })
            raise ValueError("Билеты закончились")

        await self._ensure_no_pending_vk_order(user_id, event_id, now)

        # Check if user already has an active ticket for this event
        existing_stmt = select(Ticket).where(
            and_(
                Ticket.user_id == user_id,
                Ticket.event_id == event_id,
                Ticket.status == TicketStatus.active,
            )
        )
        existing = await self.session.execute(existing_stmt)
        if existing.scalar_one_or_none() is not None:
            logger.warning("", extra={
                "event_type": "ticket.purchase_webapp_failed",
                "event_id": str(event_id),
                "user_id": str(user_id),
                "event_title": event.title,
                "status": "error",
                "error": "Already has ticket",
                "duration_ms": _ms(start),
            })
            raise ValueError("У вас уже есть активный билет на это мероприятие")

        # Create ticket
        ticket = Ticket(
            id=uuid.uuid4(),
            event_id=event_id,
            user_id=user_id,
            status=TicketStatus.active,
            validation_code=await self.generate_validation_code(),
            is_free=event.is_free,
        )
        self.session.add(ticket)

        # Decrease available tickets
        event.available_tickets -= 1

        # Актуальная цена по дате (динамические цены) → фиксируется в base_amount
        event_svc = EventService(self.session)
        base = await event_svc.effective_price_at(event, now)

        # Применение промокода (если передан)
        discount = Decimal("0")
        pcode = None
        if promo_code:
            promo = await self._validate_promo(event_id, promo_code)
            discount = self._compute_discount(promo, base)
            promo.used_count += 1
            pcode = promo.code
        amount = max(base - discount, Decimal("0"))

        # Create payment stub with pending status (Mini App flow)
        payment = Payment(
            ticket_id=ticket.id,
            amount=amount,
            status=PaymentStatus.pending,
            base_amount=base,
            discount_amount=discount,
            promo_code=pcode,
        )
        self.session.add(payment)

        await self.session.flush()

        logger.info("", extra={
            "event_type": "ticket.purchased_webapp",
            "ticket_id": str(ticket.id),
            "event_id": str(event_id),
            "event_title": event.title,
            "user_id": str(user_id),
            "amount": float(amount),
            "base_amount": float(base),
            "discount_amount": float(discount),
            "promo_code": pcode,
            "is_free": ticket.is_free,
            "validation_code": ticket.validation_code,
            "payment_status": "pending",
            "platform": "web",
            "status": "success",
            "duration_ms": _ms(start),
        })

        return {
            "ticket_id": str(ticket.id),
            "event_title": event.title,
            "event_date": event.date.isoformat(),
            "amount": float(amount),
            "base_amount": float(base),
            "discount_amount": float(discount),
            "promo_code": pcode,
            "payment_id": str(payment.id),
            "payment_status": payment.status.value,
            "purchase_date": ticket.purchase_date.isoformat(),
            "validation_code": ticket.validation_code,
            "is_free": ticket.is_free,
        }

    # ═══════════════════════════════════════════════════════════
    # Промокоды (скидки на билеты, pro-фича)
    # ═══════════════════════════════════════════════════════════

    async def create_promo_code(
        self,
        event_id: uuid.UUID,
        code: str,
        discount_type: DiscountType,
        discount_value: float,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        max_uses: int = 0,
    ) -> PromoCode:
        """Создать промокод для мероприятия.

        Код нормализуется (strip + upper), уникален в рамках события.
        Валидация: percent 0<value<=100, fixed value>=0, end>start если оба заданы.
        max_uses=0 — без лимита использований.
        """
        code = (code or "").strip().upper()
        if not code:
            raise ValueError("Укажите код промокода")

        if discount_type == DiscountType.percent and not (0 < discount_value <= 100):
            raise ValueError("Процент скидки должен быть от 1 до 100")
        if discount_type == DiscountType.fixed and discount_value < 0:
            raise ValueError("Скидка не может быть отрицательной")
        if starts_at is not None and ends_at is not None and ends_at <= starts_at:
            raise ValueError("Дата окончания должна быть позже даты начала")

        promo = PromoCode(
            event_id=event_id,
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            starts_at=starts_at,
            ends_at=ends_at,
            max_uses=max_uses or 0,
        )
        self.session.add(promo)
        await self.session.flush()
        return promo

    async def list_promo_codes(self, event_id: uuid.UUID) -> list[dict]:
        """Список промокодов мероприятия для отображения."""
        stmt = select(PromoCode).where(PromoCode.event_id == event_id).order_by(PromoCode.created_at)
        result = await self.session.execute(stmt)
        promos = result.scalars().all()
        return [
            {
                "id": str(p.id),
                "code": p.code,
                "discount_type": p.discount_type.value,
                "discount_value": float(p.discount_value),
                "starts_at": p.starts_at.isoformat() if p.starts_at else None,
                "ends_at": p.ends_at.isoformat() if p.ends_at else None,
                "max_uses": p.max_uses,
                "used_count": p.used_count,
                "is_active": p.is_active,
                "created_at": p.created_at.isoformat(),
            }
            for p in promos
        ]

    async def toggle_promo_code(self, code_id: uuid.UUID) -> PromoCode:
        """Вкл/выкл промокода (ручная деактивация)."""
        promo = await self.session.get(PromoCode, code_id)
        if promo is None:
            raise ValueError("Промокод не найден")
        promo.is_active = not promo.is_active
        await self.session.flush()
        return promo

    async def get_promo_code_by_id(self, code_id: uuid.UUID) -> PromoCode | None:
        """Промокод по ID (для проверки доступа к событию в toggle-эндпоинте)."""
        return await self.session.get(PromoCode, code_id)

    async def _get_promo_code(self, event_id: uuid.UUID, code: str) -> PromoCode | None:
        stmt = select(PromoCode).where(
            and_(PromoCode.event_id == event_id, PromoCode.code == code)
        ).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _validate_promo(self, event_id: uuid.UUID, code: str | None) -> PromoCode:
        """Проверить промокод перед применением. Raises ValueError с понятным сообщением."""
        code = (code or "").strip().upper()
        promo = await self._get_promo_code(event_id, code)
        if promo is None:
            raise ValueError("Промокод не найден")
        if promo.event_id != event_id:
            raise ValueError("Промокод не применим к этому мероприятию")
        if not promo.is_active:
            raise ValueError("Промокод неактивен")
        now = datetime.now(timezone.utc)
        if promo.starts_at is not None and now < promo.starts_at:
            raise ValueError("Промокод ещё не действует")
        if promo.ends_at is not None and now > promo.ends_at:
            raise ValueError("Срок действия промокода истёк")
        if promo.max_uses > 0:
            reserved_result = await self.session.execute(
                select(func.count(VKPayOrder.id)).where(
                    VKPayOrder.promo_code_id == promo.id,
                    VKPayOrder.status == "pending",
                    VKPayOrder.expires_at > now,
                )
            )
            reserved_uses = int(reserved_result.scalar_one())
            if promo.used_count + reserved_uses >= promo.max_uses:
                raise ValueError("Лимит использований промокода исчерпан")
        return promo

    @staticmethod
    def _compute_discount(promo: PromoCode, base_price: Decimal) -> Decimal:
        """Расчёт скидки от базовой цены (Decimal, округление до 2 знаков)."""
        if promo.discount_type == DiscountType.percent:
            value = Decimal(str(promo.discount_value))
            discount = (base_price * value / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            discount = min(Decimal(str(promo.discount_value)), base_price)
        return discount

    async def cancel_ticket(self, ticket_id: uuid.UUID, user_id: uuid.UUID) -> Ticket:
        """Cancel a ticket (refund)."""
        start = time.perf_counter()
        ticket = await self.session.get(Ticket, ticket_id)
        if ticket is None:
            logger.warning("", extra={
                "event_type": "ticket.cancel_failed",
                "ticket_id": str(ticket_id),
                "user_id": str(user_id),
                "status": "error",
                "error": "Ticket not found",
                "duration_ms": _ms(start),
            })
            raise ValueError("Билет не найден")
        if ticket.user_id != user_id:
            logger.warning("", extra={
                "event_type": "ticket.cancel_failed",
                "ticket_id": str(ticket_id),
                "user_id": str(user_id),
                "status": "error",
                "error": "Not owner",
                "duration_ms": _ms(start),
            })
            raise ValueError("Это не ваш билет")
        if ticket.status == TicketStatus.refunded:
            logger.warning("", extra={
                "event_type": "ticket.cancel_failed",
                "ticket_id": str(ticket_id),
                "user_id": str(user_id),
                "status": "error",
                "error": "Already refunded",
                "duration_ms": _ms(start),
            })
            raise ValueError("Билет уже возвращён")

        payment_stmt = select(Payment).where(Payment.ticket_id == ticket_id)
        payment_result = await self.session.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()
        if payment and payment.provider == "vk_pay" and payment.status == PaymentStatus.completed:
            raise ValueError("Возврат оплаченного билета VK Pay пока недоступен")

        # Mark ticket as refunded
        ticket.status = TicketStatus.refunded

        # Restore available tickets
        event = await self.session.get(Event, ticket.event_id)
        if event:
            event.available_tickets += 1

        if payment:
            payment.status = PaymentStatus.refunded

        await self.session.flush()

        logger.info("", extra={
            "event_type": "ticket.cancelled",
            "ticket_id": str(ticket_id),
            "event_id": str(ticket.event_id),
            "event_title": event.title if event else "",
            "user_id": str(user_id),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return ticket

    async def admin_cancel_ticket(self, ticket_id: uuid.UUID) -> Ticket:
        """Cancel any ticket by admin (no ownership check)."""
        start = time.perf_counter()
        ticket = await self.session.get(Ticket, ticket_id)
        if ticket is None:
            logger.warning("", extra={
                "event_type": "ticket.admin_cancel_failed",
                "ticket_id": str(ticket_id),
                "status": "error",
                "error": "Ticket not found",
                "duration_ms": _ms(start),
            })
            raise ValueError("Билет не найден")
        if ticket.status == TicketStatus.refunded:
            logger.warning("", extra={
                "event_type": "ticket.admin_cancel_failed",
                "ticket_id": str(ticket_id),
                "status": "error",
                "error": "Already refunded",
                "duration_ms": _ms(start),
            })
            raise ValueError("Билет уже возвращён")

        payment_stmt = select(Payment).where(Payment.ticket_id == ticket_id)
        payment_result = await self.session.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()
        if payment and payment.provider == "vk_pay" and payment.status == PaymentStatus.completed:
            raise ValueError("Возврат оплаченного билета VK Pay пока недоступен")

        ticket.status = TicketStatus.refunded

        event = await self.session.get(Event, ticket.event_id)
        if event:
            event.available_tickets += 1

        if payment:
            payment.status = PaymentStatus.refunded

        await self.session.flush()

        logger.info("", extra={
            "event_type": "ticket.admin_cancelled",
            "ticket_id": str(ticket_id),
            "event_id": str(ticket.event_id),
            "event_title": event.title if event else "",
            "status": "success",
            "duration_ms": _ms(start),
        })
        return ticket

    async def get_user_tickets(self, user_id: uuid.UUID, channel_id: uuid.UUID | None = None) -> list[dict]:
        """Get all tickets for a user with event info, optionally filtered by channel."""
        start = time.perf_counter()
        stmt = (
            select(Ticket, Event.title, Event.age_restriction)
            .join(Event, Ticket.event_id == Event.id)
            .where(Ticket.user_id == user_id)
        )
        if channel_id is not None:
            stmt = stmt.where(Event.channel_id == channel_id)
        stmt = stmt.order_by(Ticket.purchase_date.desc())
        result = await self.session.execute(stmt)
        rows = result.all()

        tickets = []
        for ticket, event_title, age_restriction in rows:
            tickets.append({
                "id": ticket.id,
                "event_id": ticket.event_id,
                "event_title": event_title,
                "purchase_date": ticket.purchase_date,
                "status": ticket.status.value,
                "validation_code": ticket.validation_code,
                "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
                "is_free": ticket.is_free,
                "age_restriction": age_restriction,
            })

        logger.info("", extra={
            "event_type": "ticket.get_user_tickets",
            "user_id": str(user_id),
            "channel_id": str(channel_id) if channel_id else None,
            "count": len(tickets),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return tickets

    # ─── Admin methods ───────────────────────────────────────────────────

    async def get_event_tickets(self, event_id: uuid.UUID) -> list[dict]:
        """Get all tickets for a specific event (admin view)."""
        start = time.perf_counter()
        stmt = (
            select(Ticket, User.name, User.platform_user_id)
            .join(User, Ticket.user_id == User.id)
            .where(Ticket.event_id == event_id)
            .order_by(Ticket.purchase_date.desc())
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        tickets = []
        for ticket, user_name, platform_user_id in rows:
            tickets.append({
                "id": ticket.id,
                "user_name": user_name or platform_user_id,
                "purchase_date": ticket.purchase_date,
                "status": ticket.status.value,
                "validation_code": ticket.validation_code,
                "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
                "checked_in_by": ticket.checked_in_by,
                "is_free": ticket.is_free,
                "is_invite": ticket.is_invite,
                "seats": ticket.seats,
            })

        logger.info("", extra={
            "event_type": "ticket.get_event_tickets",
            "event_id": str(event_id),
            "count": len(tickets),
            "status": "success",
            "duration_ms": _ms(start),
        })
        return tickets

    async def get_ticket_event(self, ticket_id: uuid.UUID) -> tuple[Ticket, Event] | None:
        """Load a ticket together with its event (for channel-scoping admin actions)."""
        ticket = await self.session.get(Ticket, ticket_id)
        if ticket is None:
            return None
        event = await self.session.get(Event, ticket.event_id)
        if event is None:
            return None
        return ticket, event

    async def export_event_tickets(self, event_id: uuid.UUID) -> list[dict]:
        """Full ticket rows for an event (CSV export)."""
        stmt = (
            select(Ticket, User.name, Event.title)
            .join(User, Ticket.user_id == User.id)
            .join(Event, Ticket.event_id == Event.id)
            .where(Ticket.event_id == event_id)
            .order_by(Ticket.purchase_date.desc())
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        tickets = []
        for ticket, user_name, event_title in rows:
            tickets.append({
                "ticket_id": str(ticket.id),
                "event_title": event_title,
                "user_name": user_name or "",
                "purchase_date": ticket.purchase_date.isoformat(),
                "status": ticket.status.value,
                "validation_code": ticket.validation_code or "",
                "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else "",
                "checked_in_by": ticket.checked_in_by or "",
                "is_free": "да" if ticket.is_free else "нет",
                "is_invite": "да" if ticket.is_invite else "нет",
                "seats": ticket.seats,
            })
        return tickets

    # ─── Validation & Check-in ────────────────────────────────────────

    async def validate_ticket(self, code: str) -> dict:
        """Проверить билет по validation_code.

        Args:
            code: Код билета (формат XXXX-XXXX).

        Returns:
            dict с результатами проверки:
                found: bool — найден ли билет
                status: str — статус (active/checked_in/refunded/not_found)
                user_name: str — имя покупателя (если найден)
                event_title: str — название мероприятия
                already_checked_in: bool — уже ли использован
                checked_in_at: str|None — время чекина (если был)
                event_id: str|None — ID мероприятия (для проверки доступа)
                ticket_id: str|None — ID билета
        """
        start = time.perf_counter()
        stmt = (
            select(Ticket, User.name, Event.title)
            # LEFT JOIN: пригласительные (user_id=None) тоже должны находиться по коду
            .outerjoin(User, Ticket.user_id == User.id)
            .join(Event, Ticket.event_id == Event.id)
            .where(Ticket.validation_code == code)
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()

        if row is None:
            logger.info("", extra={
                "event_type": "ticket.validate_not_found",
                "code": code,
                "status": "not_found",
                "duration_ms": _ms(start),
            })
            return {"found": False, "status": "not_found"}

        ticket, user_name, event_title = row

        result_data = {
            "found": True,
            "status": ticket.status.value,
            "user_name": user_name or "—",
            "event_title": event_title,
            "already_checked_in": ticket.status == TicketStatus.checked_in,
            "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
            # Для проверки доступа в web-маршруте (GET /admin/tickets/validate):
            "event_id": str(ticket.event_id),
            "ticket_id": str(ticket.id),
        }

        logger.info("", extra={
            "event_type": "ticket.validate",
            "ticket_id": str(ticket.id),
            "code": code,
            "status": ticket.status.value,
            "duration_ms": _ms(start),
        })
        return result_data

    async def check_in(self, ticket_id: uuid.UUID, admin_id: str) -> Ticket:
        """Отметить билет как использованный на входе.

        Args:
            ticket_id: UUID билета.
            admin_id: Telegram ID проверяющего.

        Returns:
            Ticket с обновлённым статусом.

        Raises:
            ValueError: если билет не найден, уже использован или возвращён.
        """
        start = time.perf_counter()
        ticket = await self.session.get(Ticket, ticket_id)
        if ticket is None:
            logger.warning("", extra={
                "event_type": "ticket.checkin_failed",
                "ticket_id": str(ticket_id),
                "status": "error",
                "error": "Ticket not found",
                "duration_ms": _ms(start),
            })
            raise ValueError("Билет не найден")

        if ticket.status == TicketStatus.checked_in:
            logger.warning("", extra={
                "event_type": "ticket.checkin_failed",
                "ticket_id": str(ticket_id),
                "status": "error",
                "error": "Already checked in",
                "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else "",
                "duration_ms": _ms(start),
            })
            raise ValueError(f"Билет уже использован (вход: {ticket.checked_in_at.strftime('%H:%M')})")

        if ticket.status == TicketStatus.refunded:
            logger.warning("", extra={
                "event_type": "ticket.checkin_failed",
                "ticket_id": str(ticket_id),
                "status": "error",
                "error": "Ticket refunded",
                "duration_ms": _ms(start),
            })
            raise ValueError("Билет возвращён")

        ticket.status = TicketStatus.checked_in
        ticket.checked_in_at = datetime.now(timezone.utc)
        ticket.checked_in_by = admin_id
        await self.session.flush()

        logger.info("", extra={
            "event_type": "ticket.checked_in",
            "ticket_id": str(ticket.id),
            "event_id": str(ticket.event_id),
            "user_id": str(ticket.user_id),
            "admin_id": admin_id,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return ticket

    async def check_in_by_code(self, code: str, admin_id: str) -> Ticket:
        """Отметить билет по validation_code.

        Args:
            code: Код билета (XXXX-XXXX).
            admin_id: Telegram ID проверяющего.

        Returns:
            Ticket с обновлённым статусом.

        Raises:
            ValueError: если билет с таким кодом не найден.
        """
        stmt = select(Ticket).where(Ticket.validation_code == code)
        result = await self.session.execute(stmt)
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError(f"Билет с кодом {code} не найден")
        return await self.check_in(ticket.id, admin_id)

    # ─── Пригласительные (invite tickets, pro) ─────────────────────

    async def issue_invite(
        self,
        event_id: uuid.UUID,
        seats: int = 1,
        issued_by: str | None = None,
    ) -> Ticket:
        """Выдать пригласительный билет на мероприятие (бесплатно).

        Пригласительный занимает `seats` мест из непроданных и не создаёт Payment.
        Проверяет: событие активно/не прошло, хватает мест, не превышена квота.

        Raises:
            ValueError: если событие невалидно, нет мест или исчерпана квота.
        """
        start = time.perf_counter()
        event = await self.session.get(Event, event_id)
        if event is None:
            raise ValueError("Мероприятие не найдено")
        if not event.is_active:
            raise ValueError("Мероприятие неактивно")
        if event.date < datetime.now(timezone.utc):
            raise ValueError("Мероприятие уже прошло")
        if event.invites_quota <= 0:
            raise ValueError("Квота пригласительных не настроена (invites_quota=0)")
        if seats < 1 or seats > 3:
            raise ValueError("Вместимость пригласительного: 1, 2 или 3 человека")
        if event.available_tickets < seats:
            raise ValueError(f"Не хватает свободных мест (свободно: {event.available_tickets})")

        # Подсчёт уже выданных пригласительных
        issued_stmt = select(func.count(Ticket.id)).where(
            and_(Ticket.event_id == event_id, Ticket.is_invite == True)
        )
        already_issued = (await self.session.execute(issued_stmt)).scalar() or 0
        if already_issued >= event.invites_quota:
            raise ValueError("Исчерпана квота пригласительных")

        ticket = Ticket(
            id=uuid.uuid4(),
            event_id=event_id,
            user_id=None,  # пригласительное не привязано к пользователю
            status=TicketStatus.active,
            validation_code=await self.generate_validation_code(),
            is_free=True,
            is_invite=True,
            seats=seats,
            invited_by=issued_by,
        )
        self.session.add(ticket)
        event.available_tickets -= seats
        await self.session.flush()

        logger.info("", extra={
            "event_type": "ticket.invite_issued",
            "ticket_id": str(ticket.id),
            "event_id": str(event_id),
            "event_title": event.title,
            "seats": seats,
            "issued_by": issued_by,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return ticket

    async def cancel_invite(self, ticket_id: uuid.UUID) -> Ticket:
        """Отменить пригласительный — вернуть места в непроданные.

        Raises:
            ValueError: если билет не найден, не пригласительный или уже возвращён.
        """
        ticket = await self.session.get(Ticket, ticket_id)
        if ticket is None:
            raise ValueError("Пригласительное не найдено")
        if not ticket.is_invite:
            raise ValueError("Это не пригласительное")
        if ticket.status == TicketStatus.refunded:
            raise ValueError("Пригласительное уже возвращено")

        ticket.status = TicketStatus.refunded
        event = await self.session.get(Event, ticket.event_id)
        if event:
            event.available_tickets += ticket.seats
        await self.session.flush()
        return ticket

    async def claim_invite(self, code: str, user_id: uuid.UUID) -> Ticket:
        """Активировать пригласительное гостем по коду из ссылки.

        Привязывает пригласительный (is_invite=True) к пользователю-гостю —
        билет появляется в его «Моих билетах». Места резервируются при выдаче
        (available -= seats), здесь только привязка владельца.

        Raises:
            ValueError: код не найден / не пригласительное / уже активировано другим.
        """
        start = time.perf_counter()
        code = code.strip().upper()
        stmt = select(Ticket).where(Ticket.validation_code == code)
        result = await self.session.execute(stmt)
        ticket = result.scalar_one_or_none()

        if ticket is None:
            raise ValueError("Билет не найден")
        if not ticket.is_invite:
            raise ValueError("Это не пригласительное")
        if ticket.status == TicketStatus.refunded:
            raise ValueError("Пригласительное отозвано")
        if ticket.user_id is not None and ticket.user_id != user_id:
            raise ValueError("Пригласительное уже активировано другим гостем")

        ticket.user_id = user_id
        await self.session.flush()

        logger.info("", extra={
            "event_type": "ticket.invite_claimed",
            "ticket_id": str(ticket.id),
            "event_id": str(ticket.event_id),
            "user_id": str(user_id),
            "seats": ticket.seats,
            "status": "success",
            "duration_ms": _ms(start),
        })
        return ticket

    async def get_event_invites(self, event_id: uuid.UUID) -> list[dict]:
        """Список пригласительных по мероприятию (админ)."""
        stmt = (
            select(Ticket)
            .where(and_(Ticket.event_id == event_id, Ticket.is_invite == True))
            .order_by(Ticket.purchase_date.desc())
        )
        result = await self.session.execute(stmt)
        tickets = list(result.scalars().all())

        return [
            {
                "id": ticket.id,
                "validation_code": ticket.validation_code,
                "seats": ticket.seats,
                "status": ticket.status.value,
                "is_invite": ticket.is_invite,
                "invited_by": ticket.invited_by,
                "purchase_date": ticket.purchase_date.isoformat(),
                "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
            }
            for ticket in tickets
        ]

    async def get_by_code(self, code: str) -> Ticket | None:
        """Найти билет по validation_code (для deep-link пригласительного)."""
        stmt = select(Ticket).where(Ticket.validation_code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


# ─── Stats Service ──────────────────────────────────────────────────────────

class StatsService:
    """Aggregated statistics for the admin panel (super-admin)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_global_stats(self) -> dict:
        """Global counts across all tenants (mirrors bot sa_stats_all)."""
        start = time.perf_counter()
        users_count = (await self.session.execute(
            select(func.count()).select_from(User)
        )).scalar() or 0

        channels_count = (await self.session.execute(
            select(func.count()).select_from(Channel)
        )).scalar() or 0

        active_subs = (await self.session.execute(
            select(func.count()).select_from(Channel).where(Channel.is_subscription_active == True)
        )).scalar() or 0

        events_count = (await self.session.execute(
            select(func.count()).select_from(Event)
        )).scalar() or 0

        upcoming_count = (await self.session.execute(
            select(func.count()).select_from(Event).where(Event.date >= datetime.now(timezone.utc))
        )).scalar() or 0

        tickets_active = (await self.session.execute(
            select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.active)
        )).scalar() or 0

        revenue = float((await self.session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == PaymentStatus.completed)
        )).scalar() or 0)

        logger.info("", extra={
            "event_type": "stats.global",
            "users": users_count,
            "channels": channels_count,
            "active_subs": active_subs,
            "events": events_count,
            "upcoming": upcoming_count,
            "tickets_active": tickets_active,
            "revenue": revenue,
            "status": "success",
            "duration_ms": _ms(start),
        })

        return {
            "users_count": users_count,
            "channels_count": channels_count,
            "active_subs": active_subs,
            "events_count": events_count,
            "upcoming_count": upcoming_count,
            "tickets_active": tickets_active,
            "revenue": revenue,
        }
