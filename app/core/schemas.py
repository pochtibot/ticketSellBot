from datetime import datetime
from uuid import UUID
from typing import Optional, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.models import SubscriptionTier, PeriodUnit, PlatformType, DiscountType

# Допустимые знаки информационной продукции (ФЗ-436, ст. 6).
AGE_RESTRICTIONS = ("0+", "6+", "12+", "16+", "18+")


# ─── User ────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    platform: str
    platform_user_id: str
    name: Optional[str] = None


class UserOut(BaseModel):
    id: UUID
    platform: str
    platform_user_id: str
    name: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Event ───────────────────────────────────────────────────────────────────

class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    date: datetime
    location: Optional[str] = None
    price: float = Field(default=0.0, ge=0)
    total_tickets: int = Field(default=0, ge=0)
    available_tickets: int = Field(default=0, ge=0)
    channel_id: Optional[UUID] = None
    owner_user_id: Optional[UUID] = None
    invites_quota: int = Field(default=0, ge=0)
    # Возрастное ограничение (ФЗ-436). По умолчанию "0+".
    age_restriction: str = Field(default="0+", max_length=4)

    @field_validator("age_restriction")
    @classmethod
    def _validate_age_restriction(cls, v: str) -> str:
        if v not in AGE_RESTRICTIONS:
            raise ValueError(f"age_restriction должно быть одно из: {', '.join(AGE_RESTRICTIONS)}")
        return v


class EventUpdateIn(BaseModel):
    """Partial update for an event (all fields optional)."""
    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[datetime] = None
    location: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    total_tickets: Optional[int] = Field(default=None, ge=0)
    invites_quota: Optional[int] = Field(default=None, ge=0)
    age_restriction: Optional[str] = Field(default=None, max_length=4)

    @field_validator("age_restriction")
    @classmethod
    def _validate_age_restriction(cls, v: str | None) -> str | None:
        if v is not None and v not in AGE_RESTRICTIONS:
            raise ValueError(f"age_restriction должно быть одно из: {', '.join(AGE_RESTRICTIONS)}")
        return v


class InviteIssueIn(BaseModel):
    """Выдать пригласительный: вместимость 1/2/3 человека."""
    seats: int = Field(default=1, ge=1, le=3)


class PromoCodeCreate(BaseModel):
    """Создать промокод для мероприятия (pro-фича).

    discount_type: percent (процент от суммы) или fixed (фиксированная сумма).
    starts_at/ends_at — срок действия (None = без границы).
    max_uses — лимит использований (0 = без лимита).
    """
    code: str = Field(min_length=1, max_length=64)
    discount_type: DiscountType
    discount_value: float = Field(gt=0)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    max_uses: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_ranges(self):
        if self.discount_type == DiscountType.percent and self.discount_value > 100:
            raise ValueError("Процент скидки должен быть от 1 до 100")
        if self.ends_at is not None and self.starts_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("Дата окончания должна быть позже даты начала")
        return self


class BuyIn(BaseModel):
    """Тело покупки билета: опциональный промокод."""
    promo_code: Optional[str] = Field(default=None, max_length=64)


class PriceRangeIn(BaseModel):
    """Один ценовой диапазон (динамические цены по дате)."""
    starts_at: datetime
    ends_at: datetime
    price: float = Field(ge=0)


class PriceRangesUpdate(BaseModel):
    """Заменить весь набор ценовых диапазонов (пустой список = выключить динамику)."""
    ranges: list[PriceRangeIn] = []


class EventOut(BaseModel):
    id: UUID
    channel_id: UUID
    title: str
    description: Optional[str]
    date: datetime
    location: Optional[str]
    price: float
    total_tickets: int
    available_tickets: int
    is_active: bool
    is_free: bool
    age_restriction: str = "0+"

    model_config = {"from_attributes": True}


class EventShortOut(BaseModel):
    """Short version for event listings."""
    id: UUID
    channel_id: UUID
    title: str
    date: datetime
    location: Optional[str]
    price: float
    available_tickets: int
    age_restriction: str = "0+"

    model_config = {"from_attributes": True}


