import uuid
from datetime import datetime, timezone
import secrets

from sqlalchemy import String, Integer, Numeric, Boolean, DateTime, ForeignKey, Text, Enum as SAEnum, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

import enum


class SubscriptionTier(str, enum.Enum):
    """Уровень подписки канала."""
    basic = "basic"  # только бесплатные мероприятия, короткий код
    pro = "pro"      # платные мероприятия + QR


class PeriodUnit(str, enum.Enum):
    """Единица измерения срока подписки."""
    days = "days"
    months = "months"
    years = "years"


class TicketStatus(str, enum.Enum):
    active = "active"
    checked_in = "checked_in"
    refunded = "refunded"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    refunded = "refunded"


class DiscountType(str, enum.Enum):
    """Тип скидки промокода."""
    percent = "percent"  # процент от суммы
    fixed = "fixed"      # фиксированная скидка в единицах стоимости


class PlatformType(str, enum.Enum):
    telegram = "telegram"
    vk = "vk"
    max = "max"


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    telegram_channel_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    admin_telegram_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    is_subscription_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(
        SAEnum(SubscriptionTier), default=SubscriptionTier.basic, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    events = relationship("Event", back_populates="channel", lazy="raise")
    admins = relationship("ChannelAdmin", back_populates="channel", lazy="raise", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Channel {self.telegram_channel_id}>"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[PlatformType] = mapped_column(
        SAEnum(PlatformType), nullable=False
    )
    platform_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Ник пользователя (Telegram username без @) — для поиска суперадмином.
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # Подписка пользователя (организатор без канала)
    is_subscription_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(
        SAEnum(SubscriptionTier), default=SubscriptionTier.basic, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tickets = relationship("Ticket", back_populates="user", lazy="raise")
    owned_events = relationship("Event", back_populates="owner", lazy="raise")
    identities = relationship("UserIdentity", back_populates="user", lazy="raise", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.platform}:{self.platform_user_id}>"


class UserIdentity(Base):
    """Каноническая идентичность организатора.

    Каждый пользователь может иметь несколько способов входа (identity):
    telegram / vk. Все они ведут к одному каноническому пользователю (users.id),
    что позволяет вести мероприятия и подписку с любой площадки.

    UNIQUE(platform, platform_user_id) — одна площадка+ID может быть привязана
    только к одному каноническому пользователю.
    """

    __tablename__ = "user_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[PlatformType] = mapped_column(
        SAEnum(PlatformType), nullable=False
    )
    platform_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", back_populates="identities", lazy="raise")

    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_user_identity_platform_puid"),
    )

    def __repr__(self):
        return f"<UserIdentity {self.platform}:{self.platform_user_id} → user {self.user_id}>"


class LinkCode(Base):
    """Одноразовый короткоживущий код привязки площадок (organizer-only).

    Генерируется на TG-стороне («Привязать VK»), вводится на VK-стороне.
    После успешной привязки — consumed (одноразовый).
    """

    __tablename__ = "link_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_platform: Mapped[PlatformType] = mapped_column(
        SAEnum(PlatformType), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", lazy="raise")

    def __repr__(self):
        return f"<LinkCode {self.code} → user {self.user_id} ({self.target_platform.value})>"


class VKGroup(Base):
    """VK-группа — цель публикации анонсов (аналог TG-канала).

    Организатор добавляет группу сам (по необходимости), community token
    хранится зашифрованным (app/core/crypto.py). Подписка-изоляция группе
    не нужна — организатор платит подписку на себя.
    """

    __tablename__ = "vk_groups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    community_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    owner = relationship("User", lazy="raise")

    def __repr__(self):
        return f"<VKGroup {self.group_id}>"


