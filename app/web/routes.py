"""
REST API endpoints for Telegram Mini App.

User-facing endpoints require platform initData validation. VK Pay notifications use VK's signed merchant callback protocol.
"""

import csv
import io
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from app.config import settings
from app.core.database import async_session_factory
from app.core.models import PlatformType, Event, VKPayOrder
from app.core.vk_pay_service import VKPayOrderService
from app.core.qr import generate_qr_png
from app.core.schemas import (
    AddManagerIn,
    BroadcastIn,
    ChangeAdminIn,
    ChangeTierIn,
    ChannelRegisterIn,
    ChannelSubscribeIn,
    CheckInIn,
    EventCreate,
    EventUpdateIn,
    InviteIssueIn,
    LinkCodeIn,
    LinkConsumeIn,
    MeUpdateIn,
    EventPremiumIn,
    PublishIn,
    SubscribeIn,
    SubscribeMeIn,
    UpdateSubscriptionIn,
    VKGroupRegisterIn,
    PromoCodeCreate,
    BuyIn,
    PriceRangesUpdate,
)
from app.core.services import (
    ChannelAdminService,
    ChannelService,
    EventService,
    StatsService,
    TicketService,
    UserService,
    VKGroupService,
)
from app.web.dependencies import (
    CurrentUser,
    get_current_user,
    require_admin,
    require_super_admin,
    validate_init_data,
)
from app.platforms.telegram.formatting import format_event_text
from app.web.announce import post_event_announcement, send_announcement_dm, send_broadcast
from app.web.telegram_client import get_telegram_bot
from app.web.vk_api import post_to_group_wall, verify_group_token, send_vk_ticket_dm
from app.web.vk_pay import (
    VK_PAY_NOTIFICATION_VERSION,
    build_notification_ack,
    build_open_pay_form_params,
    decode_notification,
    is_valid_notification_public_key,
    verify_notification_signature,
)

logger = logging.getLogger("ticketbot.web.routes")
router = APIRouter()


async def _send_ticket_dm(telegram_user_id: str, text: str) -> bool:
    """Отправить сообщение о билете в личные сообщения пользователю."""
    bot = get_telegram_bot()
    if bot is None:
        return False
    try:
        await bot.send_message(
            chat_id=int(telegram_user_id),
            text=text,
            parse_mode="HTML",
        )
        return True
    except Exception as e:
        logger.warning("Не удалось отправить DM пользователю %s: %s", telegram_user_id, e)
        return False


# ═══════════════════════════════════════════════════════════════
# Events
# ═══════════════════════════════════════════════════════════════


@router.get("/events")
async def list_events(
    auth_data: dict = Depends(validate_init_data),
    channel_id: str | None = None,
):
    """Get list of upcoming events, optionally filtered by channel."""
    async with async_session_factory() as session:
        svc = EventService(session)
        if channel_id:
            try:
                cid = UUID(channel_id)
            except ValueError:
                cid = None
            events = await svc.list_upcoming(channel_id=cid)
        else:
            events = await svc.list_upcoming()
        # Актуальная цена по дате (динамические цены) — батч-загрузка диапазонов
        now = datetime.now(timezone.utc)
        ranges_map = await svc.price_ranges_map([e.id for e in events])
        reserved_map = await VKPayOrderService(session).reserved_seats_map([e.id for e in events], now)

    return [
        {
            "id": str(e.id),
            "title": e.title,
            "date": e.date.isoformat(),
            "location": e.location,
            "price": EventService.resolve_price(ranges_map.get(e.id), e, now),
            "available_tickets": max(0, e.available_tickets - reserved_map.get(e.id, 0)),
            "total_tickets": e.total_tickets,
            "age_restriction": e.age_restriction,
            "media_file_id": e.media_telegram_file_id,
            "media_type": e.media_type,
        }
        for e in events
    ]


@router.get("/events/{event_id}")
async def get_event(event_id: str, auth_data: dict = Depends(validate_init_data)):
    """Get event details."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        svc = EventService(session)
        event = await svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        # Публичный доступ только к опубликованным, активным, не удалённым мероприятиям.
        # Черновики/неактивные/удалённые не должны быть видны по прямой ссылке.
        if not event.is_published or not event.is_active or event.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        # Актуальная цена по дате (динамические цены)
        now = datetime.now(timezone.utc)
        ranges_map = await svc.price_ranges_map([uid])
        effective_price = EventService.resolve_price(ranges_map.get(uid), event, now)
        reserved = (await VKPayOrderService(session).reserved_seats_map([uid], now)).get(uid, 0)

    return {
        "id": str(event.id),
        "title": event.title,
        "description": event.description,
        "date": event.date.isoformat(),
        "location": event.location,
        "price": effective_price,
        "available_tickets": max(0, event.available_tickets - reserved),
        "total_tickets": event.total_tickets,
        "is_active": event.is_active,
        "age_restriction": event.age_restriction,
        "media_file_id": event.media_telegram_file_id,
        "media_type": event.media_type,
    }


# Простой in-memory кэш постеров (file_id → bytes), чтобы не дёргать Telegram API на каждый запрос.
_media_cache: dict[str, bytes] = {}
_MEDIA_CACHE_MAX = 64


@router.get("/events/{event_id}/media")
async def event_media(event_id: str):
    """Постер мероприятия: скачивает media_telegram_file_id через Telegram Bot.

    Публичный (постер виден как анонс). 404 если нет media, 502 если TG недоступен.
    """
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event = await EventService(session).get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        file_id = event.media_telegram_file_id
        media_type = event.media_type or "photo"

    if not file_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="У мероприятия нет постера")

    # Кэш
    cached = _media_cache.get(file_id)
    if cached is not None:
        return StreamingResponse(io.BytesIO(cached), media_type="image/jpeg")

    bot = get_telegram_bot()
    if bot is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Telegram API недоступен")

    try:
        tg_file = await bot.get_file(file_id)
        data = await bot.download_file(tg_file.file_path)
        raw = data.read() if hasattr(data, "read") else bytes(data)
    except Exception as e:
        logger.warning("Не удалось скачать постер %s: %s", event_id, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Ошибка загрузки постера")

    # Кэшируем (простейший LRU-по размеру)
    if len(_media_cache) >= _MEDIA_CACHE_MAX:
        _media_cache.pop(next(iter(_media_cache)))
    _media_cache[file_id] = raw

    mime = "image/jpeg"
    if media_type == "video":
        mime = "video/mp4"
    elif raw[:3] == b"\x89PN":
        mime = "image/png"
    return StreamingResponse(io.BytesIO(raw), media_type=mime)


@router.post("/events/{event_id}/buy", status_code=status.HTTP_201_CREATED)
async def buy_ticket(
    event_id: str,
    body: BuyIn | None = None,
    auth_data: dict = Depends(validate_init_data),
):
    """Purchase a ticket for an event. Опциональный промокод в теле."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    # Get or create user
    user_data = auth_data.get("user", {})
    platform_user_id = str(user_data.get("id", "0"))
    name = user_data.get("first_name", "")

    async with async_session_factory() as session:
        # Paid VK tickets must use the provider-backed order flow; never allow the
        # legacy immediate-success path to be called directly instead of the UI.
        if auth_data.get("platform") == "vk":
            event = await EventService(session).get_by_id(uid)
            if event is not None:
                now = datetime.now(timezone.utc)
                ranges = await EventService(session).price_ranges_map([uid])
                if EventService.resolve_price(ranges.get(uid), event, now) > 0:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Платные билеты во VK оформляются только через VK Pay",
                    )

        # Get/create user
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=PlatformType(auth_data.get("platform", "telegram")),
            platform_user_id=platform_user_id,
            name=name,
        )

        # Buy ticket via Mini App flow
        ticket_svc = TicketService(session)
        try:
            promo = body.promo_code if body else None
            result = await ticket_svc.buy_ticket_webapp(user.id, uid, promo_code=promo)
            await session.commit()

            # A: код в DM — только для бесплатного билета (платный предъявляется QR).
            code_text = f"\n🔑 Код: <code>{result['validation_code']}</code>" if (result.get("is_free") and result.get("validation_code")) else ""
            ticket_text = (
                f"✅ Билет куплен!\n"
                f"🎫 {result['event_title']}\n"
                f"📅 {result['event_date']}{code_text}"
            )

            # Telegram-покупатель: DM от бота (как было).
            if auth_data.get("platform") != "vk":
                await _send_ticket_dm(platform_user_id, ticket_text)

            # VK-покупатель: НЕ шлём DM сразу (разрешение ещё не дано). Вместо этого
            # возвращаем vk_group_id — фронт предложит получить билет в ЛС и после
            # VKWebAppAllowMessagesFromGroup вызовет POST /tickets/{id}/send-vk.
            vk_group_id = None
            if auth_data.get("platform") == "vk":
                try:
                    event = await session.get(Event, uid)
                    if event is not None and event.owner_user_id is not None:
                        group_svc = VKGroupService(session)
                        groups = await group_svc.list_vk_groups(event.owner_user_id)
                        vk_group = next((g for g in groups if g.community_token), None)
                        if vk_group is not None:
                            vk_group_id = vk_group.group_id
                except Exception as e:
                    logger.warning("Не удалось определить VK-группу для билета %s: %s", uid, e)
            if vk_group_id:
                result["vk_group_id"] = vk_group_id

            return result
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


