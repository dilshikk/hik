"""Подключение к PostgreSQL и модель события Hikvision."""
import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Пример: postgresql+asyncpg://user:password@localhost:5432/hikvision
DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class HikEvent(Base):
    __tablename__ = "hik_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # Все разобранные части запроса (JSON-объекты или XML-строки)
    payload: Mapped[list] = mapped_column(JSONB, default=list)
    # Имена сохранённых файлов изображений
    images: Mapped[list] = mapped_column(JSONB, default=list)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def save_event(
    client_ip: str | None, payload: list, images: list[str]
) -> HikEvent:
    event_type = next(
        (p["eventType"] for p in payload if isinstance(p, dict) and "eventType" in p),
        None,
    )
    async with SessionLocal() as session:  # type: AsyncSession
        event = HikEvent(
            client_ip=client_ip,
            event_type=event_type,
            payload=payload,
            images=images,
        )
        session.add(event)
        await session.commit()
        return event