class EventPublication(Base):
    """Публикация события в конкретную цель (placements).

    Одно событие может быть опубликовано в N мест: TG-канал, VK-группа (стена),
    сообщения группы, личные сообщения. target_type:
        telegram_channel | vk_group_wall | vk_group_message | vk_user_dm
    """

    __tablename__ = "event_publications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[PlatformType] = mapped_column(
        SAEnum(PlatformType), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="posted", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    event = relationship("Event", lazy="raise")

    __table_args__ = (
        UniqueConstraint("event_id", "platform", "target_type", "target_id", name="uq_event_publication"),
    )

    def __repr__(self):
        return f"<EventPublication {self.event_id} → {self.platform.value}:{self.target_type}:{self.target_id}>"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # channel_id nullable: мероприятие может принадлежать каналу ИЛИ владельцу-пользователю
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id"), nullable=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), nullable=False, default=0.0)
    total_tickets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_tickets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Дата публикации (начало обязательного покрытия ценовых диапазонов по дате).
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    media_telegram_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "photo" или "video"
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    invites_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Возрастное ограничение (0+/6+/12+/16+/18+) — знак информационной продукции
    # по ФЗ-436. Устанавливается организатором при создании мероприятия.
    age_restriction: Mapped[str] = mapped_column(String(4), nullable=False, default="0+")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    tickets = relationship("Ticket", back_populates="event", lazy="raise")
    channel = relationship("Channel", back_populates="events", lazy="raise")
    owner = relationship("User", back_populates="owned_events", lazy="raise")
    managers = relationship("EventManager", back_populates="event", lazy="raise", cascade="all, delete-orphan")
    price_ranges = relationship("EventPriceRange", back_populates="event", lazy="raise", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Event {self.title}>"


class EventManager(Base):
    """Соработник мероприятия (несколько продавцов на одном событии).

    M2M event_managers(event_id, user_id). Менеджер ведёт продажи: публикация,
    check-in, статистика, билеты, пригласительные. Управление событием
    (редактирование/удаление/менеджеры) — только у владельца (owner).
    """

    __tablename__ = "event_managers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    event = relationship("Event", back_populates="managers", lazy="raise")
    user = relationship("User", lazy="raise")

    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_manager"),
    )

    def __repr__(self):
        return f"<EventManager {self.event_id}:{self.user_id}>"


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id"), nullable=False
    )
    # user_id nullable: пригласительные (is_invite=True) не привязаны к пользователю
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    purchase_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus), default=TicketStatus.active, nullable=False
    )
    validation_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_in_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_invite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    seats: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    invited_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    qr_code_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    event = relationship("Event", back_populates="tickets", lazy="raise")
    user = relationship("User", back_populates="tickets", lazy="raise")
    payment = relationship("Payment", back_populates="ticket", uselist=False, lazy="raise")

    def __repr__(self):
        return f"<Ticket {self.id} — {self.event_id}>"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False, unique=True
    )
    amount: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus), default=PaymentStatus.pending, nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    # Промокод, применённый при покупке (nullable — исторические платежи без скидки).
    # amount — фактически уплачено (со скидкой); base_amount — исходная цена события.
    base_amount: Mapped[float | None] = mapped_column(Numeric(precision=10, scale=2), nullable=True)
    discount_amount: Mapped[float | None] = mapped_column(Numeric(precision=10, scale=2), nullable=True)
    promo_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Снимок ценового диапазона, по которому куплен билет (динамические цены, pro).
    price_range_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    ticket = relationship("Ticket", back_populates="payment", lazy="raise")

    def __repr__(self):
        return f"<Payment {self.id} — {self.status.value}>"


class EventUpgrade(Base):
    """Per-event премиум — единовременная оплата pro-фич на одно мероприятие.

    Даёт paid_events / qr_codes / invite_tickets для конкретного события,
    независимо от подписки организатора (User/Channel). Оплата — заглушка
    (status=completed), колонка provider — под будущий Telegram Stars / YooKassa.
    expires_at = event.date (премиум действует до даты мероприятия).
    """

    __tablename__ = "event_upgrades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    amount: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), default=0, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus), default=PaymentStatus.completed, nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    event = relationship("Event", lazy="raise")

    def __repr__(self):
        return f"<EventUpgrade {self.event_id} — {self.status.value}>"


class PromoCode(Base):
    """Скидка-промокод на билеты одного мероприятия (pro-фича).

    Привязан к событию (event_id). Организатор создаёт код с типом скидки
    (percent — процент от суммы / fixed — фиксированная сумма), сроком действия
    (starts_at/ends_at, None = без границы), лимитом использований
    (max_uses, 0 = без лимита) и ручным вкл/выкл (is_active).
    """

    __tablename__ = "promo_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    discount_type: Mapped[DiscountType] = mapped_column(SAEnum(DiscountType), nullable=False)
    discount_value: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 = без лимита
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    event = relationship("Event", lazy="raise")

    __table_args__ = (UniqueConstraint("event_id", "code", name="uq_promo_code_event_code"),)

    def __repr__(self):
        return f"<PromoCode {self.code} — {self.discount_type.value}:{self.discount_value}>"


class EventPriceRange(Base):
    """Ценовой диапазон мероприятия по дате (динамические цены, pro).

    Диапазоны обязаны без дыр покрывать [event.published_at, event.date].
    Покупатель платит цену диапазона, покрывающего дату покупки;
    цена фиксируется в Payment.base_amount при покупке.
    """

    __tablename__ = "event_price_ranges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    event = relationship("Event", back_populates="price_ranges", lazy="raise")

    def __repr__(self):
        return f"<EventPriceRange {self.event_id} {self.starts_at}-{self.ends_at}: {self.price}>"


class VKPayOrder(Base):
    """Temporary ticket reservation awaiting verified VK Pay settlement."""

    __tablename__ = "vk_pay_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    issuer_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), nullable=False)
    base_amount: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), nullable=False)
    discount_amount: Mapped[float] = mapped_column(Numeric(precision=10, scale=2), default=0, nullable=False)
    promo_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("promo_codes.id", ondelete="SET NULL"), nullable=True
    )
    promo_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_vk_pay_order_event_status_expires", "event_id", "status", "expires_at"),
        Index("ix_vk_pay_order_promo_status_expires", "promo_code_id", "status", "expires_at"),
    )

    def __repr__(self):
        return f"<VKPayOrder {self.issuer_id} — {self.status}>"


class ChannelAdmin(Base):
    __tablename__ = "channel_admins"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    telegram_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    channel = relationship("Channel", back_populates="admins", lazy="raise")

    __table_args__ = (UniqueConstraint("channel_id", "telegram_user_id", name="uq_channel_admin"),)

    def __repr__(self):
        return f"<ChannelAdmin {self.channel_id}:{self.telegram_user_id}>"
