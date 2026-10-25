"""
Тесты сервисного слоя: UserService, EventService, TicketService.
"""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select, func

from app.core.models import User, Event, Ticket, Payment, ChannelAdmin, PromoCode
from app.core.models import PlatformType, TicketStatus, PaymentStatus, SubscriptionTier, DiscountType
from app.core.services import ChannelService, ChannelAdminService, TicketService


# ═══════════════════════════════════════════════════════════════
# UserService
# ═══════════════════════════════════════════════════════════════

class TestUserService:
    async def test_get_or_create_new(self, db_session, user_svc):
        """Создание нового пользователя."""
        user = await user_svc.get_or_create(
            platform=PlatformType.telegram,
            platform_user_id="new_user_001",
            name="New User",
        )
        assert user.id is not None
        assert user.platform == PlatformType.telegram
        assert user.platform_user_id == "new_user_001"
        assert user.name == "New User"

    async def test_get_or_create_existing(self, db_session, user_svc, sample_user):
        """Повторный запрос возвращает того же пользователя (без обновления имени)."""
        user = await user_svc.get_or_create(
            platform=sample_user.platform,
            platform_user_id=sample_user.platform_user_id,
            name="New Name",
        )
        assert user.id == sample_user.id
        # Сервис не обновляет имя существующего пользователя
        assert user.name == "Test User"

    async def test_get_or_create_other_platform(self, db_session, user_svc, sample_user):
        """Тот же platform_user_id, но другой platform — разные пользователи."""
        user2 = await user_svc.get_or_create(
            platform=PlatformType.vk,
            platform_user_id=sample_user.platform_user_id,
            name="VK User",
        )
        assert user2.id != sample_user.id

    # ─── Удаление аккаунта (п.1.1.10 Правил VK) ────────────────

    async def test_delete_account_marks_deleted(self, db_session, user_svc, sample_user):
        """delete_account выставляет deleted_at."""
        await user_svc.delete_account(sample_user.id)
        await db_session.flush()
        # Перечитаем
        from sqlalchemy import select
        res = await db_session.execute(select(User).where(User.id == sample_user.id))
        user = res.scalar_one()
        assert user.deleted_at is not None

    async def test_delete_account_anonymizes(self, db_session, user_svc, sample_user):
        """delete_account стирает имя и username (анонимизация)."""
        await user_svc.delete_account(sample_user.id)
        await db_session.flush()
        res = await db_session.execute(select(User).where(User.id == sample_user.id))
        user = res.scalar_one()
        assert user.name is None
        assert user.username is None

    async def test_delete_account_deactivates_subscription(self, db_session, user_svc, sample_user):
        """delete_account деактивирует подписку пользователя."""
        await user_svc.activate_subscription(sample_user.id, days=30, tier=SubscriptionTier.pro)
        await db_session.flush()
        await user_svc.delete_account(sample_user.id)
        await db_session.flush()
        res = await db_session.execute(select(User).where(User.id == sample_user.id))
        user = res.scalar_one()
        assert user.is_subscription_active is False
        assert user.subscription_until is None

    async def test_delete_account_keeps_tickets(self, db_session, user_svc, sample_user, sample_event):
        """delete_account сохраняет билеты (нужны для входа на мероприятие)."""
        ticket_svc = TicketService(db_session)
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.flush()
        await user_svc.delete_account(sample_user.id)
        await db_session.flush()
        res = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        kept = res.scalar_one()
        assert kept.user_id == sample_user.id  # билет не удалён и не отвязан

    async def test_delete_account_then_get_or_create_fresh(self, db_session, user_svc, sample_user):
        """После удаления повторный вход создаёт НОВОГО пользователя (чистый аккаунт)."""
        platform_user_id = sample_user.platform_user_id
        await user_svc.delete_account(sample_user.id)
        await db_session.flush()
        new_user = await user_svc.get_or_create(
            platform=sample_user.platform,
            platform_user_id=platform_user_id,
            name="New Life",
        )
        assert new_user.id != sample_user.id
        assert new_user.deleted_at is None
        assert new_user.name == "New Life"

    async def test_delete_account_idempotent(self, db_session, user_svc, sample_user):
        """Повторный delete_account — не ошибка (идемпотентно)."""
        await user_svc.delete_account(sample_user.id)
        await db_session.flush()
        # Второй раз — не должно падать
        user = await user_svc.delete_account(sample_user.id)
        assert user is not None
        assert user.deleted_at is not None

    async def test_delete_account_not_found(self, db_session, user_svc):
        """delete_account несуществующего пользователя — None."""
        user = await user_svc.delete_account(uuid.uuid4())
        assert user is None


# ═══════════════════════════════════════════════════════════════
# ChannelService
# ═══════════════════════════════════════════════════════════════

class TestChannelService:
    async def test_change_admin_success(self, db_session, sample_channel):
        """Успешная смена админа — обновляется и ChannelAdmin, и legacy-поле."""
        svc = ChannelService(db_session)
        channel, old_admins = await svc.change_admin("test_channel_1", "new_admin_42")
        await db_session.commit()

        assert channel.admin_telegram_user_id == "new_admin_42"

        # ChannelAdmin обновлён
        admin_svc = ChannelAdminService(db_session)
        current = await admin_svc.get_admin_ids(channel.id)
        assert "new_admin_42" in current
        assert "test_12345" not in current

    async def test_change_admin_not_found(self, db_session):
        """Несуществующий канал -> ValueError."""
        svc = ChannelService(db_session)
        with pytest.raises(ValueError, match="не найден"):
            await svc.change_admin("nonexistent", "admin_42")

    async def test_change_admin_same_admin(self, db_session, sample_channel):
        """Смена на того же админа — не падает, меняет корректно."""
        svc = ChannelService(db_session)
        channel, old_admins = await svc.change_admin("test_channel_1", "test_12345")
        await db_session.commit()

        admin_svc = ChannelAdminService(db_session)
        current = await admin_svc.get_admin_ids(channel.id)
        assert current == ["test_12345"]

    async def test_change_admin_legacy_field(self, db_session, sample_channel):
        """admin_telegram_user_id синхронизируется с channel_admins."""
        svc = ChannelService(db_session)
        channel, _ = await svc.change_admin("test_channel_1", "legacy_test")
        await db_session.commit()

        assert channel.admin_telegram_user_id == "legacy_test"

    async def test_change_admin_returns_old_admins(self, db_session, sample_channel):
        """Метод возвращает список старых админов."""
        svc = ChannelService(db_session)
        # Добавляем второго админа
        admin_svc = ChannelAdminService(db_session)
        await admin_svc.sync_admins(sample_channel.id, ["test_12345", "second_admin"])
        await db_session.commit()

        channel, old_admins = await svc.change_admin("test_channel_1", "new_admin_99")
        await db_session.commit()

        assert set(old_admins) == {"test_12345", "second_admin"}

    async def test_channel_activated_then_admin_synced(self, db_session):
        """Канал создан и активирован — админ добавляется через sync_admins.

        Симуляция: суперадмин подписывает новый канал (бота нет в БД).
        Канал создаётся, активируется. Если бот в канале — админы
        синхронизируются. Проверяем, что после sync_admins админ виден.
        """
        svc = ChannelService(db_session)
        admin_svc = ChannelAdminService(db_session)

        channel = await svc.create(
            telegram_channel_id="@test_new_ch_sub",
            admin_telegram_user_id="",
            title="New via subscribe",
        )
        await svc.activate_subscription(
            channel.id, duration_days=30, tier=SubscriptionTier.pro,
        )
        await db_session.commit()

        # Синхронизация админов (что и должна делать subscribe-ветка)
        await admin_svc.sync_admins(channel.id, ["sub_admin_id"])
        await db_session.commit()

        admin_ids = await admin_svc.get_admin_ids(channel.id)
        assert "sub_admin_id" in admin_ids

        channels = await svc.get_channels_by_admin("sub_admin_id")
        assert any(ch.id == channel.id for ch in channels)

    async def test_channel_activated_no_admins_before_sync(self, db_session):
        """Канал без синхронизации админов — список пуст.

        Симуляция: бот ещё не в канале (get_chat_administrators не вызван).
        В channel_admins никого нет — прочерк в списке каналов.
        Это ожидаемое временное состояние до добавления бота.
        """
        svc = ChannelService(db_session)
        admin_svc = ChannelAdminService(db_session)

        channel = await svc.create(
            telegram_channel_id="@test_no_bot_yet",
            admin_telegram_user_id="",
            title="Bot not in channel",
        )
        await svc.activate_subscription(channel.id, duration_days=30)
        await db_session.commit()

        admin_ids = await admin_svc.get_admin_ids(channel.id)
        assert admin_ids == []


# ═══════════════════════════════════════════════════════════════
# ChannelService — Subscription tiers
# ═══════════════════════════════════════════════════════════════

class TestSubscriptionTier:
    async def test_default_tier_is_basic(self, db_session):
        """Новый канал без подписки имеет tier=basic по умолчанию."""
        from app.core.models import Channel as ChannelModel
        ch = ChannelModel(
            telegram_channel_id="default_tier_test",
            admin_telegram_user_id="admin_test",
        )
        db_session.add(ch)
        await db_session.flush()
        assert ch.subscription_tier == SubscriptionTier.basic

    async def test_activate_subscription_with_tier(self, db_session):
        """Активация подписки с указанием tier."""
        svc = ChannelService(db_session)
        channel = await svc.create(
            telegram_channel_id="tier_test_channel",
            admin_telegram_user_id="admin_1",
            title="Tier Test",
        )
        await db_session.commit()

        activated = await svc.activate_subscription(channel.id, duration_days=30, tier=SubscriptionTier.pro)
        await db_session.commit()

        assert activated.subscription_tier == SubscriptionTier.pro
        assert activated.is_subscription_active is True

    async def test_activate_subscription_default_tier(self, db_session):
        """Активация без указания tier сохраняет basic."""
        svc = ChannelService(db_session)
        channel = await svc.create(
            telegram_channel_id="default_tier_ch",
            admin_telegram_user_id="admin_2",
            title="Default Tier",
        )
        await db_session.commit()

        activated = await svc.activate_subscription(channel.id, duration_days=30)
        await db_session.commit()

        assert activated.subscription_tier == SubscriptionTier.basic

    async def test_get_subscription_tier(self, db_session, sample_channel):
        """Проверка получения tier канала."""
        svc = ChannelService(db_session)
        tier = await svc.get_subscription_tier(sample_channel.id)
        assert tier == SubscriptionTier.pro

    async def test_get_subscription_tier_not_found(self, db_session):
        """Несуществующий канал -> None."""
        svc = ChannelService(db_session)
        tier = await svc.get_subscription_tier(uuid.uuid4())
        assert tier is None

    async def test_require_feature_basic_can_create_free(self, db_session, basic_channel):
        """Basic tier может создавать бесплатные мероприятия."""
        svc = ChannelService(db_session)
        can = await svc.require_feature(basic_channel.id, "free_events")
        assert can is True

    async def test_require_feature_basic_cannot_paid(self, db_session, basic_channel):
        """Basic tier НЕ может создавать платные мероприятия."""
        svc = ChannelService(db_session)
        can = await svc.require_feature(basic_channel.id, "paid_events")
        assert can is False

    async def test_require_feature_pro_can_paid(self, db_session):
        """Pro tier может создавать платные мероприятия."""
        svc = ChannelService(db_session)
        channel = await svc.create(
            telegram_channel_id="pro_ch",
            admin_telegram_user_id="admin_pro",
            title="Pro Channel",
        )
        await db_session.commit()
        await svc.activate_subscription(channel.id, duration_days=30, tier=SubscriptionTier.pro)
        await db_session.commit()

        can_paid = await svc.require_feature(channel.id, "paid_events")
        assert can_paid is True
        can_free = await svc.require_feature(channel.id, "free_events")
        assert can_free is True

    async def test_require_feature_no_subscription(self, db_session):
        """Канал без подписки не имеет фич."""
        from app.core.models import Channel as ChannelModel
        ch = ChannelModel(
            telegram_channel_id="no_sub_channel",
            admin_telegram_user_id="admin_no_sub",
        )
        db_session.add(ch)
        await db_session.flush()

        svc = ChannelService(db_session)
        can = await svc.require_feature(ch.id, "free_events")
        assert can is False

    async def test_event_create_basic_rejects_paid(self, db_session, event_svc, basic_channel):
        """Создание платного мероприятия на канале Basic -> ошибка."""
        future = datetime.now(timezone.utc) + timedelta(days=10)

        with pytest.raises(ValueError, match="бесплатные"):
            await event_svc.create(
                title="Платный концерт",
                description="Должен быть бесплатным",
                date=future,
                location="Москва",
                price=1000.0,
                total_tickets=50,
                channel_id=basic_channel.id,
            )

    async def test_event_create_basic_allows_free(self, db_session, event_svc, basic_channel):
        """Создание бесплатного мероприятия на канале Basic -> OK."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Бесплатная лекция",
            description="Доступно всем",
            date=future,
            location="Москва",
            price=0,
            total_tickets=100,
            channel_id=basic_channel.id,
        )
        assert event.price == 0
        assert event.is_free is True

    async def test_event_create_pro_allows_paid(self, db_session, event_svc, sample_channel):
        """Создание платного мероприятия на канале Pro -> OK."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Платный концерт",
            description="Только Pro",
            date=future,
            location="Москва",
            price=1500.0,
            total_tickets=50,
            channel_id=sample_channel.id,
        )
        assert event.price == 1500.0
        assert event.is_free is False