def _vk_pay_ack(transaction_id: str, notification: str) -> JSONResponse:
    return JSONResponse(build_notification_ack(
        transaction_id=transaction_id,
        client_id=settings.vk_pay_client_id,
        merchant_private_key=settings.vk_pay_merchant_private_key,
        timestamp=int(datetime.now(timezone.utc).timestamp()),
        notification=notification,
    ))


def _vk_pay_credentials_configured() -> bool:
    required = (
        settings.vk_app_id,
        settings.vk_pay_merchant_id,
        settings.vk_pay_client_id,
        settings.vk_pay_app_secure_key,
        settings.vk_pay_merchant_private_key,
        settings.vk_pay_notification_public_key,
    )
    if not all(required):
        return False
    try:
        if int(settings.vk_app_id) <= 0 or int(settings.vk_pay_merchant_id) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    public_key = settings.vk_pay_notification_public_key.replace("\\n", "\n")
    return is_valid_notification_public_key(public_key)


def _vk_pay_is_configured() -> bool:
    return settings.vk_pay_enabled and _vk_pay_credentials_configured()


@router.post("/events/{event_id}/vk-pay-order", status_code=status.HTTP_201_CREATED)
async def create_vk_pay_order(
    event_id: str,
    body: BuyIn | None = None,
    auth_data: dict = Depends(validate_init_data),
):
    """Create a VK-only signed pay-to-service order; ticket waits for callback."""
    if auth_data.get("platform") != "vk":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="VK Pay доступен только во VK Mini App")
    if not _vk_pay_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Оплата VK Pay пока не подключена")
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    user_data = auth_data.get("user", {})
    platform_user_id = str(user_data.get("id", "0"))
    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=PlatformType.vk,
            platform_user_id=platform_user_id,
            name=user_data.get("first_name", ""),
        )
        order_svc = VKPayOrderService(session)
        try:
            order, event = await order_svc.create_order(
                event_id=uid,
                user=user,
                platform_user_id=platform_user_id,
                promo_code=body.promo_code if body else None,
            )
            params = build_open_pay_form_params(
                app_id=settings.vk_app_id,
                app_secure_key=settings.vk_pay_app_secure_key,
                merchant_id=int(settings.vk_pay_merchant_id),
                merchant_private_key=settings.vk_pay_merchant_private_key,
                order_id=order.issuer_id,
                amount=order.amount,
                user_id=int(platform_user_id),
                description=f"Билет: {event.title}"[:50],
                timestamp=int(datetime.now(timezone.utc).timestamp()),
            )
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return {
        "order_id": order.issuer_id,
        "amount": float(order.amount),
        "status": order.status,
        "expires_at": order.expires_at.isoformat(),
        "payment": params,
    }


@router.get("/vk-pay-orders/{issuer_id}")
async def get_vk_pay_order_status(
    issuer_id: str,
    auth_data: dict = Depends(validate_init_data),
):
    if auth_data.get("platform") != "vk":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Заказ VK Pay доступен только во VK Mini App")
    user_data = auth_data.get("user", {})
    async with async_session_factory() as session:
        user = await UserService(session).get_or_create(
            platform=PlatformType.vk,
            platform_user_id=str(user_data.get("id", "0")),
            name=user_data.get("first_name", ""),
        )
        order = await VKPayOrderService(session).get_user_order(issuer_id, user.id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заказ не найден")
        await session.commit()
        return {"order_id": order.issuer_id, "status": order.status, "ticket_id": str(order.ticket_id) if order.ticket_id else None}


@router.post("/vk-pay/notifications")
async def vk_pay_notification(request: Request):
    """Process VK Pay's RSA-signed transaction callback (not Mini App auth)."""
    if not _vk_pay_credentials_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="VK Pay callback is not configured")
    chunks = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > 65536:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Уведомление слишком большое")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        form = parse_qs(raw.decode("ascii"), keep_blank_values=True, strict_parsing=True)
        data = form["data"][0]
        signature = form["signature"][0]
        version = form["version"][0]
    except (UnicodeDecodeError, ValueError, KeyError, IndexError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректное VK Pay уведомление")
    if version != VK_PAY_NOTIFICATION_VERSION:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неподдерживаемая версия уведомления")
    public_key = settings.vk_pay_notification_public_key.replace("\\n", "\n")
    if not verify_notification_signature(data, signature, public_key):
        logger.warning("VK Pay callback rejected: invalid signature")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Подпись VK Pay не прошла проверку")
    try:
        payload = decode_notification(data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    provider_body = payload["body"]
    header = payload.get("header") or {}
    try:
        transaction_id = str(UUID(str(provider_body.get("transaction_id", ""))))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID транзакции VK Pay")
    if (
        provider_body.get("notify_type") != "TRANSACTION_STATUS"
        or header.get("status") != "OK"
        or str(header.get("client_id")) != str(settings.vk_pay_client_id)
        or str(provider_body.get("merchant_id")) != str(settings.vk_pay_merchant_id)
    ):
        logger.warning("VK Pay callback rejected: merchant or protocol mismatch")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Данные платежа VK Pay не совпали с настройками")

    try:
        amount = Decimal(str(provider_body.get("amount"))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректная сумма VK Pay")
    if amount <= 0 or provider_body.get("currency") != "RUB":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Валюта или сумма VK Pay не поддерживается")

    issuer_id = str(provider_body.get("issuer_id") or "")
    async with async_session_factory() as session:
        order_result = await session.execute(
            select(VKPayOrder).where(VKPayOrder.issuer_id == issuer_id)
        )
        order = order_result.scalar_one_or_none()
        if order is None:
            logger.warning("VK Pay callback has unknown order %s", issuer_id)
            return _vk_pay_ack(transaction_id, "payment_declined")
        if amount != Decimal(str(order.amount)).quantize(Decimal("0.01")):
            logger.warning("VK Pay callback rejected: amount mismatch for order %s", issuer_id)
            return _vk_pay_ack(transaction_id, "payment_declined")
        user_info = provider_body.get("user_info") or {}
        if str(user_info.get("user_id", "")) != order.platform_user_id:
            logger.warning("VK Pay callback rejected: payer mismatch for order %s", issuer_id)
            return _vk_pay_ack(transaction_id, "payment_declined")
        try:
            order_status, _ticket_id = await VKPayOrderService(session).handle_transaction(provider_body)
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return _vk_pay_ack(
        transaction_id,
        "payment_delivered" if order_status == "completed" else "payment_declined",
    )


# ═══════════════════════════════════════════════════════════════
# Tickets
# ═══════════════════════════════════════════════════════════════


@router.get("/tickets")
async def list_tickets(auth_data: dict = Depends(validate_init_data)):
    """Get user's tickets."""
    user_data = auth_data.get("user", {})
    platform_user_id = str(user_data.get("id", "0"))

    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=PlatformType(auth_data.get("platform", "telegram")),
            platform_user_id=platform_user_id,
            name=user_data.get("first_name", ""),
        )

        ticket_svc = TicketService(session)
        tickets = await ticket_svc.get_user_tickets(user.id)
        await session.commit()

    return tickets


@router.get("/tickets/{ticket_id}/qr")
async def buyer_ticket_qr(ticket_id: str, auth_data: dict = Depends(validate_init_data)):
    """PNG-картинка QR-кода СВОЕГО билета для покупателя.

    В отличие от /admin/tickets/{id}/qr (организатор, pro), этот эндпоинт
    доступен владельцу билета без подписки — билет всегда можно предъявить
    на входе из кабинета.
    """
    try:
        tid = UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID билета")

    user_data = auth_data.get("user", {})
    platform_user_id = str(user_data.get("id", "0"))

    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=PlatformType(auth_data.get("platform", "telegram")),
            platform_user_id=platform_user_id,
            name=user_data.get("first_name", ""),
        )

        ticket_svc = TicketService(session)
        pair = await ticket_svc.get_ticket_event(tid)
        if pair is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Билет не найден")
        ticket, _event = pair
        if ticket.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Это не ваш билет")

    code = ticket.validation_code or str(ticket.id)
    png = generate_qr_png(code)
    return StreamingResponse(
        iter([png]),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="ticket-{ticket.id}-qr.png"'},
    )


@router.post("/tickets/{ticket_id}/cancel")
async def cancel_ticket(ticket_id: str, auth_data: dict = Depends(validate_init_data)):
    """Cancel a ticket."""
    try:
        tid = UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID билета")

    user_data = auth_data.get("user", {})
    platform_user_id = str(user_data.get("id", "0"))

    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=PlatformType(auth_data.get("platform", "telegram")),
            platform_user_id=platform_user_id,
            name=user_data.get("first_name", ""),
        )

        ticket_svc = TicketService(session)
        try:
            ticket = await ticket_svc.cancel_ticket(tid, user.id)
            await session.commit()

            # Отправить уведомление о возврате в личные сообщения
            try:
                event = await session.get(Event, ticket.event_id)
                event_title = event.title if event else "—"
            except Exception:
                event_title = "—"
            await _send_ticket_dm(
                platform_user_id,
                f"↩️ <b>Билет возвращён</b>\n"
                f"🎫 {event_title}\n"
                f"🔑 Код: <code>{ticket.validation_code or '—'}</code>",
            )

            return {
                "ticket_id": str(ticket.id),
                "status": ticket.status.value,
                "event_id": str(ticket.event_id),
            }
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/tickets/{ticket_id}/send-vk")
async def send_vk_ticket(ticket_id: str, auth_data: dict = Depends(validate_init_data)):
    """Отправить билет владельцу в ЛС VK от имени группы организатора.

    Вызывается фронтендом ПОСЛЕ того, как покупатель разрешил сообщения
    от группы (VKWebAppAllowMessagesFromGroup). Best-effort: если нет группы
    с токеном или VK отклонил — sent=False, билет остаётся в кабинете.
    """
    try:
        tid = UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID билета")

    user_data = auth_data.get("user", {})
    platform_user_id = str(user_data.get("id", "0"))

    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=PlatformType(auth_data.get("platform", "telegram")),
            platform_user_id=platform_user_id,
            name=user_data.get("first_name", ""),
        )

        ticket_svc = TicketService(session)
        pair = await ticket_svc.get_ticket_event(tid)
        if pair is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Билет не найден")
        ticket, event = pair
        if ticket.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Это не ваш билет")

        # Группа организатора события (первая с community token)
        vk_group_id = None
        sent = False
        if event.owner_user_id is not None:
            group_svc = VKGroupService(session)
            groups = await group_svc.list_vk_groups(event.owner_user_id)
            vk_group = next((g for g in groups if g.community_token), None)
            if vk_group is not None:
                vk_group_id = vk_group.group_id
                # A: код в ЛС VK — только для бесплатного (платный предъявляется QR).
                code_line = ""
                if ticket.is_free:
                    code = ticket.validation_code or str(ticket.id)
                    code_line = f"\n🔑 Код: <code>{code}</code>"
                text = (
                    f"✅ Билет куплен!\n"
                    f"🎫 {event.title}\n"
                    f"📅 {event.date.isoformat()}{code_line}"
                )
                sent = await send_vk_ticket_dm(platform_user_id, text, vk_group)

    return {"sent": sent, "group_id": vk_group_id}


