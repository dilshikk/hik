"""Запросы на чтение для панели просмотра событий."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Row, Select, Text, cast, func, or_, select

from db import HikEvent, SessionLocal

# Значение фильтра "события без типа"
NO_TYPE = "__none__"
NO_TYPE_LABEL = "(без типа)"

# Где в payload искать название устройства (по порядку). Если ни одного поля
# нет, берём IP отправителя. Список можно поправить под реальные события.
DEVICE_FIELDS = ("deviceName", "devName", "ipAddress", "srcName")
_first_item = HikEvent.payload[0]
DEVICE = func.coalesce(*[_first_item[field].astext for field in DEVICE_FIELDS], HikEvent.client_ip)


@dataclass(frozen=True)
class EventFilters:
    event_type: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    query: str | None = None
    with_images: bool = False


def _like_pattern(value: str) -> str:
    # Спецсимволы LIKE экранируем, чтобы поиск шёл по буквальному тексту
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _apply_filters(stmt: Select, filters: EventFilters) -> Select:
    if filters.event_type == NO_TYPE:
        stmt = stmt.where(HikEvent.event_type.is_(None))
    elif filters.event_type:
        stmt = stmt.where(HikEvent.event_type == filters.event_type)
    if filters.date_from:
        stmt = stmt.where(HikEvent.received_at >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(HikEvent.received_at <= filters.date_to)
    if filters.query:
        pattern = _like_pattern(filters.query)
        stmt = stmt.where(
            or_(
                cast(HikEvent.payload, Text).ilike(pattern, escape="\\"),
                HikEvent.raw_body.ilike(pattern, escape="\\"),
                HikEvent.client_ip.ilike(pattern, escape="\\"),
                HikEvent.event_type.ilike(pattern, escape="\\"),
            )
        )
    if filters.with_images:
        stmt = stmt.where(func.jsonb_array_length(HikEvent.images) > 0)
    return stmt


def _list_item(row: Row) -> dict:
    return {
        "id": row.id,
        "received_at": row.received_at.isoformat(),
        "client_ip": row.client_ip,
        "event_type": row.event_type,
        "content_type": row.content_type,
        "images": row.images or [],
        "device": row.device,
    }


async def list_events(
    filters: EventFilters, *, limit: int, before_id: int | None, after_id: int | None
) -> tuple[list[dict], bool]:
    """Список событий без тяжёлых полей.

    Обычный режим: новые сверху, постраничная загрузка вниз по before_id.
    Режим after_id: события новее указанного, от старых к новым (для автообновления),
    поэтому при большом потоке ничего не пропускается.
    """
    stmt = select(
        HikEvent.id,
        HikEvent.received_at,
        HikEvent.client_ip,
        HikEvent.event_type,
        HikEvent.content_type,
        HikEvent.images,
        DEVICE.label("device"),
    )
    stmt = _apply_filters(stmt, filters)
    if after_id is not None:
        stmt = stmt.where(HikEvent.id > after_id).order_by(HikEvent.id.asc())
    else:
        if before_id is not None:
            stmt = stmt.where(HikEvent.id < before_id)
        stmt = stmt.order_by(HikEvent.id.desc())
    # Берём на одну запись больше, чтобы понять, есть ли продолжение
    async with SessionLocal() as session:
        rows = (await session.execute(stmt.limit(limit + 1))).all()
    return [_list_item(row) for row in rows[:limit]], len(rows) > limit


async def get_event(event_id: int) -> dict | None:
    async with SessionLocal() as session:
        event = await session.get(HikEvent, event_id)
    if event is None:
        return None
    return {
        "id": event.id,
        "received_at": event.received_at.isoformat(),
        "client_ip": event.client_ip,
        "event_type": event.event_type,
        "method": event.method,
        "path": event.path,
        "query": event.query,
        "content_type": event.content_type,
        "headers": event.headers or {},
        "raw_body": event.raw_body,
        "parts": event.parts or [],
        "payload": event.payload or [],
        "images": event.images or [],
    }


async def list_event_types(limit: int = 100) -> list[dict]:
    count = func.count().label("n")
    stmt = (
        select(HikEvent.event_type, count)
        .where(HikEvent.event_type.is_not(None))
        .group_by(HikEvent.event_type)
        .order_by(count.desc())
        .limit(limit)
    )
    async with SessionLocal() as session:
        rows = (await session.execute(stmt)).all()
    return [{"name": row.event_type, "count": row.n} for row in rows]


async def get_stats(hours: int) -> dict:
    """Статистика за последние `hours` часов: по часам (UTC), по типам, по устройствам."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    in_range = HikEvent.received_at >= since
    hour = func.date_trunc("hour", func.timezone("UTC", HikEvent.received_at)).label("h")
    count = func.count().label("n")
    type_name = func.coalesce(HikEvent.event_type, NO_TYPE_LABEL).label("name")
    device_name = DEVICE.label("name")

    async with SessionLocal() as session:
        by_hour = (
            await session.execute(select(hour, count).where(in_range).group_by("h").order_by("h"))
        ).all()
        by_type = (
            await session.execute(
                select(type_name, count).where(in_range).group_by("name").order_by(count.desc()).limit(15)
            )
        ).all()
        by_device = (
            await session.execute(
                select(device_name, count).where(in_range).group_by("name").order_by(count.desc()).limit(15)
            )
        ).all()

    return {
        "hours": hours,
        "total": sum(row.n for row in by_hour),
        # Час приходит как UTC без пояса, добавляем пояс, чтобы браузер показал местное время
        "by_hour": [{"hour": row.h.replace(tzinfo=timezone.utc).isoformat(), "count": row.n} for row in by_hour],
        "by_type": [{"name": row.name, "count": row.n} for row in by_type],
        "by_device": [{"name": row.name or "—", "count": row.n} for row in by_device],
    }
