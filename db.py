"""Подключение к PostgreSQL и модель события Hikvision."""
import os
import re
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Пример: postgresql+asyncpg://user:password@localhost:5432/hikvision
DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Новые колонки добавляются к уже существующей таблице без потери данных
MIGRATIONS = [
    "ALTER TABLE hik_events ADD COLUMN IF NOT EXISTS method VARCHAR(16)",
    "ALTER TABLE hik_events ADD COLUMN IF NOT EXISTS path TEXT",
    "ALTER TABLE hik_events ADD COLUMN IF NOT EXISTS query TEXT",
    "ALTER TABLE hik_events ADD COLUMN IF NOT EXISTS content_type TEXT",
    "ALTER TABLE hik_events ADD COLUMN IF NOT EXISTS headers JSONB",
    "ALTER TABLE hik_events ADD COLUMN IF NOT EXISTS raw_body TEXT",
    "ALTER TABLE hik_events ADD COLUMN IF NOT EXISTS parts JSONB",
]

XML_EVENT_TYPE = re.compile(r"<eventType>\s*([^<]+?)\s*</eventType>")


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
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Все HTTP-заголовки запроса
    headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Сырое тело запроса (если это не multipart)
    raw_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Все части multipart: имя, тип, размер, текст или имя файла
    parts: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Разобранные JSON-объекты и XML-строки
    payload: Mapped[list] = mapped_column(JSONB, default=list)
    # Имена всех сохранённых файлов
    images: Mapped[list] = mapped_column(JSONB, default=list)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in MIGRATIONS:
            await conn.execute(text(statement))


def detect_event_type(payload: list) -> str | None:
    for item in payload:
        if isinstance(item, dict) and "eventType" in item:
            return str(item["eventType"])[:128]
        if isinstance(item, str):
            match = XML_EVENT_TYPE.search(item)
            if match:
                return match.group(1)[:128]
    return None


async def save_event(
    *,
    client_ip: str | None,
    method: str,
    path: str,
    query: str,
    content_type: str,
    headers: dict,
    raw_body: str | None,
    parts: list,
    payload: list,
    files: list[str],
) -> HikEvent:
    async with SessionLocal() as session:
        event = HikEvent(
            client_ip=client_ip,
            event_type=detect_event_type(payload),
            method=method,
            path=path,
            query=query,
            content_type=content_type,
            headers=headers,
            raw_body=raw_body,
            parts=parts,
            payload=payload,
            images=files,
        )
        session.add(event)
        await session.commit()
        return event