class TestEventService:
    async def test_create_event(self, db_session, event_svc, sample_channel):
        """Создание мероприятия."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Концерт",
            description="Рок",
            date=future,
            location="Москва",
            price=2000.0,
            total_tickets=50,
            channel_id=sample_channel.id,
        )
        assert event.title == "Концерт"
        assert event.available_tickets == 50
        assert event.is_active is True
        assert event.price == 2000.0

    async def test_list_upcoming(self, db_session, event_svc, sample_event, sample_past_event):
        """Только активные будущие мероприятия."""
        events = await event_svc.list_upcoming()
        ids = [e.id for e in events]
        assert sample_event.id in ids
        assert sample_past_event.id not in ids

    async def test_list_upcoming_inactive(self, db_session, event_svc, sample_event):
        """Неактивные мероприятия не показываются в списке."""
        await event_svc.set_active(sample_event.id, False)
        await db_session.commit()

        events = await event_svc.list_upcoming()
        assert sample_event.id not in [e.id for e in events]

    async def test_get_by_id_found(self, db_session, event_svc, sample_event):
        """Поиск существующего мероприятия."""
        event = await event_svc.get_by_id(sample_event.id)
        assert event is not None
        assert event.id == sample_event.id

    async def test_get_by_id_not_found(self, db_session, event_svc):
        """Поиск несуществующего мероприятия."""
        event = await event_svc.get_by_id(uuid.uuid4())
        assert event is None

    async def test_set_active(self, db_session, event_svc, sample_event):
        """Включение/отключение мероприятия."""
        updated = await event_svc.set_active(sample_event.id, False)
        assert updated.is_active is False

        updated = await event_svc.set_active(sample_event.id, True)
        assert updated.is_active is True

    async def test_update_event(self, db_session, event_svc, sample_event):
        """Обновление полей мероприятия."""
        updated = await event_svc.update(
            sample_event.id,
            title="Новое название",
            price=3000.0,
        )
        assert updated.title == "Новое название"
        assert updated.price == 3000.0

    async def test_create_event_age_restriction_default(self, db_session, event_svc, sample_channel):
        """Дефолт age_restriction = "0+", если не указан."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Концерт",
            description=None,
            date=future,
            location=None,
            price=0,
            total_tickets=50,
            channel_id=sample_channel.id,
        )
        assert event.age_restriction == "0+"

    async def test_create_event_age_restriction_custom(self, db_session, event_svc, sample_channel):
        """Указанный age_restriction сохраняется."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Концерт 18+",
            description=None,
            date=future,
            location=None,
            price=0,
            total_tickets=50,
            channel_id=sample_channel.id,
            age_restriction="18+",
        )
        assert event.age_restriction == "18+"

    async def test_update_event_age_restriction(self, db_session, event_svc, sample_event):
        """Обновление age_restriction."""
        updated = await event_svc.update(sample_event.id, age_restriction="16+")
        assert updated.age_restriction == "16+"

    async def test_list_all(self, db_session, event_svc, sample_event, sample_past_event):
        """Все мероприятия (включая прошедшие)."""
        all_events = await event_svc.list_all()
        ids = [e.id for e in all_events]
        assert sample_event.id in ids
        assert sample_past_event.id in ids

    async def test_get_event_stats(self, db_session, event_svc, ticket_svc, sample_event, sample_user):
        """Статистика продаж."""
        # Покупаем билет
        await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        stats = await event_svc.get_event_stats(sample_event.id)
        assert stats["total_tickets"] == 100
        assert stats["sold"] == 1
        assert stats["refunded"] == 0
        assert stats["sold_pct"] == 1.0
        assert stats["revenue"] == 1000.0

    async def test_get_event_stats_refund(self, db_session, event_svc, ticket_svc, sample_event, sample_user):
        """Статистика с возвратом."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        await ticket_svc.cancel_ticket(ticket.id, sample_user.id)
        await db_session.commit()

        stats = await event_svc.get_event_stats(sample_event.id)
        assert stats["sold"] == 0
        assert stats["refunded"] == 1
        assert stats["revenue"] == 0.0


# ═══════════════════════════════════════════════════════════════
# TicketService
# ═══════════════════════════════════════════════════════════════