@router.post("/invites/{code}/claim")
async def claim_invite(code: str, auth_data: dict = Depends(validate_init_data)):
    """Активировать пригласительное гостем по коду из ссылки.

    Гость открывает ссылку ?invite=<код>, видит пригласительное и активирует.
    Пригласительное привязывается к гостю — появляется в его «Моих билетах».
    Места уже резервируются при выдаче (available -= seats).
    """
    user_data = auth_data.get("user", {})
    platform_user_id = str(user_data.get("id", "0"))
    name = user_data.get("first_name", "")

    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=PlatformType(auth_data.get("platform", "telegram")),
            platform_user_id=platform_user_id,
            name=name,
        )

        ticket_svc = TicketService(session)
        try:
            invite = await ticket_svc.claim_invite(code, user.id)
            event = await session.get(Event, invite.event_id)
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        "ticket_id": str(invite.id),
        "event_title": event.title if event else "—",
        "event_date": event.date.isoformat() if event else None,
        "validation_code": invite.validation_code,
        "seats": invite.seats,
        "status": invite.status.value,
    }


# ═══════════════════════════════════════════════════════════════
# VK Mini App
# ═══════════════════════════════════════════════════════════════


@router.get("/vk/me")
async def vk_me(user_id: str, user_name: str = ""):
    """VK Mini App: приветствие пользователя и регистрация в БД.

    Вызывается из VK Mini App после инициализации VK Bridge.
    """
    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_or_create(
            platform=PlatformType.vk,
            platform_user_id=user_id,
            name=user_name or None,
        )
        await session.commit()

        return {
            "user_id": user_id,
            "name": user_name or "Гость",
            "greeting": f"Привет, {user_name or 'Гость'}!",
            "platform": "vk",
            "registered": user is not None,
        }


# ═══════════════════════════════════════════════════════════════
# Личный кабинет
# ═══════════════════════════════════════════════════════════════


@router.get("/me")
async def get_me(current: CurrentUser = Depends(get_current_user)):
    """Профиль текущего пользователя: роль + каналы, где он админ."""
    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        # Telegram channels are not exposed in the VK Mini App profile.
        channels = (
            await channel_svc.get_channels_by_admin(current.telegram_user_id)
            if current.platform is PlatformType.telegram else []
        )
        # Subscription belongs to the authenticated platform identity.
        user_svc = UserService(session)
        user = await user_svc.get_by_platform_user_id(current.platform, current.telegram_user_id)

    result = {
        "id": str(current.user_id),
        "platform": current.platform.value,
        "platform_user_id": current.telegram_user_id,
        "name": current.name,
        "role": current.role,
        "is_super_admin": current.is_super_admin,
        "is_organizer": current.is_organizer,
        "has_group": current.has_group,
        "vk_group_ids": [str(i) for i in current.vk_group_ids],
        "subscription_tier": user.subscription_tier.value if user else None,
        "is_subscription_active": user.is_subscription_active if user else False,
        "subscription_until": user.subscription_until.isoformat() if user and user.subscription_until else None,
        "channels": [
            {
                "id": str(ch.id),
                "telegram_channel_id": ch.telegram_channel_id,
                "title": ch.title,
                "is_subscription_active": ch.is_subscription_active,
                "subscription_tier": ch.subscription_tier.value,
                "subscription_until": ch.subscription_until.isoformat() if ch.subscription_until else None,
            }
            for ch in channels
        ],
    }
    # Keep the legacy field for Telegram clients only; VK must not expose a
    # Telegram-labelled identity field in its profile contract.
    if current.platform is PlatformType.telegram:
        result["telegram_user_id"] = current.telegram_user_id
    return result




@router.patch("/me")
async def update_me(
    body: MeUpdateIn,
    current: CurrentUser = Depends(get_current_user),
):
    """Обновить имя пользователя в профиле."""
    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.update_name(current.user_id, body.name)
        await session.commit()
    return {"id": str(current.user_id), "name": user.name if user else None}


@router.delete("/me")
async def delete_me(current: CurrentUser = Depends(get_current_user)):
    """Удалить свой аккаунт и данные (п.1.1.10 Правил VK Mini Apps).

    Анонимизация профиля + деактивация подписки. Билеты сохраняются
    (нужны для входа на купленные мероприятия). Повторный вход создаёт
    новый (чистый) аккаунт.
    """
    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.delete_account(current.user_id)
        await session.commit()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return {"id": str(current.user_id), "deleted": True}


@router.post("/me/subscription")
async def subscribe_me(
    body: SubscribeMeIn,
    current: CurrentUser = Depends(get_current_user),
):
    """Покупка/активация подписки пользователя (факт покупки = активация функций).

    MVP: активация подписки без реальной оплаты. Выбор tier (basic/pro).
    """
    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.activate_subscription(
            current.user_id, days=30, tier=body.tier,
        )
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
        await session.commit()

    return {
        "subscription_tier": user.subscription_tier.value,
        "subscription_until": user.subscription_until.isoformat() if user.subscription_until else None,
    }


