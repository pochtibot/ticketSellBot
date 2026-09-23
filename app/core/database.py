from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=5,
    max_overflow=5,
    pool_recycle=1800,     # пересоздавать соединения каждые 30 мин
    pool_pre_ping=True,    # проверять соединение перед использованием
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    """Dependency: единая сессия на HTTP-запрос.

    Коммит — в route handler'е (для write-операций).
    Read-only эндпоинты не коммитят.
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables (for development). Use Alembic in production."""
    # Импортируем модели, чтобы они зарегистрировались в Base.metadata
    from app.core.models import User, Event, Ticket, Payment, Channel, ChannelAdmin, VKPayOrder  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Dispose the engine."""
    await engine.dispose()