# ─── Channel ─────────────────────────────────────────────────────────────────

class ChannelCreate(BaseModel):
    telegram_channel_id: str
    title: Optional[str] = None
    admin_telegram_user_id: str


class ChannelOut(BaseModel):
    id: UUID
    telegram_channel_id: str
    title: Optional[str]
    admin_telegram_user_id: str
    is_subscription_active: bool
    subscription_until: Optional[datetime]
    subscription_tier: SubscriptionTier = SubscriptionTier.basic
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Ticket ──────────────────────────────────────────────────────────────────

class TicketOut(BaseModel):
    id: UUID
    event_id: UUID
    event_title: str = ""
    purchase_date: datetime
    status: str
    validation_code: Optional[str] = None
    checked_in_at: Optional[datetime] = None
    is_free: bool = False

    model_config = {"from_attributes": True}


# ─── Admin Panel ─────────────────────────────────────────────────────────────

class SubscribeIn(BaseModel):
    duration_days: int = Field(default=30, gt=0)
    tier: SubscriptionTier = SubscriptionTier.basic


class ChangeAdminIn(BaseModel):
    new_admin_id: str


class CheckInIn(BaseModel):
    code: str


class MeUpdateIn(BaseModel):
    name: Optional[str] = None


class ChannelSubscribeIn(BaseModel):
    """Создать канал (если нет) и активировать подписку."""
    telegram_channel_id: str  # @username или числовой ID
    title: Optional[str] = None
    duration_days: int = Field(default=30, gt=0)
    tier: SubscriptionTier = SubscriptionTier.basic


class BroadcastIn(BaseModel):
    text: str


class UpdateSubscriptionIn(BaseModel):
    """Сменить подписку канала: тип + срок (дни/месяцы/годы)."""
    tier: SubscriptionTier
    period: int = Field(gt=0)
    period_unit: PeriodUnit = PeriodUnit.months


class ChangeTierIn(BaseModel):
    """Сменить только тип подписки (срок не меняется)."""
    tier: SubscriptionTier


class SubscribeMeIn(BaseModel):
    """Покупка/активация подписки пользователя."""
    tier: SubscriptionTier = SubscriptionTier.basic


class ChannelRegisterIn(BaseModel):
    """Связать Telegram-канал с пользователем (без активации подписки)."""
    telegram_channel_id: str   # @username или числовой ID
    title: Optional[str] = None


class LinkCodeIn(BaseModel):
    """Создать одноразовый код привязки площадки (organizer-only)."""
    target_platform: PlatformType
    ttl_minutes: int = Field(default=10, ge=1, le=60)


class LinkConsumeIn(BaseModel):
    """Ввести код привязки на целевой площадке (VK-сторона)."""
    code: str


class AddManagerIn(BaseModel):
    """Добавить соработника мероприятия по платформенному ID."""
    platform: PlatformType
    platform_user_id: str


class VKGroupRegisterIn(BaseModel):
    """Зарегистрировать VK-группу как цель публикации (self-service)."""
    group_id: str  # VK community id (число или -id)
    title: Optional[str] = None
    community_token: Optional[str] = None  # от VKWebAppGetCommunityToken, шифруется


class PublishIn(BaseModel):
    """Публикация мероприятия: цель — TG-канал (channel_id) или VK-группа (vk_group_id)."""
    channel_id: Optional[UUID] = None
    vk_group_id: Optional[str] = None


# ─── Payment ─────────────────────────────────────────────────────────────────

class PaymentOut(BaseModel):
    id: UUID
    ticket_id: UUID
    amount: float
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Per-event premium (единовременная оплата) ───────────────────

class EventPremiumIn(BaseModel):
    """Покупка премиума на одно мероприятие (stub-оплата)."""
    amount: float = 0
    provider: Optional[str] = None


class VKPayProductOrderIn(BaseModel):
    """VK Pay purchase of an organizer subscription or event premium."""
    product_type: Literal["subscription", "event_premium"]
    tier: Optional[SubscriptionTier] = None
    event_id: Optional[UUID] = None