@router.post("/me/events/{event_id}/premium", status_code=status.HTTP_201_CREATED)
async def purchase_event_premium(
    event_id: str,
    body: EventPremiumIn | None = None,
    current: CurrentUser = Depends(get_current_user),
):
    """Купить премиум на одно мероприятие (единовременная оплата).

    Даёт pro-фичи (платные билеты, QR, пригласительные) для этого события,
    независимо от подписки. Stub-оплата (как подписка): сразу completed.
    """
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    body = body or EventPremiumIn()
    async with async_session_factory() as session:
        event_svc = EventService(session)
        try:
            upgrade = await event_svc.purchase_event_premium(
                uid, current.user_id, amount=body.amount, provider=body.provider,
            )
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        "event_id": str(uid),
        "is_premium": True,
        "status": upgrade.status.value,
        "expires_at": upgrade.expires_at.isoformat() if upgrade.expires_at else None,
    }


@router.post("/me/link-code")
async def create_me_link_code(
    body: LinkCodeIn,
    current: CurrentUser = Depends(require_admin),
):
    """Создать одноразовый код привязки площадки (organizer-only).

    Код показывается на TG-стороне («Привязать VK») и вводится на целевой
    площадке (VK Mini App), где привязывает identity к каноническому организатору.
    Одноразовый, короткоживущий (по умолчанию 10 минут).
    """
    async with async_session_factory() as session:
        user_svc = UserService(session)
        code = await user_svc.create_link_code(
            canonical_user_id=current.user_id,
            target_platform=body.target_platform,
            ttl_minutes=body.ttl_minutes,
        )
        await session.commit()

    return {
        "code": code,
        "target_platform": body.target_platform.value,
        "ttl_minutes": body.ttl_minutes,
    }


@router.get("/me/identities")
async def list_me_identities(current: CurrentUser = Depends(get_current_user)):
    """Список привязанных к кабинету площадок (TG/VK/...)."""
    async with async_session_factory() as session:
        user_svc = UserService(session)
        identities = await user_svc.list_identities(current.user_id)

    return [
        {
            "platform": i.platform.value,
            "platform_user_id": i.platform_user_id,
        }
        for i in identities
    ]


@router.post("/me/link", status_code=status.HTTP_201_CREATED)
async def link_me(
    body: LinkConsumeIn,
    auth_data: dict = Depends(validate_init_data),
    current: CurrentUser = Depends(get_current_user),
):
    """Ввести код привязки на целевой площадке (VK-сторона).

    Текущий VK-пользователь (аутентифицирован launch params + sign) привязывает
    свою VK-identity к каноническому организатору, которому принадлежит код.
    """
    if auth_data.get("platform") != "vk":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Привязка площадки доступна только из VK Mini App",
        )
    vk_user_id = str(auth_data["user"]["id"])

    async with async_session_factory() as session:
        user_svc = UserService(session)
        try:
            await user_svc.consume_link_code(
                code=body.code,
                platform=PlatformType.vk,
                platform_user_id=vk_user_id,
                current_user_id=current.user_id,
            )
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"linked": True, "platform": "vk"}


# ─── Мои каналы (самообслуживание) ────────────────────────────


def _channel_dict(ch) -> dict:
    """Сериализация канала для /api/me/channels."""
    return {
        "id": str(ch.id),
        "telegram_channel_id": ch.telegram_channel_id,
        "title": ch.title,
    }


@router.get("/me/channels")
async def list_my_channels(current: CurrentUser = Depends(get_current_user)):
    """Список каналов текущего пользователя (где он админ)."""
    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        channels = await channel_svc.get_channels_by_admin(current.telegram_user_id)

    return [_channel_dict(ch) for ch in channels]


@router.post("/me/channels")
async def register_my_channel(
    body: ChannelRegisterIn,
    current: CurrentUser = Depends(get_current_user),
):
    """Добавить Telegram-канал в свой кабинет.

    Канал создаётся без подписки (inactive). Когда бот будет добавлен в канал,
    my_chat_member обновит telegram_channel_id и синхронизирует админов.

    Returns 201 если канал создан, 200 если уже был привязан.
    """
    from fastapi.responses import JSONResponse

    telegram_channel_id = body.telegram_channel_id.strip()
    if not telegram_channel_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите @username или ID канала",
        )

    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        admin_svc = ChannelAdminService(session)

        channel = await channel_svc.get_by_telegram_id(telegram_channel_id)

        if channel is None:
            # Новый канал: создать и привязать пользователя как админа
            channel = await channel_svc.create(
                telegram_channel_id=telegram_channel_id,
                admin_telegram_user_id=current.telegram_user_id,
                title=body.title or f"Канал {telegram_channel_id}",
            )
            await admin_svc.sync_admins(channel.id, [current.telegram_user_id])
            await session.commit()
            logger.info("", extra={
                "event_type": "channel.self_registered",
                "channel_id": str(channel.id),
                "telegram_channel_id": telegram_channel_id,
                "user_id": current.telegram_user_id,
            })
            return JSONResponse(
                content=_channel_dict(channel),
                status_code=status.HTTP_201_CREATED,
            )

        # Канал уже существует — проверить что пользователь админ
        is_admin = await admin_svc.user_is_admin(channel.id, current.telegram_user_id)
        if is_admin:
            # Идемпотентно: канал уже привязан
            return _channel_dict(channel)

        # Защита от захвата: канал принадлежит другому пользователю
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Канал уже зарегистрирован. Добавьте бота в канал — он привяжет вас автоматически.",
        )


# ─── Мои VK-группы (самообслуживание целей публикации) ─────────


@router.get("/me/vk-groups")
async def list_my_vk_groups(current: CurrentUser = Depends(get_current_user)):
    """Список VK-групп организатора (цели публикации)."""
    async with async_session_factory() as session:
        group_svc = VKGroupService(session)
        groups = await group_svc.list_vk_groups(current.user_id)

    return [
        {
            "id": str(g.id),
            "group_id": g.group_id,
            "title": g.title,
            "has_token": bool(g.community_token),
        }
        for g in groups
    ]


@router.post("/me/vk-groups", status_code=status.HTTP_201_CREATED)
async def register_my_vk_group(
    body: VKGroupRegisterIn,
    current: CurrentUser = Depends(get_current_user),
):
    """Зарегистрировать VK-группу как цель публикации (community token шифруется)."""
    group_id = body.group_id.strip()
    if not group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите ID VK-группы",
        )

    # Верификация: если передан community token — он должен принадлежать этой группе.
    # Иначе организатор может привязать токен чужой группы и постить на её стену.
    if body.community_token:
        verified = await verify_group_token(group_id, body.community_token)
        if not verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Не удалось подтвердить токен для этой группы",
            )

    async with async_session_factory() as session:
        group_svc = VKGroupService(session)
        try:
            group = await group_svc.register_vk_group(
                owner_user_id=current.user_id,
                group_id=group_id,
                title=body.title,
                community_token=body.community_token,
            )
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        "id": str(group.id),
        "group_id": group.group_id,
        "title": group.title,
        "has_token": bool(group.community_token),
    }