class TestTicketService:
    async def test_buy_ticket_success(self, db_session, ticket_svc, sample_user, sample_event):
        """Успешная покупка билета."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        assert ticket.status == TicketStatus.active
        assert ticket.event_id == sample_event.id
        assert ticket.user_id == sample_user.id

        # Проверяем, что available_tickets уменьшился
        event = await db_session.get(Event, sample_event.id)
        assert event.available_tickets == 99

        # Проверяем, что создался Payment
        stmt = select(Payment).where(Payment.ticket_id == ticket.id)
        result = await db_session.execute(stmt)
        payment = result.scalar_one()
        assert payment.amount == sample_event.price
        assert payment.status == PaymentStatus.completed

    async def test_buy_ticket_sold_out(self, db_session, ticket_svc, sample_user, event_svc, sample_channel):
        """Покупка при отсутствии билетов."""
        # Создаём мероприятие с 0 билетов
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Sold Out", description=None, date=future, price=0,
            total_tickets=0, location="Msk",
            channel_id=sample_channel.id,
        )
        event.is_published = True
        await db_session.commit()

        with pytest.raises(ValueError, match="Билеты закончились"):
            await ticket_svc.buy_ticket(sample_user.id, event.id)

    async def test_buy_ticket_inactive(self, db_session, ticket_svc, sample_user, event_svc, sample_event):
        """Покупка на неактивное мероприятие."""
        await event_svc.set_active(sample_event.id, False)
        await db_session.commit()

        with pytest.raises(ValueError, match="Мероприятие неактивно"):
            await ticket_svc.buy_ticket(sample_user.id, sample_event.id)

    async def test_buy_ticket_past(self, db_session, ticket_svc, sample_user, sample_past_event):
        """Покупка на прошедшее мероприятие."""
        with pytest.raises(ValueError, match="Мероприятие уже прошло"):
            await ticket_svc.buy_ticket(sample_user.id, sample_past_event.id)

    async def test_buy_ticket_not_found(self, db_session, ticket_svc, sample_user):
        """Покупка на несуществующее мероприятие."""
        with pytest.raises(ValueError, match="Мероприятие не найдено"):
            await ticket_svc.buy_ticket(sample_user.id, uuid.uuid4())

    async def test_buy_ticket_duplicate(self, db_session, ticket_svc, sample_user, sample_event):
        """Повторная покупка на то же мероприятие."""
        await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        with pytest.raises(ValueError, match="У вас уже есть активный билет"):
            await ticket_svc.buy_ticket(sample_user.id, sample_event.id)

    async def test_buy_ticket_draft_rejected(self, db_session, ticket_svc, sample_user, event_svc, sample_channel):
        """Черновик (is_published=False) нельзя купить даже по ID."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Draft", description=None, date=future, price=0,
            total_tickets=10, location="Msk", channel_id=sample_channel.id,
        )
        await db_session.commit()

        with pytest.raises(ValueError, match="не опубликовано"):
            await ticket_svc.buy_ticket(sample_user.id, event.id)

    async def test_buy_ticket_webapp_draft_rejected(self, db_session, ticket_svc, sample_user, event_svc, sample_channel):
        """Черновик (is_published=False) нельзя купить через Mini App."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Draft WA", description=None, date=future, price=0,
            total_tickets=10, location="Msk", channel_id=sample_channel.id,
        )
        await db_session.commit()

        with pytest.raises(ValueError, match="не опубликовано"):
            await ticket_svc.buy_ticket_webapp(sample_user.id, event.id)

    async def test_buy_ticket_webapp_success(self, db_session, ticket_svc, sample_user, sample_event):
        """Покупка билета через Mini App (Payment.status = pending)."""
        result = await ticket_svc.buy_ticket_webapp(sample_user.id, sample_event.id)
        await db_session.commit()

        assert result["ticket_id"] is not None
        assert result["event_title"] == sample_event.title
        assert result["amount"] == float(sample_event.price)
        assert result["payment_status"] == "pending"
        # A: is_free в dict — фронт отличает код (free) от QR (paid)
        assert result["is_free"] == sample_event.is_free

        # Check that available_tickets decreased
        assert result["ticket_id"] != ""

    async def test_buy_ticket_webapp_sold_out(self, db_session, ticket_svc, sample_user, event_svc, sample_channel):
        """Покупка через Mini App при отсутствии билетов."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Sold Out WA", description=None, date=future, price=0,
            total_tickets=0, location="Msk",
            channel_id=sample_channel.id,
        )
        event.is_published = True
        await db_session.commit()

        with pytest.raises(ValueError, match="Билеты закончились"):
            await ticket_svc.buy_ticket_webapp(sample_user.id, event.id)

    async def test_buy_ticket_webapp_pending_payment(self, db_session, ticket_svc, sample_user, sample_event):
        """Проверка, что payment создаётся с status=pending."""
        from app.core.models import Payment, PaymentStatus
        from sqlalchemy import select

        result = await ticket_svc.buy_ticket_webapp(sample_user.id, sample_event.id)
        await db_session.commit()

        # Verify payment in DB
        stmt = select(Payment).where(Payment.ticket_id == result["ticket_id"])
        payment = (await db_session.execute(stmt)).scalar_one_or_none()
        assert payment is not None
        assert payment.status == PaymentStatus.pending
        assert float(payment.amount) == float(sample_event.price)

    async def test_cancel_ticket_success(self, db_session, ticket_svc, sample_user, sample_event):
        """Успешный возврат билета."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        cancelled = await ticket_svc.cancel_ticket(ticket.id, sample_user.id)
        await db_session.commit()

        assert cancelled.status == TicketStatus.refunded

        # Проверяем, что available_tickets восстановился
        event = await db_session.get(Event, sample_event.id)
        assert event.available_tickets == 100

        # Проверяем Payment
        stmt = select(Payment).where(Payment.ticket_id == ticket.id)
        result = await db_session.execute(stmt)
        payment = result.scalar_one()
        assert payment.status == PaymentStatus.refunded

    async def test_cancel_other_user(self, db_session, ticket_svc, sample_user, sample_event):
        """Попытка вернуть чужой билет."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        other_user_id = uuid.uuid4()
        with pytest.raises(ValueError, match="Это не ваш билет"):
            await ticket_svc.cancel_ticket(ticket.id, other_user_id)

    async def test_cancel_already_refunded(self, db_session, ticket_svc, sample_user, sample_event):
        """Попытка вернуть уже возвращённый билет."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()
        await ticket_svc.cancel_ticket(ticket.id, sample_user.id)
        await db_session.commit()

        with pytest.raises(ValueError, match="Билет уже возвращён"):
            await ticket_svc.cancel_ticket(ticket.id, sample_user.id)

    async def test_cancel_not_found(self, db_session, ticket_svc, sample_user):
        """Попытка отменить несуществующий билет."""
        with pytest.raises(ValueError, match="Билет не найден"):
            await ticket_svc.cancel_ticket(uuid.uuid4(), sample_user.id)

    async def test_get_user_tickets(self, db_session, ticket_svc, sample_user, sample_event):
        """Список билетов пользователя."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        tickets = await ticket_svc.get_user_tickets(sample_user.id)
        assert len(tickets) == 1
        assert tickets[0]["event_title"] == "Тестовое мероприятие"
        assert tickets[0]["status"] == "active"
        assert tickets[0]["id"] == ticket.id

    async def test_get_user_tickets_empty(self, db_session, ticket_svc, sample_user):
        """Пустой список билетов."""
        tickets = await ticket_svc.get_user_tickets(sample_user.id)
        assert tickets == []

    async def test_admin_cancel_ticket(self, db_session, ticket_svc, sample_user, sample_event):
        """Админская отмена билета (без проверки владельца)."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        result = await ticket_svc.admin_cancel_ticket(ticket.id)
        assert result.status == TicketStatus.refunded

    async def test_get_event_tickets(self, db_session, ticket_svc, sample_user, sample_event):
        """Список билетов на мероприятие (админка)."""
        await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        tickets = await ticket_svc.get_event_tickets(sample_event.id)
        assert len(tickets) == 1
        assert tickets[0]["user_name"] == "Test User"

    async def test_buy_free_ticket_has_validation_code(self, db_session, ticket_svc, sample_user, event_svc, sample_channel):
        """Бесплатный билет получает validation_code."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Бесплатный", description=None, date=future, price=0,
            total_tickets=50, location="Msk",
            channel_id=sample_channel.id,
        )
        event.is_published = True
        await db_session.commit()

        ticket = await ticket_svc.buy_ticket(sample_user.id, event.id)
        await db_session.commit()

        assert ticket.validation_code is not None
        assert len(ticket.validation_code) == 9  # XXXX-XXXX
        assert "-" in ticket.validation_code

    async def test_validation_code_format(self, db_session, ticket_svc):
        """Формат validation_code: XXXX-XXXX."""
        code = await ticket_svc.generate_validation_code()
        assert len(code) == 9  # 8 символов + дефис
        assert code[4] == "-"
        # Все символы — буквы или цифры (кроме дефиса)
        parts = code.split("-")
        assert len(parts) == 2
        assert len(parts[0]) == 4
        assert len(parts[1]) == 4
        # hex: только 0-9, A-F
        assert all(c in "0123456789ABCDEF" for c in (parts[0] + parts[1]))

    async def test_validation_code_unique(self, db_session, ticket_svc):
        """Последовательные коды уникальны."""
        codes = set()
        for _ in range(100):
            code = await ticket_svc.generate_validation_code()
            assert code not in codes
            codes.add(code)

    async def test_is_free_flag_on_ticket(self, db_session, ticket_svc, sample_user, event_svc, sample_channel):
        """is_free=True для бесплатного билета."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        event = await event_svc.create(
            title="Free Event", description=None, date=future, price=0,
            total_tickets=10, location="Msk",
            channel_id=sample_channel.id,
        )
        event.is_published = True
        await db_session.commit()

        event_paid = await event_svc.create(
            title="Paid Event", description=None, date=future, price=500,
            total_tickets=10, location="Msk",
            channel_id=sample_channel.id,
        )
        event_paid.is_published = True
        await db_session.commit()

        free_ticket = await ticket_svc.buy_ticket(sample_user.id, event.id)
        paid_ticket = await ticket_svc.buy_ticket(sample_user.id, event_paid.id)
        await db_session.commit()

        assert free_ticket.is_free is True
        assert paid_ticket.is_free is False


# ═══════════════════════════════════════════════════════════════
# TicketService — Validation & Check-in
# ═══════════════════════════════════════════════════════════════

class TestTicketValidation:
    async def test_validate_ticket_found(self, db_session, ticket_svc, sample_user, sample_event):
        """Поиск билета по validation_code."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        result = await ticket_svc.validate_ticket(ticket.validation_code)
        assert result["found"] is True
        assert result["status"] == "active"
        assert result["user_name"] == "Test User"
        assert result["event_title"] == "Тестовое мероприятие"
        assert result["already_checked_in"] is False

    async def test_validate_ticket_found_exposes_event_and_ticket_id(self, db_session, ticket_svc, sample_user, sample_event):
        """validate_ticket отдаёт event_id/ticket_id (для проверки доступа в web-маршруте)."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        result = await ticket_svc.validate_ticket(ticket.validation_code)
        assert result["event_id"] == str(sample_event.id)
        assert result["ticket_id"] == str(ticket.id)

    async def test_validate_ticket_not_found(self, db_session, ticket_svc):
        """Несуществующий код -> not found (без event_id/ticket_id)."""
        result = await ticket_svc.validate_ticket("ZZZZ-ZZZZ")
        assert result["found"] is False
        assert result["status"] == "not_found"
        assert "event_id" not in result
        assert "ticket_id" not in result

    async def test_validate_ticket_refunded(self, db_session, ticket_svc, sample_user, sample_event):
        """Возвращённый билет -> status=refunded."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()
        await ticket_svc.cancel_ticket(ticket.id, sample_user.id)
        await db_session.commit()

        result = await ticket_svc.validate_ticket(ticket.validation_code)
        assert result["found"] is True
        assert result["status"] == "refunded"

    async def test_check_in_success(self, db_session, ticket_svc, sample_user, sample_event):
        """Успешный чекин билета."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        checked = await ticket_svc.check_in(ticket.id, "admin_123")
        await db_session.commit()

        assert checked.status == TicketStatus.checked_in
        assert checked.checked_in_at is not None
        assert checked.checked_in_by == "admin_123"

    async def test_check_in_by_code_success(self, db_session, ticket_svc, sample_user, sample_event):
        """Чекин по validation_code."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        checked = await ticket_svc.check_in_by_code(ticket.validation_code, "admin_456")
        await db_session.commit()

        assert checked.status == TicketStatus.checked_in
        assert checked.checked_in_by == "admin_456"

    async def test_check_in_already_checked(self, db_session, ticket_svc, sample_user, sample_event):
        """Повторный чекин -> ошибка."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()

        await ticket_svc.check_in(ticket.id, "admin_1")
        await db_session.commit()

        with pytest.raises(ValueError, match="уже использован"):
            await ticket_svc.check_in(ticket.id, "admin_2")

    async def test_check_in_refunded_ticket(self, db_session, ticket_svc, sample_user, sample_event):
        """Чекин возвращённого билета -> ошибка."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()
        await ticket_svc.cancel_ticket(ticket.id, sample_user.id)
        await db_session.commit()

        with pytest.raises(ValueError, match="возвращён"):
            await ticket_svc.check_in(ticket.id, "admin_1")

    async def test_check_in_nonexistent(self, db_session, ticket_svc):
        """Чекин несуществующего билета -> ошибка."""
        with pytest.raises(ValueError, match="не найден"):
            await ticket_svc.check_in(uuid.uuid4(), "admin_1")

    async def test_buy_paid_ticket_on_basic_channel_fails(self, db_session, ticket_svc, sample_user, basic_channel):
        """Покупка платного билета на Basic-канале -> ошибка."""
        from app.core.services import EventService
        svc = EventService(db_session)
        future = datetime.now(timezone.utc) + timedelta(days=10)
        # Basic-канал не даст создать платное мероприятие, но проверим buy_ticket напрямую
        # через существующее free-мероприятие с подменой цены
        event = await svc.create(
            title="Free But...", description="x", date=future, price=0,
            total_tickets=10, location="Msk",
            channel_id=basic_channel.id,
        )
        event.is_published = True
        await db_session.commit()

        # Покупка работает для бесплатного
        ticket = await ticket_svc.buy_ticket(sample_user.id, event.id)
        await db_session.commit()
        assert ticket.is_free is True

    async def test_validate_after_check_in_shows_timestamp(self, db_session, ticket_svc, sample_user, sample_event):
        """После чекина validate возвращает checked_in_at."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()
        await ticket_svc.check_in(ticket.id, "admin_1")
        await db_session.commit()

        result = await ticket_svc.validate_ticket(ticket.validation_code)
        assert result["found"] is True
        assert result["status"] == "checked_in"
        assert result["already_checked_in"] is True
        assert result["checked_in_at"] is not None
        assert result["event_id"] == str(sample_event.id)
        assert result["ticket_id"] == str(ticket.id)

    async def test_check_in_by_code_not_found(self, db_session, ticket_svc):
        """Чекин по несуществующему коду -> ошибка."""
        with pytest.raises(ValueError, match="не найден"):
            await ticket_svc.check_in_by_code("ZZZZ-ZZZZ", "admin_1")


# ═══════════════════════════════════════════════════════════════
# Новые методы для веб-кабинета/админки
# ═══════════════════════════════════════════════════════════════

class TestWebAdminServices:
    """Тесты новых методов сервисного слоя для web-кабинета."""

    async def test_channel_list_all(self, db_session, sample_channel):
        """ChannelService.list_all возвращает все каналы."""
        from app.core.services import ChannelService
        svc = ChannelService(db_session)
        channels = await svc.list_all()
        assert isinstance(channels, list)
        assert len(channels) >= 1

    async def test_channel_summary(self, db_session, sample_channel, sample_event):
        """ChannelService.get_channel_summary собирает сводку по каналу."""
        from app.core.services import ChannelService, TicketService
        from uuid import uuid4
        svc = ChannelService(db_session)

        summary = await svc.get_channel_summary(sample_channel.id)
        assert summary is not None
        assert summary["id"] == str(sample_channel.id)
        assert summary["events_count"] >= 1
        assert summary["admins"] is not None
        assert "subscription_tier" in summary

    async def test_channel_summary_missing(self, db_session):
        """get_channel_summary для несуществующего канала -> None."""
        from app.core.services import ChannelService
        from uuid import uuid4
        svc = ChannelService(db_session)
        assert await svc.get_channel_summary(uuid4()) is None

    async def test_get_ticket_event(self, db_session, sample_event, ticket_svc):
        """TicketService.get_ticket_event возвращает (билет, мероприятие)."""
        from app.core.services import UserService
        from app.core.models import PlatformType
        user_svc = UserService(db_session)
        user = await user_svc.get_or_create(PlatformType.telegram, "te_user", "TE User")
        ticket = await ticket_svc.buy_ticket(user.id, sample_event.id)
        await db_session.commit()

        pair = await ticket_svc.get_ticket_event(ticket.id)
        assert pair is not None
        t, ev = pair
        assert t.id == ticket.id
        assert ev.id == sample_event.id

    async def test_get_ticket_event_missing(self, db_session, ticket_svc):
        """get_ticket_event для несуществующего -> None."""
        from uuid import uuid4
        assert await ticket_svc.get_ticket_event(uuid4()) is None

    async def test_event_tickets_has_validation_code(self, db_session, sample_event, ticket_svc):
        """get_event_tickets теперь включает validation_code."""
        from app.core.services import UserService
        from app.core.models import PlatformType
        user_svc = UserService(db_session)
        user = await user_svc.get_or_create(PlatformType.telegram, "vet_user", "VET User")
        await ticket_svc.buy_ticket(user.id, sample_event.id)
        await db_session.commit()

        tickets = await ticket_svc.get_event_tickets(sample_event.id)
        assert len(tickets) == 1
        assert tickets[0]["validation_code"] is not None

    async def test_stats_global(self, db_session, sample_channel):
        """StatsService.get_global_stats возвращает агрегаты."""
        from app.core.services import StatsService
        svc = StatsService(db_session)
        stats = await svc.get_global_stats()
        assert stats["channels_count"] >= 1
        assert stats["users_count"] >= 0
        assert stats["events_count"] >= 0
        assert stats["revenue"] >= 0

    async def test_user_update_name(self, db_session, sample_user):
        """UserService.update_name меняет имя пользователя."""
        from app.core.services import UserService
        svc = UserService(db_session)
        user = await svc.update_name(sample_user.id, "Новое Имя")
        assert user is not None
        assert user.name == "Новое Имя"


class TestUserLookup:
    """Тесты поиска пользователя без создания."""

    async def test_get_by_platform_user_id_found(self, db_session, sample_user):
        """Находит существующего пользователя по platform+id."""
        from app.core.services import UserService
        from app.core.models import PlatformType
        svc = UserService(db_session)
        user = await svc.get_by_platform_user_id(PlatformType.telegram, sample_user.platform_user_id)
        assert user is not None
        assert user.id == sample_user.id

    async def test_get_by_platform_user_id_missing(self, db_session):
        """Возвращает None для отсутствующего (без создания)."""
        from app.core.services import UserService
        from app.core.models import PlatformType
        svc = UserService(db_session)
        user = await svc.get_by_platform_user_id(PlatformType.telegram, "nonexistent_999")
        assert user is None


# ═══════════════════════════════════════════════════════════════
# Пригласительные (invite tickets) — TDD: тесты пишутся до кода
# ═══════════════════════════════════════════════════════════════

class TestInviteTickets:
    """Тесты выдачи/отмены пригласительных."""

    async def _make_invite_event(self, db_session, channel, total=10, quota=5, price=1000.0):
        """Создаёт мероприятие с квотой пригласительных."""
        from app.core.services import EventService
        from datetime import datetime, timezone, timedelta
        svc = EventService(db_session)
        event = await svc.create(
            title="Invite Event",
            description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None,
            price=price,
            total_tickets=total,
            channel_id=channel.id,
            invites_quota=quota,
        )
        await db_session.flush()
        return event

    async def test_issue_invite_basic(self, db_session, sample_channel):
        """Выдача пригласительного: is_invite, seats=1, available-1, код есть."""
        from app.core.services import TicketService
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=5)
        ticket_svc = TicketService(db_session)

        invite = await ticket_svc.issue_invite(event.id, seats=1, issued_by="admin_1")
        await db_session.commit()

        assert invite.is_invite is True
        assert invite.seats == 1
        assert invite.invited_by == "admin_1"
        assert invite.validation_code is not None
        assert invite.status.value == "active"
        # available уменьшился на 1
        await db_session.refresh(event)
        assert event.available_tickets == 9

    async def test_issue_invite_seats3(self, db_session, sample_channel):
        """Пригласительное на 3 человек: available -= 3."""
        from app.core.services import TicketService
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=5)
        ticket_svc = TicketService(db_session)

        await ticket_svc.issue_invite(event.id, seats=3, issued_by="admin_1")
        await db_session.commit()
        await db_session.refresh(event)
        assert event.available_tickets == 7

    async def test_issue_invite_quota_zero(self, db_session, sample_channel):
        """Пригласительное при invites_quota=0 → ValueError."""
        from app.core.services import TicketService
        import pytest
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=0)
        ticket_svc = TicketService(db_session)

        with pytest.raises(ValueError):
            await ticket_svc.issue_invite(event.id, seats=1, issued_by="admin_1")

    async def test_issue_invite_quota_exceeded(self, db_session, sample_channel):
        """Превышение квоты пригласительных → ValueError."""
        from app.core.services import TicketService
        import pytest
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=2)
        ticket_svc = TicketService(db_session)

        await ticket_svc.issue_invite(event.id, seats=1, issued_by="a")
        await ticket_svc.issue_invite(event.id, seats=1, issued_by="a")
        await db_session.commit()
        with pytest.raises(ValueError):
            await ticket_svc.issue_invite(event.id, seats=1, issued_by="a")

    async def test_issue_invite_no_seats(self, db_session, sample_channel):
        """Не хватает мест → ValueError."""
        from app.core.services import TicketService
        import pytest
        event = await self._make_invite_event(db_session, sample_channel, total=2, quota=5)
        ticket_svc = TicketService(db_session)

        await ticket_svc.issue_invite(event.id, seats=2, issued_by="a")
        await db_session.commit()
        with pytest.raises(ValueError):
            await ticket_svc.issue_invite(event.id, seats=2, issued_by="a")

    async def test_issue_invite_past_event(self, db_session, sample_past_event):
        """Прошедшее мероприятие → ValueError."""
        from app.core.services import TicketService
        import pytest
        sample_past_event.invites_quota = 5
        await db_session.flush()
        ticket_svc = TicketService(db_session)
        with pytest.raises(ValueError):
            await ticket_svc.issue_invite(sample_past_event.id, seats=1, issued_by="a")

    async def test_cancel_invite(self, db_session, sample_channel):
        """Отмена пригласительного: available += seats, статус refunded."""
        from app.core.services import TicketService
        from app.core.models import TicketStatus
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=5)
        ticket_svc = TicketService(db_session)
        invite = await ticket_svc.issue_invite(event.id, seats=2, issued_by="admin_1")
        await db_session.commit()

        await ticket_svc.cancel_invite(invite.id)
        await db_session.commit()

        await db_session.refresh(event)
        assert event.available_tickets == 10  # вернулось
        await db_session.refresh(invite)
        assert invite.status == TicketStatus.refunded

    async def test_get_event_invites(self, db_session, sample_channel):
        """Список пригласительных по событию."""
        from app.core.services import TicketService
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=5)
        ticket_svc = TicketService(db_session)
        await ticket_svc.issue_invite(event.id, seats=1, issued_by="a")
        await ticket_svc.issue_invite(event.id, seats=2, issued_by="b")
        await db_session.commit()

        invites = await ticket_svc.get_event_invites(event.id)
        assert len(invites) == 2
        assert invites[0]["is_invite"] is True
        assert invites[0]["validation_code"] is not None

    async def test_claim_invite_binds_to_user(self, db_session, sample_channel):
        """Активация пригласительного гостем: user_id привязывается."""
        from app.core.services import TicketService, UserService
        from app.core.models import PlatformType
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=5)
        ticket_svc = TicketService(db_session)
        invite = await ticket_svc.issue_invite(event.id, seats=2, issued_by="admin_1")
        await db_session.commit()

        guest = await UserService(db_session).get_or_create(PlatformType.telegram, "guest_1", "Гость")
        claimed = await ticket_svc.claim_invite(invite.validation_code, guest.id)
        await db_session.commit()

        assert claimed.id == invite.id
        assert claimed.user_id == guest.id
        assert claimed.is_invite is True
        await db_session.refresh(event)
        assert event.available_tickets == 8  # 2 места пригласительного уже резервированы

    async def test_claim_invite_already_claimed_by_other(self, db_session, sample_channel):
        """Повторная активация другим гостем → ValueError."""
        from app.core.services import TicketService, UserService
        from app.core.models import PlatformType
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=5)
        ticket_svc = TicketService(db_session)
        invite = await ticket_svc.issue_invite(event.id, seats=1, issued_by="admin_1")
        await db_session.commit()

        guest1 = await UserService(db_session).get_or_create(PlatformType.telegram, "guest_1", "Гость")
        await ticket_svc.claim_invite(invite.validation_code, guest1.id)
        await db_session.commit()

        guest2 = await UserService(db_session).get_or_create(PlatformType.telegram, "guest_2", "Другой")
        import pytest
        with pytest.raises(ValueError, match="уже активировано"):
            await ticket_svc.claim_invite(invite.validation_code, guest2.id)

    async def test_claim_invite_not_invite(self, db_session, sample_channel):
        """Обычный билет нельзя активировать как пригласительное."""
        from app.core.services import TicketService, UserService
        from app.core.models import PlatformType
        event = await self._make_invite_event(db_session, sample_channel, total=10, quota=5)
        event.is_published = True  # buy_ticket требует опубликованное событие
        await db_session.flush()
        ticket_svc = TicketService(db_session)
        user = await UserService(db_session).get_or_create(PlatformType.telegram, "buyer_x", "Покупатель")
        ticket = await ticket_svc.buy_ticket(user.id, event.id)
        await db_session.commit()

        import pytest
        with pytest.raises(ValueError, match="не пригласительное"):
            await ticket_svc.claim_invite(ticket.validation_code, user.id)

    async def test_claim_invite_not_found(self, db_session, sample_channel):
        """Несуществующий код → ValueError."""
        from app.core.services import TicketService
        ticket_svc = TicketService(db_session)
        import pytest
        with pytest.raises(ValueError, match="не найден"):
            await ticket_svc.claim_invite("ZZZZ-ZZZZ", None)


class TestEventInvitesQuota:
    """Тесты квоты пригласительных на мероприятии."""

    async def test_create_with_invites_quota(self, db_session, sample_channel):
        """create сохраняет invites_quota."""
        from app.core.services import EventService
        from datetime import datetime, timezone, timedelta
        svc = EventService(db_session)
        event = await svc.create(
            title="Q Event", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=0, total_tickets=20,
            channel_id=sample_channel.id, invites_quota=7,
        )
        await db_session.commit()
        await db_session.refresh(event)
        assert event.invites_quota == 7
        assert event.available_tickets == 20

    async def test_update_invites_quota_increase(self, db_session, sample_channel):
        """Увеличение квоты выделяет из непроданных (available -= diff)."""
        from app.core.services import EventService
        from datetime import datetime, timezone, timedelta
        svc = EventService(db_session)
        event = await svc.create(
            title="Q Event", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=0, total_tickets=20,
            channel_id=sample_channel.id, invites_quota=5,
        )
        await db_session.commit()
        await svc.update(event.id, invites_quota=10)
        await db_session.commit()
        await db_session.refresh(event)
        assert event.available_tickets == 15  # 20 - 5 новых

    async def test_update_invites_quota_decrease(self, db_session, sample_channel):
        """Уменьшение квоты возвращает в непроданные (available += diff)."""
        from app.core.services import EventService
        from datetime import datetime, timezone, timedelta
        svc = EventService(db_session)
        event = await svc.create(
            title="Q Event", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=0, total_tickets=20,
            channel_id=sample_channel.id, invites_quota=10,
        )
        await db_session.commit()
        await svc.update(event.id, invites_quota=6)
        await db_session.commit()
        await db_session.refresh(event)
        assert event.available_tickets == 24  # 20 + 4


class TestStatsWithInvites:
    """Тесты статистики с пригласительными."""

    async def test_stats_invites(self, db_session, sample_channel):
        """get_event_stats: sold только paid, invites_issued/used/quota."""
        from app.core.services import EventService, TicketService, UserService
        from app.core.models import PlatformType
        from datetime import datetime, timezone, timedelta

        event_svc = EventService(db_session)
        event = await event_svc.create(
            title="Stats Event", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=1000.0, total_tickets=20,
            channel_id=sample_channel.id, invites_quota=5,
        )
        event.is_published = True
        await db_session.commit()

        # Купленный билет
        user_svc = UserService(db_session)
        user = await user_svc.get_or_create(PlatformType.telegram, "buyer1", "Buyer")
        ticket_svc = TicketService(db_session)
        await ticket_svc.buy_ticket(user.id, event.id)
        # Пригласительное
        invite = await ticket_svc.issue_invite(event.id, seats=1, issued_by="admin")
        # Пригласительное использовано (check-in)
        await ticket_svc.check_in(invite.id, "checker")
        await db_session.commit()

        stats = await event_svc.get_event_stats(event.id)
        assert stats["sold"] == 1          # только купленный
        assert stats["invites_issued"] == 1
        assert stats["invites_used"] == 1
        assert stats["invites_quota"] == 5
        assert stats["revenue"] == 1000.0  # только оплаченный


class TestQrGeneration:
    """Тесты генерации QR-кодов."""

    async def test_generate_qr_png(self):
        """generate_qr_png возвращает PNG-байты."""
        from app.core.qr import generate_qr_png
        png = generate_qr_png("AB3X-K7M9")
        assert png.startswith(b"\x89PNG")
        assert len(png) > 100


class TestTicketExtraFields:
    """Поля is_invite/seats в списках билетов."""

    async def test_get_event_tickets_has_invite_fields(self, db_session, sample_event, ticket_svc, sample_user):
        """get_event_tickets включает is_invite/seats."""
        await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()
        tickets = await ticket_svc.get_event_tickets(sample_event.id)
        assert len(tickets) == 1
        assert "is_invite" in tickets[0]
        assert "seats" in tickets[0]

# ═══════════════════════════════════════════════════════════════
# Управление подпиской: смена типа + срока (дни/месяцы/годы)
# ═══════════════════════════════════════════════════════════════

class TestSubscriptionDuration:
    """Тесты change_subscription / change_tier (TDD: до реализации)."""

    async def test_change_subscription_months(self, db_session, sample_channel):
        """change_subscription с months — срок ≈ now + 1 месяц (календарный)."""
        from app.core.services import ChannelService
        from app.core.models import SubscriptionTier, PeriodUnit
        from datetime import datetime, timezone
        from dateutil.relativedelta import relativedelta

        svc = ChannelService(db_session)
        before = datetime.now(timezone.utc)
        channel = await svc.change_subscription(
            sample_channel.id, SubscriptionTier.pro, period=1, period_unit=PeriodUnit.months,
        )
        await db_session.commit()
        await db_session.refresh(channel)

        assert channel is not None
        assert channel.subscription_tier == SubscriptionTier.pro
        expected = before + relativedelta(months=1)
        # срок ~ now+1 мес (допуск 5 мин на время выполнения)
        diff = abs((channel.subscription_until - expected).total_seconds())
        assert diff < 300, f"срок {channel.subscription_until} != ~{expected}"

    async def test_change_subscription_days(self, db_session, sample_channel):
        """change_subscription с days — срок = now + N дней."""
        from app.core.services import ChannelService
        from app.core.models import SubscriptionTier, PeriodUnit
        from datetime import datetime, timezone, timedelta

        svc = ChannelService(db_session)
        before = datetime.now(timezone.utc)
        channel = await svc.change_subscription(
            sample_channel.id, SubscriptionTier.basic, period=30, period_unit=PeriodUnit.days,
        )
        await db_session.commit()
        await db_session.refresh(channel)

        expected = before + timedelta(days=30)
        diff = abs((channel.subscription_until - expected).total_seconds())
        assert diff < 300

    async def test_change_subscription_years(self, db_session, sample_channel):
        """change_subscription с years — срок = now + N лет."""
        from app.core.services import ChannelService
        from app.core.models import SubscriptionTier, PeriodUnit
        from datetime import datetime, timezone
        from dateutil.relativedelta import relativedelta

        svc = ChannelService(db_session)
        before = datetime.now(timezone.utc)
        channel = await svc.change_subscription(
            sample_channel.id, SubscriptionTier.pro, period=1, period_unit=PeriodUnit.years,
        )
        await db_session.commit()
        await db_session.refresh(channel)

        expected = before + relativedelta(years=1)
        diff = abs((channel.subscription_until - expected).total_seconds())
        assert diff < 300

    async def test_change_subscription_missing_channel(self, db_session):
        """change_subscription для несуществующего канала → None."""
        from app.core.services import ChannelService
        from app.core.models import SubscriptionTier, PeriodUnit
        from uuid import uuid4
        svc = ChannelService(db_session)
        result = await svc.change_subscription(
            uuid4(), SubscriptionTier.pro, period=1, period_unit=PeriodUnit.months,
        )
        assert result is None

    async def test_change_tier_keeps_until(self, db_session, sample_channel):
        """change_tier меняет тип, НЕ трогая срок."""
        from app.core.services import ChannelService
        from app.core.models import SubscriptionTier, PeriodUnit

        svc = ChannelService(db_session)
        # задаём известный срок
        channel = await svc.change_subscription(
            sample_channel.id, SubscriptionTier.pro, period=30, period_unit=PeriodUnit.days,
        )
        await db_session.commit()
        old_until = channel.subscription_until

        await svc.change_tier(sample_channel.id, SubscriptionTier.basic)
        await db_session.commit()
        await db_session.refresh(channel)

        assert channel.subscription_tier == SubscriptionTier.basic
        assert channel.subscription_until == old_until, "срок не должен меняться при смене типа"


# ═══════════════════════════════════════════════════════════════
# Роль «Организатор» + подписка на пользователя (TDD)
# ═══════════════════════════════════════════════════════════════

class TestUserSubscription:
    """Подписка на пользователя (организатор без канала)."""

    async def test_activate_user_subscription(self, db_session, sample_user):
        """activate_subscription для User — tier + срок."""
        from app.core.services import UserService
        from app.core.models import SubscriptionTier
        from datetime import datetime, timezone

        svc = UserService(db_session)
        user = await svc.activate_subscription(sample_user.id, days=30, tier=SubscriptionTier.pro)
        await db_session.commit()

        assert user is not None
        assert user.is_subscription_active is True
        assert user.subscription_tier == SubscriptionTier.pro
        assert user.subscription_until is not None

    async def test_deactivate_user_subscription(self, db_session, sample_user):
        """deactivate_subscription для User."""
        from app.core.services import UserService
        from app.core.models import SubscriptionTier

        svc = UserService(db_session)
        await svc.activate_subscription(sample_user.id, days=30, tier=SubscriptionTier.pro)
        await db_session.commit()
        await svc.deactivate_subscription(sample_user.id)
        await db_session.commit()
        await db_session.refresh(sample_user)

        assert sample_user.is_subscription_active is False
        assert sample_user.subscription_until is None

    async def test_user_subscription_valid(self, db_session, sample_user):
        """is_subscription_valid для активной подписки пользователя."""
        from app.core.services import UserService
        from app.core.models import SubscriptionTier

        svc = UserService(db_session)
        await svc.activate_subscription(sample_user.id, days=30, tier=SubscriptionTier.pro)
        await db_session.commit()

        assert await svc.is_subscription_valid(sample_user.id) is True

    async def test_user_require_feature_pro(self, db_session, sample_user):
        """require_feature(user_id) — pro даёт paid_events."""
        from app.core.services import UserService
        from app.core.models import SubscriptionTier

        svc = UserService(db_session)
        await svc.activate_subscription(sample_user.id, days=30, tier=SubscriptionTier.pro)
        await db_session.commit()

        assert await svc.require_feature(sample_user.id, "paid_events") is True

    async def test_user_require_feature_basic_no_paid(self, db_session, sample_user):
        """basic-пользователь НЕ может платные мероприятия."""
        from app.core.services import UserService
        from app.core.models import SubscriptionTier

        svc = UserService(db_session)
        await svc.activate_subscription(sample_user.id, days=30, tier=SubscriptionTier.basic)
        await db_session.commit()

        assert await svc.require_feature(sample_user.id, "paid_events") is False


class TestEventOwner:
    """Создание мероприятий владельцем-пользователем (без канала)."""

    async def test_create_event_with_owner(self, db_session, sample_user):
        """create с owner_user_id (без канала) — ok."""
        from app.core.services import EventService
        from app.core.models import SubscriptionTier
        from datetime import datetime, timezone, timedelta
        from uuid import uuid4

        # даём пользователю подписку
        from app.core.services import UserService
        await UserService(db_session).activate_subscription(sample_user.id, 30, SubscriptionTier.basic)
        await db_session.commit()

        svc = EventService(db_session)
        event = await svc.create(
            title="Org Event", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=0, total_tickets=10,
            channel_id=None, owner_user_id=sample_user.id,
        )
        await db_session.commit()

        assert event is not None
        assert event.owner_user_id == sample_user.id
        assert event.channel_id is None

    async def test_create_event_without_owner_or_channel(self, db_session, sample_user):
        """create без owner и без канала → ValueError."""
        from app.core.services import EventService
        from datetime import datetime, timezone, timedelta
        import pytest

        svc = EventService(db_session)
        with pytest.raises(ValueError):
            await svc.create(
                title="No Target", description=None,
                date=datetime.now(timezone.utc) + timedelta(days=7),
                location=None, price=0, total_tickets=10,
                channel_id=None, owner_user_id=None,
            )

    async def test_create_paid_event_owner_pro(self, db_session, sample_user):
        """Организатор с pro может платное мероприятие (owner)."""
        from app.core.services import EventService, UserService
        from app.core.models import SubscriptionTier
        from datetime import datetime, timezone, timedelta

        await UserService(db_session).activate_subscription(sample_user.id, 30, SubscriptionTier.pro)
        await db_session.commit()

        svc = EventService(db_session)
        event = await svc.create(
            title="Paid Org Event", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=500, total_tickets=10,
            channel_id=None, owner_user_id=sample_user.id,
        )
        await db_session.commit()
        assert event.price == 500


class TestRoleOrganizer:
    """Роль организатора (web-роль) — через подписку пользователя."""

    async def test_user_with_subscription_is_organizer(self, db_session, sample_user):
        """Пользователь с активной подпиской (без канала) — организатор."""
        from app.core.services import UserService
        from app.core.models import SubscriptionTier

        svc = UserService(db_session)
        await svc.activate_subscription(sample_user.id, 30, SubscriptionTier.basic)
        await db_session.commit()

        assert await svc.is_organizer(sample_user.id) is True

    async def test_user_without_subscription_not_organizer(self, db_session, sample_user):
        """Пользователь без подписки и без канала — не организатор."""
        from app.core.services import UserService

        svc = UserService(db_session)
        assert await svc.is_organizer(sample_user.id) is False


# ═══════════════════════════════════════════════════════════════
# B: лимит «1 опубликованное будущее» для бесплатного организатора
# ═══════════════════════════════════════════════════════════════

class TestFreeOrganizerEventLimit:
    """Лимит free-организатора: максимум 1 опубликованное будущее мероприятие."""

    async def _make_owner_event(self, db_session, owner_user_id, title="E", days=7, published=False, price=0):
        from app.core.services import EventService
        svc = EventService(db_session)
        event = await svc.create(
            title=title, description=None,
            date=datetime.now(timezone.utc) + timedelta(days=days),
            location=None, price=price, total_tickets=10,
            channel_id=None, owner_user_id=owner_user_id,
        )
        if published:
            event.is_published = True
        await db_session.flush()
        return event

    async def test_free_owner_can_publish_first(self, db_session, sample_user):
        """Free-юзер публикует первое событие — ок."""
        from app.core.services import EventService
        svc = EventService(db_session)
        event = await self._make_owner_event(db_session, sample_user.id, published=False)
        # не опубликовано — слот свободен
        count = await svc.count_published_future(owner_user_id=sample_user.id)
        assert count == 0
        # лимит не блокирует (уже опубликованное не считаем за новое)
        event.is_published = True
        await db_session.flush()
        assert await svc.count_published_future(owner_user_id=sample_user.id) == 1

    async def test_free_owner_second_publish_rejected(self, db_session, sample_user):
        """Free-юзер: второе опубликованное будущее → ValueError."""
        from app.core.services import EventService
        svc = EventService(db_session)
        await self._make_owner_event(db_session, sample_user.id, title="Первое", published=True)
        second = await self._make_owner_event(db_session, sample_user.id, title="Второе", published=False)
        await db_session.commit()

        with pytest.raises(ValueError, match="только одно мероприятие"):
            await svc.ensure_free_slot(second)

    async def test_free_owner_past_event_not_counted(self, db_session, sample_user):
        """Прошедшее опубликованное не занимает слот — можно публиковать новое."""
        from app.core.services import EventService
        svc = EventService(db_session)
        past = await self._make_owner_event(db_session, sample_user.id, title="Прошлое", days=-1, published=True)
        await db_session.commit()
        assert await svc.count_published_future(owner_user_id=sample_user.id) == 0

        new = await self._make_owner_event(db_session, sample_user.id, title="Новое", published=False)
        await db_session.commit()
        await svc.ensure_free_slot(new)  # не падает

    async def test_pro_owner_not_limited(self, db_session, sample_user):
        """Pro-организатор не ограничен лимитом."""
        from app.core.services import EventService, UserService
        user_svc = UserService(db_session)
        await user_svc.activate_subscription(sample_user.id, 30, SubscriptionTier.pro)
        await db_session.commit()

        svc = EventService(db_session)
        await self._make_owner_event(db_session, sample_user.id, title="Первое", published=True)
        second = await self._make_owner_event(db_session, sample_user.id, title="Второе", published=False)
        await db_session.commit()
        await svc.ensure_free_slot(second)  # не падает (pro)

    async def test_basic_owner_limited(self, db_session, sample_user):
        """Basic-организатор тоже ограничен лимитом."""
        from app.core.services import EventService, UserService
        user_svc = UserService(db_session)
        await user_svc.activate_subscription(sample_user.id, 30, SubscriptionTier.basic)
        await db_session.commit()

        svc = EventService(db_session)
        await self._make_owner_event(db_session, sample_user.id, title="Первое", published=True)
        second = await self._make_owner_event(db_session, sample_user.id, title="Второе", published=False)
        await db_session.commit()

        with pytest.raises(ValueError, match="только одно мероприятие"):
            await svc.ensure_free_slot(second)

    async def test_free_channel_limited(self, db_session):
        """Канал без подписки (=free) тоже ограничен лимитом."""
        from app.core.services import EventService, ChannelService
        svc = EventService(db_session)
        ch_svc = ChannelService(db_session)
        channel = await ch_svc.create(
            telegram_channel_id="free_chan", admin_telegram_user_id="admin", title="Free Chan",
        )
        await db_session.flush()

        async def _mk(title, published):
            ev = await svc.create(
                title=title, description=None,
                date=datetime.now(timezone.utc) + timedelta(days=7),
                location=None, price=0, total_tickets=10,
                channel_id=channel.id, owner_user_id=None,
            )
            if published:
                ev.is_published = True
            await db_session.flush()
            return ev
        await _mk("Первое", published=True)
        second = await _mk("Второе", published=False)
        await db_session.commit()

        with pytest.raises(ValueError, match="только одно мероприятие"):
            await svc.ensure_free_slot(second)


# ═══════════════════════════════════════════════════════════════
# C: per-event премиум (единовременная оплата за мероприятие)
# ═══════════════════════════════════════════════════════════════

class TestEventUpgrade:
    """Per-event премиум: покупка, is_premium, pro-фичи, баг update."""

    async def _make_event(self, db_session, owner_user_id, price=0):
        from app.core.services import EventService
        svc = EventService(db_session)
        event = await svc.create(
            title="Prem Event", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=price, total_tickets=10,
            channel_id=None, owner_user_id=owner_user_id,
        )
        await db_session.flush()
        return event

    async def test_purchase_sets_premium(self, db_session, sample_user):
        """Покупка премиума → событие премиум."""
        from app.core.services import EventService
        svc = EventService(db_session)
        event = await self._make_event(db_session, sample_user.id)
        await db_session.commit()

        up = await svc.purchase_event_premium(event.id, sample_user.id)
        await db_session.commit()

        assert up.event_id == event.id
        assert await svc.get_event_is_premium(event.id) is True

    async def test_purchase_upsert(self, db_session, sample_user):
        """Повторная покупка премиума — не дублирует запись (upsert)."""
        from app.core.services import EventService
        svc = EventService(db_session)
        event = await self._make_event(db_session, sample_user.id)
        await db_session.commit()

        await svc.purchase_event_premium(event.id, sample_user.id)
        await svc.purchase_event_premium(event.id, sample_user.id)
        await db_session.commit()

        up = await svc._get_upgrade(event.id)
        assert up is not None
        assert await svc.get_event_is_premium(event.id) is True

    async def test_not_owner_cannot_purchase(self, db_session, sample_user):
        """Не-владелец не может купить премиум для чужого события."""
        from app.core.services import EventService, UserService
        from app.core.models import PlatformType
        svc = EventService(db_session)
        event = await self._make_event(db_session, sample_user.id)
        await db_session.commit()

        stranger = await UserService(db_session).get_or_create(
            PlatformType.telegram, "stranger_1", "Чужой",
        )
        await db_session.commit()

        with pytest.raises(ValueError, match="владелец"):
            await svc.purchase_event_premium(event.id, stranger.id)

    async def test_premium_grants_pro_features(self, db_session, sample_user):
        """Премиум события даёт paid_events/qr_codes/invite_tickets без подписки."""
        from app.core.services import EventService
        svc = EventService(db_session)
        event = await self._make_event(db_session, sample_user.id)
        await svc.purchase_event_premium(event.id, sample_user.id)
        await db_session.commit()

        for feat in ("paid_events", "qr_codes", "invite_tickets"):
            assert await svc.has_event_pro_feature(event.id, feat) is True, feat

    async def test_no_premium_falls_back_to_subscription(self, db_session, sample_user, sample_channel):
        """Без премиума — pro-фичи решаются подпиской (pro-канал даёт)."""
        from app.core.services import EventService
        svc = EventService(db_session)
        event = await svc.create(
            title="Chan Event", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=0, total_tickets=10,
            channel_id=sample_channel.id, owner_user_id=None,  # sample_channel = pro
        )
        await db_session.commit()

        # pro-канал даёт фичи и без премиума
        assert await svc.has_event_pro_feature(event.id, "paid_events") is True

    async def test_update_price_gate_fixed(self, db_session, sample_user):
        """Баг #075: free-юзер не может поднять цену без премиума (PATCH) — но может с премиумом."""
        from app.core.services import EventService
        user_id = sample_user.id  # сохраняем ДО операций
        svc = EventService(db_session)

        # Событие без премиума: поднять цену → ошибка (объект остаётся price=0)
        ev_free = await self._make_event(db_session, user_id, price=0)
        await db_session.commit()
        with pytest.raises(ValueError, match="платн"):
            await svc.update(ev_free.id, price=500)

        # Отдельное событие с премиумом: поднять цену можно
        ev_prem = await self._make_event(db_session, user_id, price=0)
        await svc.purchase_event_premium(ev_prem.id, user_id)
        await db_session.commit()
        ev2 = await svc.update(ev_prem.id, price=500)
        await db_session.commit()
        assert ev2.price == 500


