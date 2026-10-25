"""
Pytest conftest — общие фикстуры для тестов TicketBot.

Фикстуры:
  db_session  — асинхронная сессия с rollback после каждого теста
                (использует SAVEPOINT + перехват commit)
  user_svc    — UserService
  event_svc   — EventService
  ticket_svc  — TicketService
  sample_event — создаёт тестовое мероприятие
"""

import os
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator

# Тестовая среда явно разрешает X-Skip-Auth (обход аутентификации под id=12345).
# Флаг читается app.config при импорте, поэтому ставим ДО любых import app.*.
# В production (деплой) флаг не задан → settings.allow_skip_auth=False → бэкдор закрыт.
os.environ.setdefault("ALLOW_SKIP_AUTH", "true")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool

from app.core.models import Base, User, Event, Ticket, Channel
from app.core.models import PlatformType, SubscriptionTier
from app.core.services import UserService, EventService, TicketService, ChannelService


# ─── Настройка тестовой БД ─────────────────────────────────────

# Пароль берём из DB_PASSWORD_VK (как на деплое VK), фолбэк postgres для локальной разработки
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://postgres:{os.getenv('DB_PASSWORD_VK', 'postgres')}@localhost:5432/ticketbot_test",
)


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Создаёт тестовый движок и все таблицы."""
    engine = create_async_engine(TEST_DB_URL, echo=False, poolclass=NullPool)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        pytest.skip("Тестовая БД недоступна. Запустите PostgreSQL.")

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Session bound to an outer transaction, rolled back after the test.

    SQLAlchemy's ``create_savepoint`` join mode keeps explicit ``commit()``
    calls inside the fixture's external transaction without monkey-patching
    Session internals. The outer rollback discards all test data.
    """
    connection = await test_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        await session.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def db_client(db_session: AsyncSession):
    """Async HTTP-клиент FastAPI с патчем async_session_factory на db_session.

    Использует httpx.AsyncClient + ASGITransport — тот же event loop, что и
    db_session (SAVEPOINT-изоляция conftest работает). Роуты и зависимости
    обращаются к реальной тестовой БД: сквозной web-flow в одном сценарии.
    """
    import httpx
    from unittest.mock import AsyncMock, patch
    from app.web.server import create_app

    app = create_app()

    # Фабрика, отдающая одну и ту же db_session
    class _SessionCM:
        def __init__(self, session):
            self._session = session
        async def __aenter__(self):
            return self._session
        async def __aexit__(self, *exc):
            return False

    class _OneSessionFactory:
        def __call__(self):
            return _SessionCM(db_session)

    factory = _OneSessionFactory()

    # В тестах get_session() отдаёт db_session напрямую (без close).
    # FastAPI резолвит Depends(get_session) один раз на запрос.
    async def _test_get_session():
        yield db_session

    with (
        patch("app.web.routes.async_session_factory", factory),
        patch("app.web.dependencies.async_session_factory", factory),
        patch("app.web.server.init_db", new_callable=AsyncMock),
        patch("app.web.server.close_db", new_callable=AsyncMock),
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ─── Фабрики моделей ────────────────────────────────────────────

@pytest_asyncio.fixture
async def sample_channel(db_session: AsyncSession) -> Channel:
    """Создаёт тестовый канал с активной Pro-подпиской."""
    svc = ChannelService(db_session)
    channel = await svc.create(
        telegram_channel_id="test_channel_1",
        admin_telegram_user_id="test_12345",
        title="Test Channel",
    )
    channel = await svc.activate_subscription(
        channel.id, duration_days=365, tier=SubscriptionTier.pro,
    )
    await db_session.flush()
    return channel


@pytest_asyncio.fixture
async def basic_channel(db_session: AsyncSession) -> Channel:
    """Создаёт тестовый канал с Basic-подпиской (только бесплатные мероприятия)."""
    svc = ChannelService(db_session)
    channel = await svc.create(
        telegram_channel_id="test_basic_channel",
        admin_telegram_user_id="test_basic_admin",
        title="Test Basic Channel",
    )
    channel = await svc.activate_subscription(
        channel.id, duration_days=365, tier=SubscriptionTier.basic,
    )
    await db_session.flush()
    return channel


@pytest_asyncio.fixture
async def sample_user(db_session: AsyncSession) -> User:
    """Создаёт тестового пользователя."""
    svc = UserService(db_session)
    user = await svc.get_or_create(
        platform=PlatformType.telegram,
        platform_user_id="test_12345",
        name="Test User",
    )
    return user


@pytest_asyncio.fixture
async def sample_event(db_session: AsyncSession, sample_channel: Channel) -> Event:
    """Создаёт тестовое мероприятие."""
    svc = EventService(db_session)
    event = await svc.create(
        title="Тестовое мероприятие",
        description="Описание тестового мероприятия",
        date=datetime.now(timezone.utc) + timedelta(days=14),
        location="Москва",
        price=1000.0,
        total_tickets=100,
        channel_id=sample_channel.id,
    )
    event.is_published = True
    await db_session.flush()
    return event


@pytest_asyncio.fixture
async def sample_past_event(db_session: AsyncSession, sample_channel: Channel) -> Event:
    """Создаёт прошедшее мероприятие."""
    svc = EventService(db_session)
    event = await svc.create(
        title="Прошедшее мероприятие",
        description="Уже прошло",
        date=datetime.now(timezone.utc) - timedelta(days=1),
        location="Москва",
        price=500.0,
        total_tickets=10,
        channel_id=sample_channel.id,
    )
    event.is_published = True
    await db_session.flush()
    return event


@pytest_asyncio.fixture
async def sample_free_event(db_session: AsyncSession, basic_channel: Channel) -> Event:
    """Создаёт тестовое БЕСПЛАТНОЕ мероприятие на Basic-канале."""
    svc = EventService(db_session)
    event = await svc.create(
        title="Бесплатное мероприятие",
        description="Только бесплатные билеты",
        date=datetime.now(timezone.utc) + timedelta(days=14),
        location="Москва",
        price=0,
        total_tickets=50,
        channel_id=basic_channel.id,
    )
    event.is_published = True
    await db_session.flush()
    return event


@pytest_asyncio.fixture
async def sample_ticket(
    db_session: AsyncSession,
    sample_user: User,
    sample_event: Event,
) -> Ticket:
    """Создаёт тестовый билет."""
    svc = TicketService(db_session)
    ticket = await svc.buy_ticket(sample_user.id, sample_event.id)
    return ticket


# ─── Сервисы ─────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def user_svc(db_session: AsyncSession) -> UserService:
    return UserService(db_session)


@pytest_asyncio.fixture
async def event_svc(db_session: AsyncSession) -> EventService:
    return EventService(db_session)


@pytest_asyncio.fixture
async def ticket_svc(db_session: AsyncSession) -> TicketService:
    return TicketService(db_session)