@router.delete("/me/vk-groups/{group_id}")
async def remove_my_vk_group(
    group_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Удалить VK-группу организатора."""
    async with async_session_factory() as session:
        group_svc = VKGroupService(session)
        removed = await group_svc.remove_vk_group(current.user_id, group_id)
        await session.commit()

    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Группа не найдена")
    return {"group_id": group_id, "removed": True}


# ═══════════════════════════════════════════════════════════════
# Админ: мероприятия
# ═══════════════════════════════════════════════════════════════


@router.get("/admin/events")
async def admin_list_events(current: CurrentUser = Depends(require_admin)):
    """Список мероприятий организатора (свои каналы + owner-мероприятия).

    Матрица ролей: только организатор. Суперадмин НЕ управляет мероприятиями
    (пусто). Обычный пользователь — 403 (нет доступа к панели).
    """
    async with async_session_factory() as session:
        event_svc = EventService(session)
        channel_svc = ChannelService(session)
        if current.is_super_admin:
            events = []
        else:
            events = []
            # мероприятия по каналам организатора
            for cid in current.managed_channel_ids:
                events.extend(await event_svc.list_all(channel_id=cid))
            # мероприятия организатора без канала (по owner)
            events.extend(await event_svc.list_all(owner_user_id=current.user_id))

        # Карта каналов для названий (owner-события без канала — пропускаем None)
        channel_ids = {e.channel_id for e in events if e.channel_id is not None}
        channels = {}
        for cid in channel_ids:
            ch = await channel_svc.get_by_id(cid)
            if ch:
                channels[cid] = ch

        # C: карта per-event премиума
        premium_map = await event_svc.get_event_premium_map([e.id for e in events])

    return [
        {
            "id": str(e.id),
            "channel_id": str(e.channel_id) if e.channel_id else None,
            "channel_title": channels.get(e.channel_id).title if e.channel_id and e.channel_id in channels else None,
            "owner_user_id": str(e.owner_user_id) if e.owner_user_id else None,
            "title": e.title,
            "date": e.date.isoformat(),
            "location": e.location,
            "price": float(e.price),
            "total_tickets": e.total_tickets,
            "available_tickets": e.available_tickets,
            "is_active": e.is_active,
            "is_published": e.is_published,
            "is_free": e.is_free,
            "is_premium": premium_map.get(e.id, False),
            "age_restriction": e.age_restriction,
            "media_file_id": e.media_telegram_file_id,
            "media_type": e.media_type,
        }
        for e in events
    ]


@router.post("/admin/events", status_code=status.HTTP_201_CREATED)
async def admin_create_event(
    body: EventCreate,
    current: CurrentUser = Depends(get_current_user),
):
    """Создать мероприятие (черновик).

    Матрица ролей: только организатор (is_creator). Суперадмин эксклюзивен —
    не создаёт мероприятия. Обычный пользователь — 403.
    Мероприятие принадлежит каналу (если указан) ИЛИ организатору-пользователю.
    """
    if not current.is_creator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Создавать мероприятия может только организатор",
        )
    # Канальный путь: организатор должен управлять каналом
    if body.channel_id is not None and not current.can_manage(body.channel_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="У вас нет доступа к этому каналу",
        )
    # Путь организатора без канала: owner_user_id должен быть текущим пользователем
    if body.channel_id is None:
        if body.owner_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите канал или владельца мероприятия",
            )
        if not current.is_super_admin and body.owner_user_id != current.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Вы не можете создавать мероприятия от имени другого пользователя",
            )

    async with async_session_factory() as session:
        event_svc = EventService(session)
        try:
            event = await event_svc.create(
                title=body.title,
                description=body.description,
                date=body.date,
                location=body.location,
                price=body.price,
                total_tickets=body.total_tickets,
                channel_id=body.channel_id,
                invites_quota=body.invites_quota,
                owner_user_id=body.owner_user_id,
                age_restriction=body.age_restriction,
            )
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"id": str(event.id), "is_published": event.is_published}


@router.get("/admin/events/{event_id}")
async def admin_get_event(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Детали мероприятия (админ)."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        # Доступ: организатор-владелец ИЛИ супер-админ ИЛИ управляет каналом
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        channel_svc = ChannelService(session)
        channel = await channel_svc.get_by_id(event.channel_id) if event.channel_id else None
        # C: per-event премиум
        is_premium = await event_svc.get_event_is_premium(event.id)

    return {
        "id": str(event.id),
        "channel_id": str(event.channel_id) if event.channel_id else None,
        "owner_user_id": str(event.owner_user_id) if event.owner_user_id else None,
        "channel_title": channel.title if channel else None,
        "title": event.title,
        "description": event.description,
        "date": event.date.isoformat(),
        "location": event.location,
        "price": float(event.price),
        "total_tickets": event.total_tickets,
        "available_tickets": event.available_tickets,
        "is_active": event.is_active,
        "is_published": event.is_published,
        "is_free": event.is_free,
        "is_premium": is_premium,
        "age_restriction": event.age_restriction,
        "media_file_id": event.media_telegram_file_id,
        "media_type": event.media_type,
    }


@router.patch("/admin/events/{event_id}")
async def admin_update_event(
    event_id: str,
    body: EventUpdateIn,
    current: CurrentUser = Depends(get_current_user),
):
    """Частичное обновление мероприятия."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    data = body.model_dump(exclude_unset=True)

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        if not data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет полей для обновления")
        try:
            event = await event_svc.update(uid, **data)
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"id": str(uid), "updated": True}


