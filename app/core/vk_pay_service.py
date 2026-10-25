"""VK Pay ticket reservations and verified fulfillment."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import (
    Event,
    Payment,
    PaymentStatus,
    PromoCode,
    Ticket,
    TicketStatus,
    User,
    VKPayOrder,
    VKPayProductOrder,
    SubscriptionTier,
)
from app.core.services import EventService, TicketService, UserService

logger = logging.getLogger("ticketbot.vk_pay")
ORDER_TTL = timedelta(minutes=15)


class VKPayOrderService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def reserved_seats_map(self, event_ids: list[uuid.UUID], now: datetime | None = None) -> dict[uuid.UUID, int]:
        if not event_ids:
            return {}
        now = now or datetime.now(timezone.utc)
        result = await self.session.execute(
            select(VKPayOrder.event_id, func.count(VKPayOrder.id))
            .where(
                VKPayOrder.event_id.in_(event_ids),
                VKPayOrder.status == "pending",
                VKPayOrder.expires_at > now,
            )
            .group_by(VKPayOrder.event_id)
        )
        return {event_id: int(count) for event_id, count in result.all()}

    async def create_order(
        self,
        *,
        event_id: uuid.UUID,
        user: User,
        platform_user_id: str,
        promo_code: str | None = None,
    ) -> tuple[VKPayOrder, Event]:
        now = datetime.now(timezone.utc)
        event_result = await self.session.execute(
            select(Event).where(Event.id == event_id).with_for_update()
        )
        event = event_result.scalar_one_or_none()
        if event is None:
            raise ValueError("Мероприятие не найдено")
        if not event.is_published:
            raise ValueError("Мероприятие не опубликовано")
        if not event.is_active:
            raise ValueError("Мероприятие неактивно")
        if event.date < now:
            raise ValueError("Мероприятие уже прошло")

        pending = await self.session.execute(
            select(VKPayOrder).where(
                VKPayOrder.event_id == event_id,
                VKPayOrder.user_id == user.id,
                VKPayOrder.status == "pending",
                VKPayOrder.expires_at > now,
            ).with_for_update()
        )
        existing_order = pending.scalar_one_or_none()
        if existing_order is not None:
            if promo_code and (promo_code.strip().upper() != (existing_order.promo_code or "")):
                raise ValueError("У вас уже есть ожидающий оплаты заказ")
            return existing_order, event

        existing_ticket = await self.session.execute(
            select(Ticket.id).where(
                Ticket.user_id == user.id,
                Ticket.event_id == event_id,
                Ticket.status == TicketStatus.active,
            )
        )
        if existing_ticket.scalar_one_or_none() is not None:
            raise ValueError("У вас уже есть активный билет на это мероприятие")

        reservations = await self.reserved_seats_map([event_id], now)
        if event.available_tickets - reservations.get(event_id, 0) <= 0:
            raise ValueError("Билеты закончились")

        event_svc = EventService(self.session)
        base = await event_svc.effective_price_at(event, now)
        if base <= 0:
            raise ValueError("Бесплатные билеты оформляются без VK Pay")

        discount = Decimal("0")
        promo = None
        normalized_code = None
        if promo_code:
            ticket_svc = TicketService(self.session)
            promo = await ticket_svc._validate_promo(event_id, promo_code)
            discount = ticket_svc._compute_discount(promo, base)
            normalized_code = promo.code
        amount = max(base - discount, Decimal("0"))
        if amount < Decimal("1.00"):
            raise ValueError("Сумма после скидки меньше минимальной оплаты VK Pay — 1 ₽")

        order = VKPayOrder(
            id=uuid.uuid4(),
            issuer_id=uuid.uuid4().hex,
            event_id=event_id,
            user_id=user.id,
            platform_user_id=platform_user_id,
            amount=amount,
            base_amount=base,
            discount_amount=discount,
            promo_code_id=promo.id if promo else None,
            promo_code=normalized_code,
            status="pending",
            expires_at=now + ORDER_TTL,
        )
        self.session.add(order)
        await self.session.flush()
        return order, event

    async def get_user_order(self, issuer_id: str, user_id: uuid.UUID) -> VKPayOrder | None:
        result = await self.session.execute(
            select(VKPayOrder).where(
                VKPayOrder.issuer_id == issuer_id,
                VKPayOrder.user_id == user_id,
            ).with_for_update()
        )
        order = result.scalar_one_or_none()
        if order is not None and order.status == "pending" and order.expires_at <= datetime.now(timezone.utc):
            order.status = "expired"
            await self.session.flush()
        return order

    async def handle_transaction(self, body: dict) -> tuple[str, str | None]:
        """Apply provider status once; returns (order status, ticket id)."""
        issuer_id = str(body.get("issuer_id") or "")
        transaction_id = str(body.get("transaction_id") or "")
        if not issuer_id or not transaction_id:
            raise ValueError("В уведомлении VK Pay отсутствует ID заказа или транзакции")

        result = await self.session.execute(
            select(VKPayOrder).where(VKPayOrder.issuer_id == issuer_id).with_for_update()
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise ValueError("Заказ VK Pay не найден")
        if order.status == "completed":
            if order.provider_transaction_id != transaction_id:
                raise ValueError("Для заказа уже сохранена другая транзакция")
            return order.status, str(order.ticket_id) if order.ticket_id else None

        if order.status not in {"pending", "expired"}:
            return order.status, None

        was_expired = order.status == "expired" or order.expires_at <= datetime.now(timezone.utc)
        provider_status = str(body.get("status") or "")
        if provider_status in {"rejected", "expired", "hold_failed", "hold_canceled"}:
            order.status = "failed"
            await self.session.flush()
            return order.status, None
        if provider_status != "paid":
            raise ValueError("Транзакция VK Pay ещё не имеет окончательного статуса")
        event_result = await self.session.execute(
            select(Event).where(Event.id == order.event_id).with_for_update()
        )
        event = event_result.scalar_one_or_none()
        if event is None:
            order.status = "paid_unfulfilled"
            order.provider_transaction_id = transaction_id
            order.settled_at = datetime.now(timezone.utc)
            await self.session.flush()
            logger.error("VK Pay transaction paid for missing event: issuer_id=%s", issuer_id)
            return order.status, None

        duplicate_ticket = await self.session.execute(
            select(Ticket.id).where(
                Ticket.event_id == event.id,
                Ticket.user_id == order.user_id,
                Ticket.status.in_([TicketStatus.active, TicketStatus.checked_in]),
            )
        )
        if duplicate_ticket.scalar_one_or_none() is not None:
            order.status = "paid_unfulfilled"
            order.provider_transaction_id = transaction_id
            order.settled_at = datetime.now(timezone.utc)
            await self.session.flush()
            logger.error("VK Pay paid duplicate ticket order: issuer_id=%s", issuer_id)
            return order.status, None

        now = datetime.now(timezone.utc)
        if was_expired and order.promo_code_id is not None:
            promo_result = await self.session.execute(
                select(PromoCode).where(PromoCode.id == order.promo_code_id).with_for_update()
            )
            promo = promo_result.scalar_one_or_none()
            if promo is not None and promo.max_uses > 0:
                reserved_promos = await self.session.execute(
                    select(func.count(VKPayOrder.id)).where(
                        VKPayOrder.promo_code_id == promo.id,
                        VKPayOrder.issuer_id != order.issuer_id,
                        VKPayOrder.status == "pending",
                        VKPayOrder.expires_at > now,
                    )
                )
                if promo.used_count + int(reserved_promos.scalar_one()) >= promo.max_uses:
                    order.status = "paid_unfulfilled"
                    order.provider_transaction_id = transaction_id
                    order.settled_at = now
                    await self.session.flush()
                    logger.error("VK Pay late paid order lost its promo reservation: issuer_id=%s", issuer_id)
                    return order.status, None

        other_reservations = await self.session.execute(
            select(func.count(VKPayOrder.id)).where(
                VKPayOrder.event_id == event.id,
                VKPayOrder.issuer_id != order.issuer_id,
                VKPayOrder.status == "pending",
                VKPayOrder.expires_at > now,
            )
        )
        reserved_seats = int(other_reservations.scalar_one())
        if event.available_tickets - reserved_seats <= 0:
            order.status = "paid_unfulfilled"
            order.provider_transaction_id = transaction_id
            order.settled_at = now
            await self.session.flush()
            logger.error(
                "VK Pay transaction paid without available ticket: issuer_id=%s event_id=%s",
                issuer_id,
                event.id,
            )
            return order.status, None

        # Ticket is created only after the signed provider notification is checked.
        ticket = Ticket(
            id=uuid.uuid4(),
            event_id=event.id,
            user_id=order.user_id,
            status=TicketStatus.active,
            validation_code=await TicketService.generate_validation_code(),
            is_free=False,
        )
        self.session.add(ticket)
        event.available_tickets -= 1
        # The order has no ORM relationship to Ticket; flush the ticket first so
        # the FK update on vk_pay_orders is never emitted before the ticket insert.
        await self.session.flush()

        payment = Payment(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            amount=order.amount,
            status=PaymentStatus.completed,
            provider="vk_pay",
            provider_order_id=order.issuer_id,
            provider_transaction_id=transaction_id,
            base_amount=order.base_amount,
            discount_amount=order.discount_amount,
            promo_code=order.promo_code,
        )
        self.session.add(payment)
        if order.promo_code_id is not None:
            promo_result = await self.session.execute(
                select(PromoCode).where(PromoCode.id == order.promo_code_id).with_for_update()
            )
            promo = promo_result.scalar_one_or_none()
            if promo is not None:
                promo.used_count += 1

        order.status = "completed"
        order.provider_transaction_id = transaction_id
        order.ticket_id = ticket.id
        order.settled_at = datetime.now(timezone.utc)
        await self.session.flush()

        logger.info("", extra={
            "event_type": "vk_pay.ticket_settled",
            "issuer_id": issuer_id,
            "transaction_id": transaction_id,
            "event_id": str(event.id),
            "ticket_id": str(ticket.id),
            "amount": float(order.amount),
            "status": "success",
        })
        return order.status, str(ticket.id)

    async def create_product_order(
        self,
        *,
        user: User,
        platform_user_id: str,
        product_type: str,
        amount: Decimal,
        tier: SubscriptionTier | None = None,
        event_id: uuid.UUID | None = None,
    ) -> VKPayProductOrder:
        """Create a fixed-price order for an organizer feature."""
        now = datetime.now(timezone.utc)
        if not amount.is_finite() or amount < Decimal("1.00"):
            raise ValueError("Цена должна быть не меньше 1 ₽")
        await self.session.execute(
            select(User.id).where(User.id == user.id).with_for_update()
        )
        if product_type == "subscription":
            if tier is None or event_id is not None:
                raise ValueError("Для подписки нужно указать тариф")
        elif product_type == "event_premium":
            if event_id is None or tier is not None:
                raise ValueError("Для премиума нужно указать мероприятие")
            event_result = await self.session.execute(
                select(Event).where(Event.id == event_id).with_for_update()
            )
            event = event_result.scalar_one_or_none()
            if event is None or event.owner_user_id != user.id:
                raise ValueError("Мероприятие не найдено или принадлежит другому организатору")
            if event.date <= now:
                raise ValueError("Мероприятие уже прошло")
            if await EventService(self.session).get_event_is_premium(event_id):
                raise ValueError("Премиум для мероприятия уже активен")
        else:
            raise ValueError("Неизвестный продукт VK Pay")

        pending_result = await self.session.execute(
            select(VKPayProductOrder).where(
                VKPayProductOrder.user_id == user.id,
                VKPayProductOrder.product_type == product_type,
                VKPayProductOrder.tier == tier,
                VKPayProductOrder.event_id == event_id,
                VKPayProductOrder.status == "pending",
                VKPayProductOrder.expires_at > now,
            ).with_for_update()
        )
        existing = pending_result.scalar_one_or_none()
        if existing is not None:
            return existing

        order = VKPayProductOrder(
            issuer_id=uuid.uuid4().hex,
            user_id=user.id,
            platform_user_id=platform_user_id,
            product_type=product_type,
            tier=tier,
            event_id=event_id,
            amount=amount.quantize(Decimal("0.01")),
            status="pending",
            expires_at=now + ORDER_TTL,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_product_order(
        self, issuer_id: str, user_id: uuid.UUID
    ) -> VKPayProductOrder | None:
        result = await self.session.execute(
            select(VKPayProductOrder).where(
                VKPayProductOrder.issuer_id == issuer_id,
                VKPayProductOrder.user_id == user_id,
            ).with_for_update()
        )
        order = result.scalar_one_or_none()
        if order is not None and order.status == "pending" and order.expires_at <= datetime.now(timezone.utc):
            order.status = "expired"
            await self.session.flush()
        return order

    async def handle_product_transaction(self, body: dict) -> str:
        """Idempotently fulfill an organizer product after a verified VK callback."""
        issuer_id = str(body.get("issuer_id") or "")
        transaction_id = str(body.get("transaction_id") or "")
        if not issuer_id or not transaction_id:
            raise ValueError("В уведомлении VK Pay отсутствует ID заказа или транзакции")
        result = await self.session.execute(
            select(VKPayProductOrder)
            .where(VKPayProductOrder.issuer_id == issuer_id)
            .with_for_update()
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise ValueError("Заказ VK Pay не найден")
        if order.status == "completed":
            if order.provider_transaction_id != transaction_id:
                raise ValueError("Для заказа уже сохранена другая транзакция")
            return order.status
        if order.status not in {"pending", "expired"}:
            return order.status

        provider_status = str(body.get("status") or "")
        if provider_status in {"rejected", "expired", "hold_failed", "hold_canceled"}:
            order.status = "failed"
            await self.session.flush()
            return order.status
        if provider_status != "paid":
            raise ValueError("Транзакция VK Pay ещё не имеет окончательного статуса")

        amount = Decimal(str(body.get("amount", "0"))).quantize(Decimal("0.01"))
        if amount != Decimal(str(order.amount)).quantize(Decimal("0.01")):
            raise ValueError("Сумма транзакции VK Pay не совпадает с заказом")
        if body.get("currency") != "RUB":
            raise ValueError("Поддерживается только оплата в RUB")

        if order.product_type == "subscription":
            user = await self.session.get(User, order.user_id, with_for_update=True)
            if user is None:
                raise ValueError("Пользователь заказа VK Pay не найден")
            await UserService(self.session).activate_subscription(
                user.id, days=30, tier=order.tier,
            )
        elif order.product_type == "event_premium" and order.event_id is not None:
            event = await self.session.get(Event, order.event_id, with_for_update=True)
            if event is None or event.date <= datetime.now(timezone.utc):
                order.status = "paid_unfulfilled"
                order.provider_transaction_id = transaction_id
                order.settled_at = datetime.now(timezone.utc)
                await self.session.flush()
                logger.error("VK Pay premium payment could not be fulfilled: issuer_id=%s", issuer_id)
                return order.status
            await EventService(self.session).purchase_event_premium(
                order.event_id,
                order.user_id,
                amount=float(order.amount),
                provider="vk_pay",
            )
        else:
            raise ValueError("Тип заказа VK Pay не поддерживается")

        order.status = "completed"
        order.provider_transaction_id = transaction_id
        order.settled_at = datetime.now(timezone.utc)
        await self.session.flush()
        return order.status