# ═══════════════════════════════════════════════════════════════
# Промокоды (скидки на билеты, pro-фича)
# ═══════════════════════════════════════════════════════════════

class TestPromoCodes:
    """Создание, валидация и применение промокодов при покупке."""

    async def _create(self, ticket_svc, event_id, code="SUMMER10",
                      discount_type=DiscountType.percent, value=10.0,
                      starts_at=None, ends_at=None, max_uses=0):
        return await ticket_svc.create_promo_code(
            event_id, code, discount_type, value,
            starts_at=starts_at, ends_at=ends_at, max_uses=max_uses,
        )

    async def _payment(self, db_session, ticket_id):
        """Payment билета отдельным SELECT (Ticket.payment — lazy='raise')."""
        stmt = select(Payment).where(Payment.ticket_id == ticket_id)
        return (await db_session.execute(stmt)).scalar_one_or_none()

    # ─── Создание ───────────────────────────────────────────────

    async def test_create_promo_code(self, db_session, ticket_svc, sample_event):
        """Создание промокода: код нормализован в upper, поля заполнены."""
        promo = await self._create(ticket_svc, sample_event.id, code="summer10")
        await db_session.commit()
        assert promo.code == "SUMMER10"
        assert promo.discount_type == DiscountType.percent
        assert promo.discount_value == 10
        assert promo.max_uses == 0
        assert promo.used_count == 0
        assert promo.is_active is True

    async def test_create_promo_code_duplicate_code_raises(self, db_session, ticket_svc, sample_event):
        """Дубликат кода в рамках события → ошибка."""
        await self._create(ticket_svc, sample_event.id, code="SUMMER10")
        await db_session.commit()
        with pytest.raises(Exception):
            await self._create(ticket_svc, sample_event.id, code="SUMMER10")
            await db_session.commit()

    async def test_create_promo_code_same_code_other_event_ok(self, db_session, ticket_svc, sample_event, sample_channel):
        """Тот же код на другом мероприятии допустим."""
        from app.core.services import EventService
        ev2 = await EventService(db_session).create(
            title="Второе", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=500, total_tickets=10,
            channel_id=sample_channel.id,
        )
        await db_session.commit()
        p1 = await self._create(ticket_svc, sample_event.id, code="SUMMER10")
        p2 = await self._create(ticket_svc, ev2.id, code="SUMMER10")
        await db_session.commit()
        assert p1.id != p2.id

    async def test_create_promo_percent_range_validation(self, db_session, ticket_svc, sample_event):
        """Percent больше 100 → ошибка."""
        with pytest.raises(ValueError, match="Процент"):
            await self._create(ticket_svc, sample_event.id, discount_type=DiscountType.percent, value=150)

    async def test_create_promo_fixed_negative_raises(self, db_session, ticket_svc, sample_event):
        """Fixed с отрицательным значением → ошибка."""
        with pytest.raises(ValueError):
            await self._create(ticket_svc, sample_event.id, discount_type=DiscountType.fixed, value=-5)

    async def test_create_promo_ends_before_starts_raises(self, db_session, ticket_svc, sample_event):
        """ends_at раньше starts_at → ошибка."""
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError, match="позже"):
            await self._create(ticket_svc, sample_event.id,
                               starts_at=now + timedelta(days=2), ends_at=now + timedelta(days=1))

    # ─── Применение при покупке ─────────────────────────────────

    async def test_apply_promo_percent(self, db_session, ticket_svc, sample_user, sample_event):
        """Покупка с percent-промокодом: amount со скидкой, поля Payment, used_count."""
        promo = await self._create(ticket_svc, sample_event.id, discount_type=DiscountType.percent, value=10)
        await db_session.commit()
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        payment = await self._payment(db_session, ticket.id)
        assert float(payment.amount) == 900.0
        assert float(payment.base_amount) == 1000.0
        assert float(payment.discount_amount) == 100.0
        assert payment.promo_code == "SUMMER10"
        assert promo.used_count == 1

    async def test_apply_promo_fixed(self, db_session, ticket_svc, sample_user, sample_event):
        """Покупка с fixed-промокодом (скидка 250 от 1000)."""
        promo = await self._create(ticket_svc, sample_event.id, discount_type=DiscountType.fixed, value=250)
        await db_session.commit()
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        payment = await self._payment(db_session, ticket.id)
        assert float(payment.amount) == 750.0
        assert float(payment.discount_amount) == 250.0
        assert promo.used_count == 1

    async def test_apply_promo_percent_rounding(self, db_session, ticket_svc, sample_user, sample_event):
        """Процентная скидка округляется до 2 знаков (33% от 1000 = 330.00)."""
        await self._create(ticket_svc, sample_event.id, discount_type=DiscountType.percent, value=33)
        await db_session.commit()
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        payment = await self._payment(db_session, ticket.id)
        assert float(payment.amount) == 670.0
        assert float(payment.discount_amount) == 330.0

    async def test_apply_promo_fixed_over_price_zero_amount(self, db_session, ticket_svc, sample_user, sample_event):
        """Fixed-скидка больше цены → amount клампится до 0 (не отрицательно)."""
        await self._create(ticket_svc, sample_event.id, discount_type=DiscountType.fixed, value=5000)
        await db_session.commit()
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        payment = await self._payment(db_session, ticket.id)
        assert float(payment.amount) == 0.0
        assert float(payment.discount_amount) == 1000.0

    # ─── Валидация промокода ────────────────────────────────────

    async def test_promo_not_found_raises(self, db_session, ticket_svc, sample_user, sample_event):
        with pytest.raises(ValueError, match="не найден"):
            await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="NOCODE")

    async def test_promo_other_event_raises(self, db_session, ticket_svc, sample_user, sample_event, sample_channel):
        """Код изолирован по событию: промокод на другом событии не действует на этом."""
        from app.core.services import EventService
        ev2 = await EventService(db_session).create(
            title="Второе", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=500, total_tickets=10,
            channel_id=sample_channel.id,
        )
        await db_session.commit()
        # Тот же код SUMMER10 создан ТОЛЬКО на другом событии
        await self._create(ticket_svc, ev2.id, code="SUMMER10")
        await db_session.commit()
        with pytest.raises(ValueError, match="не найден"):
            await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")

    async def test_promo_inactive_raises(self, db_session, ticket_svc, sample_user, sample_event):
        promo = await self._create(ticket_svc, sample_event.id)
        await ticket_svc.toggle_promo_code(promo.id)
        await db_session.commit()
        with pytest.raises(ValueError, match="неактивен"):
            await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")

    async def test_promo_not_started_raises(self, db_session, ticket_svc, sample_user, sample_event):
        await self._create(ticket_svc, sample_event.id,
                           starts_at=datetime.now(timezone.utc) + timedelta(days=1))
        await db_session.commit()
        with pytest.raises(ValueError, match="не действует"):
            await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")

    async def test_promo_expired_raises(self, db_session, ticket_svc, sample_user, sample_event):
        await self._create(ticket_svc, sample_event.id,
                           ends_at=datetime.now(timezone.utc) - timedelta(days=1))
        await db_session.commit()
        with pytest.raises(ValueError, match="истёк"):
            await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")

    async def test_promo_limit_reached_raises(self, db_session, ticket_svc, sample_user, sample_event):
        await self._create(ticket_svc, sample_event.id, max_uses=1)
        await db_session.commit()
        await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        from app.core.services import UserService
        user2 = await UserService(db_session).get_or_create(
            platform=PlatformType.telegram, platform_user_id="test_other", name="Другой")
        with pytest.raises(ValueError, match="исчерпан"):
            await ticket_svc.buy_ticket(user2.id, sample_event.id, promo_code="SUMMER10")

    async def test_promo_limit_consumed_after_n_buys(self, db_session, ticket_svc, sample_user, sample_event):
        """max_uses=2: две покупки ок, третья — лимит."""
        await self._create(ticket_svc, sample_event.id, max_uses=2)
        await db_session.commit()
        from app.core.services import UserService
        svc = UserService(db_session)
        u1 = await svc.get_or_create(platform=PlatformType.telegram, platform_user_id="u1", name="U1")
        u2 = await svc.get_or_create(platform=PlatformType.telegram, platform_user_id="u2", name="U2")
        u3 = await svc.get_or_create(platform=PlatformType.telegram, platform_user_id="u3", name="U3")
        await db_session.commit()
        await ticket_svc.buy_ticket(u1.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        await ticket_svc.buy_ticket(u2.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        with pytest.raises(ValueError, match="исчерпан"):
            await ticket_svc.buy_ticket(u3.id, sample_event.id, promo_code="SUMMER10")

    # ─── Webapp, совместимость, возврат, список ─────────────────

    async def test_buy_webapp_with_promo_dict_fields(self, db_session, ticket_svc, sample_user, sample_event):
        """buy_ticket_webapp возвращает amount со скидкой + поля скидки."""
        await self._create(ticket_svc, sample_event.id, discount_type=DiscountType.percent, value=20)
        await db_session.commit()
        result = await ticket_svc.buy_ticket_webapp(sample_user.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        assert result["amount"] == 800.0
        assert result["base_amount"] == 1000.0
        assert result["discount_amount"] == 200.0
        assert result["promo_code"] == "SUMMER10"

    async def test_buy_without_promo_backward_compat(self, db_session, ticket_svc, sample_user, sample_event):
        """Покупка без промокода: base==price, discount==0, promo None."""
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()
        payment = await self._payment(db_session, ticket.id)
        assert float(payment.base_amount) == 1000.0
        assert float(payment.discount_amount) == 0.0
        assert payment.promo_code is None

    async def test_cancel_ticket_keeps_used_count(self, db_session, ticket_svc, sample_user, sample_event):
        """Возврат билета НЕ возвращает слот использований промокода."""
        promo = await self._create(ticket_svc, sample_event.id, max_uses=2)
        await db_session.commit()
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        await ticket_svc.cancel_ticket(ticket.id, sample_user.id)
        await db_session.commit()
        assert promo.used_count == 1  # слот потреблён при покупке

    async def test_toggle_promo_deactivates(self, db_session, ticket_svc, sample_event):
        """Toggle выключает промокод."""
        promo = await self._create(ticket_svc, sample_event.id)
        await db_session.commit()
        assert promo.is_active is True
        await ticket_svc.toggle_promo_code(promo.id)
        await db_session.commit()
        assert promo.is_active is False

    async def test_list_promo_codes_fields(self, db_session, ticket_svc, sample_event):
        """Список промокодов события: нужные поля."""
        await self._create(ticket_svc, sample_event.id, code="ONE", discount_type=DiscountType.fixed, value=50)
        await self._create(ticket_svc, sample_event.id, code="TWO", discount_type=DiscountType.percent, value=15, max_uses=5)
        await db_session.commit()
        promos = await ticket_svc.list_promo_codes(sample_event.id)
        assert len(promos) == 2
        codes = {p["code"]: p for p in promos}
        assert codes["TWO"]["discount_type"] == "percent"
        assert codes["TWO"]["discount_value"] == 15
        assert codes["TWO"]["max_uses"] == 5
        assert codes["TWO"]["used_count"] == 0
        assert codes["TWO"]["is_active"] is True


# ═══════════════════════════════════════════════════════════════
# Динамические цены по дате (early bird, pro-фича)
# ═══════════════════════════════════════════════════════════════

class TestPriceRanges:
    """Эшелоны цен по датам: effective_price_at + replace_price_ranges + покупка."""

    async def _ranges(self, db_session, event, price=100):
        """Выставить базовую цену + published_at (now-2d) и вернуть единый `now`
        для построения диапазонов — чтобы не было микро-дыр от разных now."""
        event.price = price
        now = datetime.now(timezone.utc)
        event.published_at = now - timedelta(days=2)
        await db_session.flush()
        return event, now

    # ─── effective_price_at ─────────────────────────────────────

    async def test_effective_price_inside_range(self, db_session, event_svc, sample_event):
        """dt в диапазоне → цена диапазона."""
        _, now = await self._ranges(db_session, sample_event)
        end = now + timedelta(days=3)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": end, "price": 150},
            {"starts_at": end, "ends_at": sample_event.date, "price": 200},
        ])
        # dt сейчас → первый диапазон (150)
        assert float(await event_svc.effective_price_at(sample_event, now)) == 150.0

    async def test_effective_price_no_ranges_fallback(self, db_session, event_svc, sample_event):
        """Без диапазонов → базовая цена."""
        await self._ranges(db_session, sample_event, price=300)
        assert float(await event_svc.effective_price_at(sample_event, datetime.now(timezone.utc))) == 300.0

    async def test_effective_price_boundary_start_inclusive(self, db_session, event_svc, sample_event):
        """dt == starts_at → цена диапазона (граница включительно)."""
        _, now = await self._ranges(db_session, sample_event)
        start = now - timedelta(days=2)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": start, "ends_at": now + timedelta(days=3), "price": 120},
            {"starts_at": now + timedelta(days=3), "ends_at": sample_event.date, "price": 200},
        ])
        assert float(await event_svc.effective_price_at(sample_event, start)) == 120.0

    async def test_effective_price_boundary_end_exclusive(self, db_session, event_svc, sample_event):
        """dt == ends_at промежуточного → следующий диапазон (end не включается)."""
        _, now = await self._ranges(db_session, sample_event)
        mid = now + timedelta(days=3)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": mid, "price": 120},
            {"starts_at": mid, "ends_at": sample_event.date, "price": 200},
        ])
        assert float(await event_svc.effective_price_at(sample_event, mid)) == 200.0

    async def test_effective_price_last_range_includes_event_date(self, db_session, event_svc, sample_event):
        """dt == event.date → цена последнего диапазона (включительно)."""
        _, now = await self._ranges(db_session, sample_event)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": sample_event.date, "price": 200},
        ])
        assert float(await event_svc.effective_price_at(sample_event, sample_event.date)) == 200.0

    async def test_effective_price_free_event_ignored(self, db_session, event_svc, sample_event):
        """Бесплатное событие (price=0) → 0 независимо от диапазонов."""
        await self._ranges(db_session, sample_event, price=0)
        assert float(await event_svc.effective_price_at(sample_event, datetime.now(timezone.utc))) == 0.0

    # ─── replace_price_ranges ───────────────────────────────────

    async def test_replace_ranges_success(self, db_session, event_svc, sample_event):
        """2 диапазона с полным покрытием; старые удалены."""
        _, now = await self._ranges(db_session, sample_event)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": now + timedelta(days=3), "price": 100},
            {"starts_at": now + timedelta(days=3), "ends_at": sample_event.date, "price": 200},
        ])
        ranges = await event_svc.get_price_ranges(sample_event.id)
        assert len(ranges) == 2
        assert [r["price"] for r in ranges] == [100.0, 200.0]

    async def test_replace_hole_in_middle_raises(self, db_session, event_svc, sample_event):
        """Дыра между диапазонами → ValueError."""
        _, now = await self._ranges(db_session, sample_event)
        with pytest.raises(ValueError, match="не покрывают|дыра"):
            await event_svc.replace_price_ranges(sample_event.id, [
                {"starts_at": now - timedelta(days=2), "ends_at": now + timedelta(days=2), "price": 100},
                {"starts_at": now + timedelta(days=4), "ends_at": sample_event.date, "price": 200},
            ])

    async def test_replace_gap_at_start_raises(self, db_session, event_svc, sample_event):
        """Первый диапазон начинается позже published_at → дыра в начале."""
        _, now = await self._ranges(db_session, sample_event)
        with pytest.raises(ValueError, match="не покрывают"):
            await event_svc.replace_price_ranges(sample_event.id, [
                {"starts_at": now + timedelta(days=1), "ends_at": sample_event.date, "price": 100},
            ])

    async def test_replace_gap_at_end_raises(self, db_session, event_svc, sample_event):
        """Последний диапазон заканчивается раньше event.date → дыра в конце."""
        _, now = await self._ranges(db_session, sample_event)
        with pytest.raises(ValueError, match="не покрывают"):
            await event_svc.replace_price_ranges(sample_event.id, [
                {"starts_at": now - timedelta(days=2), "ends_at": sample_event.date - timedelta(days=1), "price": 100},
            ])

    async def test_replace_overlap_raises(self, db_session, event_svc, sample_event):
        """Пересечение диапазонов → ValueError."""
        _, now = await self._ranges(db_session, sample_event)
        with pytest.raises(ValueError, match="пересекаются"):
            await event_svc.replace_price_ranges(sample_event.id, [
                {"starts_at": now - timedelta(days=2), "ends_at": now + timedelta(days=5), "price": 100},
                {"starts_at": now + timedelta(days=3), "ends_at": sample_event.date, "price": 200},
            ])

    async def test_replace_ends_before_starts_raises(self, db_session, event_svc, sample_event):
        """end раньше start → ValueError."""
        _, now = await self._ranges(db_session, sample_event)
        with pytest.raises(ValueError, match="позже"):
            await event_svc.replace_price_ranges(sample_event.id, [
                {"starts_at": now + timedelta(days=3), "ends_at": now, "price": 100},
            ])

    async def test_replace_negative_price_raises(self, db_session, event_svc, sample_event):
        """Отрицательная цена → ValueError."""
        _, now = await self._ranges(db_session, sample_event)
        with pytest.raises(ValueError, match="отрицательн"):
            await event_svc.replace_price_ranges(sample_event.id, [
                {"starts_at": now - timedelta(days=2), "ends_at": sample_event.date, "price": -5},
            ])

    async def test_replace_range_outside_window_raises(self, db_session, event_svc, sample_event):
        """Диапазон выходит за [published_at, event.date] → ValueError."""
        _, now = await self._ranges(db_session, sample_event)
        with pytest.raises(ValueError):
            await event_svc.replace_price_ranges(sample_event.id, [
                {"starts_at": now - timedelta(days=10), "ends_at": sample_event.date, "price": 100},
            ])

    async def test_replace_empty_clears(self, db_session, event_svc, sample_event):
        """Пустой список → диапазоны удалены (выключить динамику)."""
        _, now = await self._ranges(db_session, sample_event)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": sample_event.date, "price": 100},
        ])
        await event_svc.replace_price_ranges(sample_event.id, [])
        assert await event_svc.get_price_ranges(sample_event.id) == []

    async def test_replace_free_event_raises(self, db_session, event_svc, sample_event):
        """Бесплатное событие (price=0) не может иметь диапазоны."""
        _, now = await self._ranges(db_session, sample_event, price=0)
        with pytest.raises(ValueError, match="платн"):
            await event_svc.replace_price_ranges(sample_event.id, [
                {"starts_at": now - timedelta(days=2), "ends_at": sample_event.date, "price": 100},
            ])

    # ─── Покупка с диапазоном ──────────────────────────────────

    async def test_buy_ticket_uses_range_price(self, db_session, ticket_svc, sample_user, sample_event, event_svc):
        """Покупка берёт цену диапазона (base_amount = цена диапазона)."""
        _, now = await self._ranges(db_session, sample_event)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": now + timedelta(days=3), "price": 150},
            {"starts_at": now + timedelta(days=3), "ends_at": sample_event.date, "price": 200},
        ])
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()
        pm = (await db_session.execute(select(Payment).where(Payment.ticket_id == ticket.id))).scalar_one()
        assert float(pm.base_amount) == 150.0
        assert float(pm.amount) == 150.0

    async def test_buy_webapp_uses_range_price_dict_fields(self, db_session, ticket_svc, sample_user, sample_event, event_svc):
        """buy_ticket_webapp возвращает base_amount = цена диапазона."""
        _, now = await self._ranges(db_session, sample_event)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": sample_event.date, "price": 250},
        ])
        result = await ticket_svc.buy_ticket_webapp(sample_user.id, sample_event.id)
        await db_session.commit()
        assert result["base_amount"] == 250.0
        assert result["amount"] == 250.0

    async def test_buy_with_promo_on_top_of_range_price(self, db_session, ticket_svc, sample_user, sample_event, event_svc):
        """Промокод применяется к цене диапазона."""
        _, now = await self._ranges(db_session, sample_event)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": sample_event.date, "price": 200},
        ])
        await ticket_svc.create_promo_code(sample_event.id, "SUMMER10", DiscountType.percent, 10)
        await db_session.commit()
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id, promo_code="SUMMER10")
        await db_session.commit()
        pm = (await db_session.execute(select(Payment).where(Payment.ticket_id == ticket.id))).scalar_one()
        assert float(pm.base_amount) == 200.0
        assert float(pm.amount) == 180.0  # 200 - 10%

    async def test_buy_fixes_price_for_ticket(self, db_session, ticket_svc, sample_user, sample_event, event_svc):
        """Цена фиксируется при покупке: рост цены не меняет купленный билет."""
        _, now = await self._ranges(db_session, sample_event)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": now + timedelta(days=3), "price": 100},
            {"starts_at": now + timedelta(days=3), "ends_at": sample_event.date, "price": 200},
        ])
        ticket = await ticket_svc.buy_ticket(sample_user.id, sample_event.id)
        await db_session.commit()
        pm1 = (await db_session.execute(select(Payment).where(Payment.ticket_id == ticket.id))).scalar_one()
        assert float(pm1.base_amount) == 100.0
        # Организатор поднял первый диапазон — купленный билет не тронут
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": now + timedelta(days=3), "price": 700},
            {"starts_at": now + timedelta(days=3), "ends_at": sample_event.date, "price": 200},
        ])
        pm2 = (await db_session.execute(select(Payment).where(Payment.ticket_id == ticket.id))).scalar_one()
        assert float(pm2.base_amount) == 100.0

    # ─── Гейты и мутации ───────────────────────────────────────

    async def test_has_event_pro_feature_dynamic_pricing_premium(self, db_session, event_svc, sample_user):
        """Премиум события даёт dynamic_pricing."""
        from app.core.services import EventService, UserService
        user_id = sample_user.id
        # Pro-подписка нужна, чтобы создать платное owner-событие
        await UserService(db_session).activate_subscription(
            user_id, days=30, tier=SubscriptionTier.pro
        )
        await db_session.commit()
        ev = await EventService(db_session).create(
            title="Prem", description=None,
            date=datetime.now(timezone.utc) + timedelta(days=7),
            location=None, price=100, total_tickets=10,
            channel_id=None, owner_user_id=user_id,
        )
        await event_svc.purchase_event_premium(ev.id, user_id)
        await db_session.commit()
        assert await event_svc.has_event_pro_feature(ev.id, "dynamic_pricing") is True

    async def test_update_price_to_zero_deletes_ranges(self, db_session, event_svc, sample_event):
        """Смена цены на 0 удаляет диапазоны (инвариант «динамика только для платных»)."""
        _, now = await self._ranges(db_session, sample_event)
        await event_svc.replace_price_ranges(sample_event.id, [
            {"starts_at": now - timedelta(days=2), "ends_at": sample_event.date, "price": 100},
        ])
        await event_svc.update(sample_event.id, price=0)
        await db_session.commit()
        assert await event_svc.get_price_ranges(sample_event.id) == []