@router.post("/admin/events/{event_id}/toggle")
async def admin_toggle_event(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Включить/отключить мероприятие."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        event = await event_svc.set_active(uid, not event.is_active)
        await session.commit()

    return {"id": str(uid), "is_active": event.is_active}


@router.post("/admin/events/{event_id}/delete")
async def admin_delete_event(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Мягко удалить мероприятие."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        await event_svc.soft_delete(uid)
        await session.commit()

    return {"id": str(uid), "deleted": True}


# ─── Админ: соработники мероприятия (несколько продавцов) ───────


@router.get("/admin/events/{event_id}/managers")
async def admin_list_managers(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Список соработников мероприятия."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        managers = await event_svc.list_managers(uid)

    return {
        "event_id": str(uid),
        "managers": [
            {
                "id": str(m.id),
                "name": m.name,
                "platform": m.platform.value,
                "platform_user_id": m.platform_user_id,
            }
            for m in managers
        ],
    }


@router.post("/admin/events/{event_id}/managers", status_code=status.HTTP_201_CREATED)
async def admin_add_manager(
    event_id: str,
    body: AddManagerIn,
    current: CurrentUser = Depends(get_current_user),
):
    """Добавить соработника по платформенному ID (TG ID / VK ID).

    Резолвится в канонического организатора через user_identities.
    Право: владелец события или супер-админ (не соработник).
    """
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Управлять соработниками может только владелец")

        # Резолв канонического пользователя по platform + platform_user_id
        user_svc = UserService(session)
        user = await user_svc.get_by_platform_user_id(body.platform, body.platform_user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден на этой площадке")
        try:
            await event_svc.add_manager(uid, user.id)
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        "event_id": str(uid),
        "manager": {"id": str(user.id), "name": user.name},
    }


@router.delete("/admin/events/{event_id}/managers/{user_id}")
async def admin_remove_manager(
    event_id: str,
    user_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Убрать соработника мероприятия."""
    try:
        uid = UUID(event_id)
        manager_uid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Управлять соработниками может только владелец")
        removed = await event_svc.remove_manager(uid, manager_uid)
        await session.commit()

    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Соработник не найден")
    return {"event_id": str(uid), "removed": True}


@router.post("/admin/events/{event_id}/publish")
async def admin_publish_event(
    event_id: str,
    body: PublishIn | None = None,
    current: CurrentUser = Depends(get_current_user),
):
    """Опубликовать мероприятие + отправить анонс в цель.

    Цель: TG-канал (channel_id) или VK-группа (vk_group_id). Мульти-публикация:
    одно событие может быть опубликовано в N мест (записи event_publications).
    Ошибка анонса не откатывает флаг публикации.
    """
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    if body is None:
        body = PublishIn()
    if body.channel_id and body.vk_group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите одну цель публикации: канал или VK-группу",
        )

    # ─── VK-группа (стена) ───────────────────────────────────────
    if body.vk_group_id:
        async with async_session_factory() as session:
            event_svc = EventService(session)
            group_svc = VKGroupService(session)
            event = await event_svc.get_by_id(uid)
            if event is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
            if not _can_manage_event(current, event):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")

            group = await group_svc.get_by_group_id(body.vk_group_id.strip())
            if group is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VK-группа не найдена")
            if group.owner_user_id != current.user_id and not current.is_super_admin:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа к этой группе")

            # B: лимит free-организатора «1 опубликованное будущее»
            try:
                await event_svc.ensure_free_slot(event)
            except ValueError as e:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

            await event_svc.update(uid, is_published=True)
            text = format_event_text(event, mode="full")
            ok = await post_to_group_wall(group, text)
            await event_svc.add_publication(
                event_id=uid,
                platform=PlatformType.vk,
                target_type="vk_group_wall",
                target_id=group.group_id,
                created_by=current.user_id,
                status="posted" if ok else "error",
                last_error=None if ok else "VK wall.post failed",
            )
            await session.commit()

        return {
            "id": str(uid),
            "is_published": True,
            "announced": ok,
            "platform": "vk",
            "group_id": group.group_id,
        }

    # ─── TG-канал / DM (как было) ────────────────────────────────
    target_channel_id: UUID | None = None
    if body.channel_id:
        target_channel_id = body.channel_id

    async with async_session_factory() as session:
        event_svc = EventService(session)
        channel_svc = ChannelService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")

        # B: лимит free-организатора «1 опубликованное будущее»
        try:
            await event_svc.ensure_free_slot(event)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

        channel = None
        if target_channel_id:
            if not current.can_manage(target_channel_id) and not current.is_super_admin:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа к этому каналу")
            await event_svc.update(uid, channel_id=target_channel_id, is_published=True)
            channel = await channel_svc.get_by_id(target_channel_id)
        else:
            await event_svc.update(uid, is_published=True)
        await session.commit()

    announced = await post_event_announcement(uid)
    dm_sent = False
    if not announced:
        dm_sent = await send_announcement_dm(uid, current.telegram_user_id)

    # Запись публикации (TG-канал, если анонс ушёл в канал)
    if announced and channel is not None:
        async with async_session_factory() as session:
            event_svc = EventService(session)
            await event_svc.add_publication(
                event_id=uid,
                platform=PlatformType.telegram,
                target_type="telegram_channel",
                target_id=channel.telegram_channel_id,
                created_by=current.user_id,
                status="posted",
            )
            await session.commit()

    return {
        "id": str(uid),
        "is_published": True,
        "announced": announced,
        "dm_sent": dm_sent,
    }


@router.get("/admin/events/{event_id}/publications")
async def admin_list_publications(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Список публикаций мероприятия (куда опубликовано: TG-каналы / VK-группы)."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        pubs = await event_svc.list_publications(uid)

    return {
        "event_id": str(uid),
        "publications": [
            {
                "id": str(p.id),
                "platform": p.platform.value,
                "target_type": p.target_type,
                "target_id": p.target_id,
                "status": p.status,
                "last_error": p.last_error,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in pubs
        ],
    }


@router.delete("/admin/events/{event_id}/publications/{publication_id}")
async def admin_remove_publication(
    event_id: str,
    publication_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Удалить запись публикации (не отменяет отправленный анонс)."""
    try:
        uid = UUID(event_id)
        pid = UUID(publication_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        removed = await event_svc.remove_publication(pid)
        await session.commit()

    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Публикация не найдена")
    return {"publication_id": str(pid), "removed": True}


@router.post("/admin/events/{event_id}/repost")
async def admin_repost_event(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Переотправить анонс в канал."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")

    announced = await post_event_announcement(uid)
    return {"id": str(uid), "announced": announced}


@router.get("/admin/events/{event_id}/stats")
async def admin_event_stats(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Статистика продаж мероприятия (всем админам, без тарифного гейта)."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        stats = await event_svc.get_event_stats(uid)

    return {"event_id": str(uid), **stats}


@router.get("/admin/events/{event_id}/tickets")
async def admin_event_tickets(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Список билетов на мероприятие (админ)."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        ticket_svc = TicketService(session)
        tickets = await ticket_svc.get_event_tickets(uid)

    return {"event_id": str(uid), "tickets": tickets}


@router.get("/admin/events/{event_id}/tickets.csv")
async def admin_event_tickets_csv(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Экспорт билетов на мероприятие в CSV."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        ticket_svc = TicketService(session)
        tickets = await ticket_svc.export_event_tickets(uid)

    if not tickets:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет билетов для экспорта")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(tickets[0].keys()))
    writer.writeheader()
    writer.writerows(tickets)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="event-{uid}-tickets.csv"'},
    )


# ═══════════════════════════════════════════════════════════════
# Админ: билеты и вход
# ═══════════════════════════════════════════════════════════════


@router.get("/admin/tickets/validate")
async def admin_validate_ticket(
    code: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Проверить билет по коду (без отметки входа).

    Доступ: проверяющий должен управлять мероприятием билета
    (owner/manager/channel-admin), либо суперадмин (поиск покупателя —
    глобальная операция, без права управления событием). Иначе 403.
    """
    async with async_session_factory() as session:
        ticket_svc = TicketService(session)
        result = await ticket_svc.validate_ticket(_normalize_ticket_code(code))

        if result.get("found") and result.get("event_id"):
            event_svc = EventService(session)
            event = await event_svc.get_by_id(UUID(result["event_id"]))
            if event is not None and not current.is_super_admin and not _can_manage_event(current, event):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа к этому билету")
    return result


@router.post("/admin/tickets/checkin")
async def admin_checkin_ticket(
    body: CheckInIn,
    current: CurrentUser = Depends(get_current_user),
):
    """Отметить вход по коду билета."""
    code = _normalize_ticket_code(body.code)

    async with async_session_factory() as session:
        ticket_svc = TicketService(session)
        try:
            ticket = await ticket_svc.check_in_by_code(code, current.telegram_user_id)
            # Проверка доступа: организатор должен управлять мероприятием билета
            event_svc = EventService(session)
            event = await event_svc.get_by_id(ticket.event_id)
            if event is not None and not _can_manage_event(current, event):
                await session.rollback()
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа к этому билету")
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        "ok": True,
        "ticket_id": str(ticket.id),
        "status": ticket.status.value,
        "event_id": str(ticket.event_id),
        "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
    }


@router.post("/admin/tickets/{ticket_id}/cancel")
async def admin_cancel_ticket(
    ticket_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Отменить билет (админ). Канальный доступ по мероприятию билета."""
    try:
        tid = UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID билета")

    async with async_session_factory() as session:
        ticket_svc = TicketService(session)
        pair = await ticket_svc.get_ticket_event(tid)
        if pair is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Билет не найден")
        ticket, event = pair
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        try:
            ticket = await ticket_svc.admin_cancel_ticket(tid)
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"ticket_id": str(tid), "status": ticket.status.value}


# ═══════════════════════════════════════════════════════════════
# Админ: каналы и подписки (super-admin)
# ═══════════════════════════════════════════════════════════════


@router.get("/admin/channels")
async def admin_list_channels(current: CurrentUser = Depends(require_super_admin)):
    """Список всех каналов со статусом подписки и админами."""
    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        admin_svc = ChannelAdminService(session)
        channels = await channel_svc.list_all()
        admins_by_channel = {
            str(ch.id): await admin_svc.get_admin_ids(ch.id)
            for ch in channels
        }

    return [
        {
            "id": str(ch.id),
            "telegram_channel_id": ch.telegram_channel_id,
            "title": ch.title,
            "is_subscription_active": ch.is_subscription_active,
            "subscription_tier": ch.subscription_tier.value,
            "subscription_until": ch.subscription_until.isoformat() if ch.subscription_until else None,
            "admins": admins_by_channel.get(str(ch.id), []),
        }
        for ch in channels
    ]


@router.get("/admin/channels/{channel_id}")
async def admin_channel_info(
    channel_id: str,
    current: CurrentUser = Depends(require_super_admin),
):
    """Детальная информация о канале."""
    try:
        cid = UUID(channel_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID канала")

    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        summary = await channel_svc.get_channel_summary(cid)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Канал не найден")
    return summary


@router.post("/admin/channels/{channel_id}/subscribe")
async def admin_subscribe(
    channel_id: str,
    body: SubscribeIn,
    current: CurrentUser = Depends(require_super_admin),
):
    """Активировать подписку каналу."""
    try:
        cid = UUID(channel_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID канала")

    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        channel = await channel_svc.activate_subscription(
            cid,
            duration_days=body.duration_days,
            tier=body.tier,
        )
        if channel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Канал не найден")
        await session.commit()

    return {
        "channel_id": str(cid),
        "is_subscription_active": channel.is_subscription_active,
        "subscription_tier": channel.subscription_tier.value,
        "subscription_until": channel.subscription_until.isoformat() if channel.subscription_until else None,
    }


@router.post("/admin/channels/{channel_id}/unsubscribe")
async def admin_unsubscribe(
    channel_id: str,
    current: CurrentUser = Depends(require_super_admin),
):
    """Отключить подписку канала."""
    try:
        cid = UUID(channel_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID канала")

    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        channel = await channel_svc.deactivate_subscription(cid)
        if channel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Канал не найден")
        await session.commit()

    return {"channel_id": str(cid), "is_subscription_active": False}


@router.post("/admin/channels/{channel_id}/change_admin")
async def admin_change_admin(
    channel_id: str,
    body: ChangeAdminIn,
    current: CurrentUser = Depends(require_super_admin),
):
    """Сменить администратора канала."""
    try:
        cid = UUID(channel_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID канала")

    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        channel = await channel_svc.get_by_id(cid)
        if channel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Канал не найден")
        try:
            _, old_admins = await channel_svc.change_admin(
                channel.telegram_channel_id,
                body.new_admin_id,
            )
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"channel_id": str(cid), "new_admin_id": body.new_admin_id, "old_admins": old_admins}


@router.post("/admin/channels/check_expired")
async def admin_check_expired(current: CurrentUser = Depends(require_super_admin)):
    """Проверить и деактивировать просроченные подписки."""
    async with async_session_factory() as session:
        from sqlalchemy import select as _select
        from app.core.models import Channel as _Channel

        result = await session.execute(_select(_Channel).where(_Channel.is_subscription_active == True))
        channels = list(result.scalars().all())
        channel_svc = ChannelService(session)
        deactivated = 0
        for ch in channels:
            if not await channel_svc.is_subscription_valid(ch.id):
                deactivated += 1
        await session.commit()

    return {"checked": len(channels), "deactivated": deactivated}


@router.post("/admin/channels/{channel_id}/subscription")
async def admin_update_subscription(
    channel_id: str,
    body: UpdateSubscriptionIn,
    current: CurrentUser = Depends(require_super_admin),
):
    """Сменить подписку канала: тип + срок (дни/месяцы/годы)."""
    try:
        cid = UUID(channel_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID канала")

    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        channel = await channel_svc.change_subscription(
            cid,
            tier=body.tier,
            period=body.period,
            period_unit=body.period_unit,
        )
        if channel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Канал не найден")
        await session.commit()

    return {
        "channel_id": str(cid),
        "subscription_tier": channel.subscription_tier.value,
        "subscription_until": channel.subscription_until.isoformat() if channel.subscription_until else None,
    }


@router.post("/admin/channels/{channel_id}/tier")
async def admin_change_tier(
    channel_id: str,
    body: ChangeTierIn,
    current: CurrentUser = Depends(require_super_admin),
):
    """Сменить только тип подписки канала (срок не меняется)."""
    try:
        cid = UUID(channel_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID канала")

    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        channel = await channel_svc.change_tier(cid, tier=body.tier)
        if channel is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Канал не найден")
        await session.commit()

    return {
        "channel_id": str(cid),
        "subscription_tier": channel.subscription_tier.value,
    }


# ═══════════════════════════════════════════════════════════════
# Админ: общая статистика (super-admin)
# ═══════════════════════════════════════════════════════════════


@router.get("/admin/stats")
async def admin_global_stats(current: CurrentUser = Depends(require_super_admin)):
    """Общая статистика по всем каналам/мероприятиям/билетам."""
    async with async_session_factory() as session:
        stats_svc = StatsService(session)
        stats = await stats_svc.get_global_stats()
    return stats


# ═══════════════════════════════════════════════════════════════
# Админ: создать канал + подписка, инфо о пользователе,
#        рассылка, здоровье (super-admin)
# ═══════════════════════════════════════════════════════════════


@router.post("/admin/channels", status_code=status.HTTP_201_CREATED)
async def admin_create_channel(
    body: ChannelSubscribeIn,
    current: CurrentUser = Depends(require_super_admin),
):
    """Создать канал (если нет в БД) и активировать подписку.

    Зеркалит бота admin_subscribe: по @username/ID создаёт канал,
    если он ещё не зарегистрирован, затем активирует подписку.
    """
    async with async_session_factory() as session:
        channel_svc = ChannelService(session)
        try:
            channel = await channel_svc.get_by_telegram_id(body.telegram_channel_id)
            if channel is None:
                channel = await channel_svc.create(
                    telegram_channel_id=body.telegram_channel_id,
                    admin_telegram_user_id="",
                    title=body.title or f"Канал {body.telegram_channel_id}",
                )
            channel = await channel_svc.activate_subscription(
                channel.id,
                duration_days=body.duration_days,
                tier=body.tier,
            )
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        "channel_id": str(channel.id),
        "telegram_channel_id": channel.telegram_channel_id,
        "is_subscription_active": channel.is_subscription_active,
        "subscription_tier": channel.subscription_tier.value,
        "subscription_until": channel.subscription_until.isoformat() if channel.subscription_until else None,
    }


@router.get("/admin/users/{telegram_user_id}")
async def admin_user_info(
    telegram_user_id: str,
    current: CurrentUser = Depends(require_super_admin),
):
    """Информация о пользователе по Telegram ID или @username (без создания).

    Если вход числовой — по platform_user_id; иначе (ник) — по username.
    Зеркалит бота sa_user_info, но без side-effect get_or_create.
    """
    async with async_session_factory() as session:
        user_svc = UserService(session)
        is_numeric = telegram_user_id.strip().lstrip("-").isdigit()
        if is_numeric:
            user = await user_svc.get_by_platform_user_id(PlatformType.telegram, telegram_user_id)
        else:
            user = await user_svc.get_by_username(telegram_user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
        channel_svc = ChannelService(session)
        channels = await channel_svc.get_channels_by_admin(user.platform_user_id)

    return {
        "id": str(user.id),
        "telegram_user_id": user.platform_user_id,
        "username": user.username,
        "name": user.name,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "is_subscription_active": user.is_subscription_active,
        "subscription_tier": user.subscription_tier.value if user.subscription_tier else None,
        "subscription_until": user.subscription_until.isoformat() if user.subscription_until else None,
        "channels": [
            {
                "id": str(ch.id),
                "telegram_channel_id": ch.telegram_channel_id,
                "title": ch.title,
                "is_subscription_active": ch.is_subscription_active,
                "subscription_tier": ch.subscription_tier.value,
            }
            for ch in channels
        ],
    }


@router.post("/admin/users/{telegram_user_id}/subscription")
async def admin_user_subscribe(
    telegram_user_id: str,
    body: SubscribeIn,
    current: CurrentUser = Depends(require_super_admin),
):
    """Активировать/продлить подписку организатора без канала (по Telegram ID, суперадмин).

    Аналог канальной подписки (/admin/channels/{id}/subscribe), но для
    пользователя: суперадмин выдаёт подписку организатору напрямую.
    """
    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_by_platform_user_id(PlatformType.telegram, telegram_user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
        user = await user_svc.activate_subscription(
            user.id, days=body.duration_days, tier=body.tier,
        )
        await session.commit()

    return {
        "telegram_user_id": telegram_user_id,
        "subscription_tier": user.subscription_tier.value,
        "subscription_until": user.subscription_until.isoformat() if user.subscription_until else None,
    }


@router.get("/admin/users")
async def admin_list_users(current: CurrentUser = Depends(require_super_admin)):
    """Список всех пользователей (не удалённых)."""
    async with async_session_factory() as session:
        user_svc = UserService(session)
        users = await user_svc.list_all()

    return [
        {
            "id": str(u.id),
            "telegram_user_id": u.platform_user_id,
            "name": u.name,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "is_subscription_active": u.is_subscription_active,
            "subscription_tier": u.subscription_tier.value,
        }
        for u in users
    ]


@router.delete("/admin/users/{telegram_user_id}")
async def admin_delete_user(
    telegram_user_id: str,
    current: CurrentUser = Depends(require_super_admin),
):
    """Мягкое удаление пользователя (super-admin only)."""
    async with async_session_factory() as session:
        user_svc = UserService(session)
        user = await user_svc.get_by_platform_user_id(PlatformType.telegram, telegram_user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
        if user.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Пользователь уже удалён")
        await user_svc.soft_delete(user.id)
        await session.commit()

    return {"id": str(user.id), "deleted": True}


@router.post("/admin/broadcast")
async def admin_broadcast(
    body: BroadcastIn,
    current: CurrentUser = Depends(require_super_admin),
):
    """Разослать сообщение во все активные каналы."""
    if not body.text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Сообщение не может быть пустым")
    sent, total = await send_broadcast(body.text.strip())
    return {"sent": sent, "total": total}


@router.get("/admin/health")
async def admin_health(current: CurrentUser = Depends(require_super_admin)):
    """Здоровье бота: статус, username, БД."""
    from sqlalchemy import text as sqltext

    db_ok = False
    async with async_session_factory() as session:
        try:
            await session.execute(sqltext("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False

    bot_username = None
    bot = get_telegram_bot()
    if bot is not None:
        try:
            me = await bot.get_me()
            bot_username = me.username
        except Exception:
            bot_username = None

    return {
        "status": "ok" if db_ok else "degraded",
        "bot_username": bot_username,
        "db_ok": db_ok,
    }


# ═══════════════════════════════════════════════════════════════
# Админ: пригласительные (pro, только админ канала)
# ═══════════════════════════════════════════════════════════════


def _normalize_ticket_code(raw: str) -> str:
    """Привести код билета к каноническому виду XXXX-XXXX.

    AB3XK7M9 (8 символов без дефиса) → AB3X-K7M9. Используется и в validate,
    и в checkin — единый источник нормализации.
    """
    code = (raw or "").strip().upper()
    if len(code) == 8 and "-" not in code:
        code = f"{code[:4]}-{code[4:]}"
    return code


def _can_manage_event(current: CurrentUser, event) -> bool:
    """Доступ к продажам мероприятия: владелец (owner), соработник (manager)
    или организатор канала. Суперадмин эксклюзивен — НЕ управляет мероприятиями."""
    if current.is_super_admin:
        return False
    # Владелец (owner-событие) — даже если event привязан к каналу
    if event.owner_user_id == current.user_id:
        return True
    # Соработник (несколько продавцов на одном мероприятии)
    if current.can_manage_event(event.id):
        return True
    # Канал — организатор управляет мероприятиями своего канала
    if event.channel_id is not None:
        return current.can_manage(event.channel_id)
    return False


def _can_admin_event(current: CurrentUser, event) -> bool:
    """Доступ к управлению мероприятием (редактирование/удаление/менеджеры):
    владелец (owner) или организатор канала. Суперадмин эксклюзивен — НЕ управляет;
    менеджер — только продажи."""
    if current.is_super_admin:
        return False
    if event.owner_user_id == current.user_id:
        return True
    if event.channel_id is not None:
        return current.can_manage(event.channel_id)
    return False


def _can_issue_invites(current: CurrentUser, event) -> bool:
    """Правило: пригласительные выдаёт организатор (не суперадмин).

    Соработник (manager), канальный организатор или владелец (owner).
    Pro-подписка проверяется в эндпоинте.
    """
    if current.is_super_admin:
        return False
    if current.can_manage_event(event.id):
        return True
    if event.channel_id is not None:
        return event.channel_id in current.managed_channel_ids
    return event.owner_user_id == current.user_id


@router.post("/admin/events/{event_id}/invites", status_code=status.HTTP_201_CREATED)
async def admin_issue_invite(
    event_id: str,
    body: InviteIssueIn,
    current: CurrentUser = Depends(get_current_user),
):
    """Выдать пригласительное (только админ канала, pro-подписка)."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_issue_invites(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Пригласительные выдаёт только админ канала")

        # Pro-гейт: подписка канала/владельца ИЛИ per-event премиум
        has_feature = await event_svc.has_event_pro_feature(uid, "invite_tickets")
        if not has_feature:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Для пригласительных нужна подписка Pro или премиум на событие")

        ticket_svc = TicketService(session)
        try:
            invite = await ticket_svc.issue_invite(uid, seats=body.seats, issued_by=current.telegram_user_id)
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        "ticket_id": str(invite.id),
        "validation_code": invite.validation_code,
        "seats": invite.seats,
        "status": invite.status.value,
    }


@router.post("/admin/events/{event_id}/invites/{ticket_id}/cancel")
async def admin_cancel_invite(
    event_id: str,
    ticket_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Отменить пригласительное (вернуть места)."""
    try:
        uid = UUID(event_id)
        tid = UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_issue_invites(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Пригласительные управляет админ канала")

        ticket_svc = TicketService(session)
        try:
            invite = await ticket_svc.cancel_invite(tid)
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"ticket_id": str(tid), "status": invite.status.value}


@router.get("/admin/events/{event_id}/invites")
async def admin_list_invites(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Список пригласительных по мероприятию."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_issue_invites(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Пригласительные управляет админ канала")
        ticket_svc = TicketService(session)
        invites = await ticket_svc.get_event_invites(uid)

    return {"event_id": str(uid), "invites": invites}


# ─── Промокоды (скидки на билеты, pro) ─────────────────────────

@router.post("/admin/events/{event_id}/promo-codes", status_code=status.HTTP_201_CREATED)
async def admin_create_promo(
    event_id: str,
    body: PromoCodeCreate,
    current: CurrentUser = Depends(get_current_user),
):
    """Создать промокод для мероприятия (владелец/админ канала, pro-фича)."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Промокоды управляет владелец мероприятия")

        has_feature = await event_svc.has_event_pro_feature(uid, "promo_codes")
        if not has_feature:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Промокоды доступны на подписке Pro или премиуме события")

        ticket_svc = TicketService(session)
        try:
            promo = await ticket_svc.create_promo_code(
                event_id=uid,
                code=body.code,
                discount_type=body.discount_type,
                discount_value=body.discount_value,
                starts_at=body.starts_at,
                ends_at=body.ends_at,
                max_uses=body.max_uses,
            )
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        "id": str(promo.id),
        "code": promo.code,
        "discount_type": promo.discount_type.value,
        "discount_value": float(promo.discount_value),
        "starts_at": promo.starts_at.isoformat() if promo.starts_at else None,
        "ends_at": promo.ends_at.isoformat() if promo.ends_at else None,
        "max_uses": promo.max_uses,
        "used_count": promo.used_count,
        "is_active": promo.is_active,
        "created_at": promo.created_at.isoformat(),
    }


@router.get("/admin/events/{event_id}/promo-codes")
async def admin_list_promos(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Список промокодов мероприятия."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Промокоды управляет владелец мероприятия")
        ticket_svc = TicketService(session)
        promos = await ticket_svc.list_promo_codes(uid)

    return {"event_id": str(uid), "promo_codes": promos}


@router.post("/admin/promo-codes/{code_id}/toggle")
async def admin_toggle_promo(
    code_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Вкл/выкл промокода (ручная деактивация, владелец мероприятия)."""
    try:
        cid = UUID(code_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID промокода")

    async with async_session_factory() as session:
        ticket_svc = TicketService(session)
        promo = await ticket_svc.get_promo_code_by_id(cid)
        if promo is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Промокод не найден")

        event_svc = EventService(session)
        event = await event_svc.get_by_id(promo.event_id)
        if event is None or not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Промокоды управляет владелец мероприятия")
        has_feature = await event_svc.has_event_pro_feature(promo.event_id, "promo_codes")
        if not has_feature:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Промокоды доступны на подписке Pro или премиуме события")

        toggled = await ticket_svc.toggle_promo_code(cid)
        await session.commit()

    return {"id": str(toggled.id), "is_active": toggled.is_active}


# ─── Динамические цены по дате (pro) ──────────────────────────

@router.put("/admin/events/{event_id}/price-ranges")
async def admin_replace_price_ranges(
    event_id: str,
    body: PriceRangesUpdate,
    current: CurrentUser = Depends(get_current_user),
):
    """Заменить весь набор ценовых диапазонов (владелец/админ канала, pro).

    Пустой список — выключить динамику. 409 при «дырах»/пересечениях/бесплатном событии.
    """
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Цены по дате управляет владелец мероприятия")
        has_feature = await event_svc.has_event_pro_feature(uid, "dynamic_pricing")
        if not has_feature:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Цены по дате доступны на подписке Pro или премиуме события")

        try:
            result = await event_svc.replace_price_ranges(uid, [r.model_dump() for r in body.ranges])
            await session.commit()
        except ValueError as e:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"event_id": str(uid), "price_ranges": result}


@router.get("/admin/events/{event_id}/price-ranges")
async def admin_get_price_ranges(
    event_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """Список ценовых диапазонов мероприятия (админ)."""
    try:
        uid = UUID(event_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID мероприятия")

    async with async_session_factory() as session:
        event_svc = EventService(session)
        event = await event_svc.get_by_id(uid)
        if event is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Мероприятие не найдено")
        if not _can_admin_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Цены по дате управляет владелец мероприятия")
        ranges = await event_svc.get_price_ranges(uid)

    return {"event_id": str(uid), "price_ranges": ranges}


@router.get("/admin/tickets/{ticket_id}/qr")
async def admin_ticket_qr(
    ticket_id: str,
    current: CurrentUser = Depends(get_current_user),
):
    """PNG-картинка QR-кода билета/пригласительного."""
    try:
        tid = UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный ID билета")

    async with async_session_factory() as session:
        ticket_svc = TicketService(session)
        pair = await ticket_svc.get_ticket_event(tid)
        if pair is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Билет не найден")
        ticket, event = pair
        if not _can_manage_event(current, event):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет доступа")
        event_svc = EventService(session)

        # QR-коды — фича pro (подписка ИЛИ per-event премиум)
        has_qr = await event_svc.has_event_pro_feature(event.id, "qr_codes")
        if not has_qr:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="QR-коды доступны на подписке Pro или премиуме события")

    code = ticket.validation_code or str(ticket.id)
    png = generate_qr_png(code)
    return StreamingResponse(
        iter([png]),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="ticket-{ticket.id}-qr.png"'},
    )
