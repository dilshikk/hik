"""Пересылка отметок прохода в базу посещаемости FaceID (таблица public.access_logs).

Включается переменной ATTENDANCE_DATABASE_URL. Если она не задана, ничего не
происходит. Ошибки пересылки только пишутся в лог: приём событий от камеры
от них не зависит.

Колонки access_logs читаются из самой базы, значения приводятся к их реальным
типам, а отсутствующие колонки пропускаются. Поэтому код не ломается, если
схема таблицы немного отличается.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from config import ATTENDANCE_DATABASE_URL

logger = logging.getLogger("hikvision.attendance")

LOCAL_TZ = ZoneInfo("Asia/Tashkent")
AUTH_TYPE = "hikvision_gateway"
DIRECTIONS = {"checkin": "in", "checkout": "out"}

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker | None = None
# Колонки access_logs: имя -> (тип, есть ли значение по умолчанию)
_columns: dict[str, tuple[str, bool]] | None = None

if ATTENDANCE_DATABASE_URL:
    _engine = create_async_engine(ATTENDANCE_DATABASE_URL, pool_pre_ping=True)
    _sessions = async_sessionmaker(_engine, expire_on_commit=False)

COLUMNS_SQL = text(
    """
    SELECT a.attname AS name,
           format_type(a.atttypid, a.atttypmod) AS type,
           (a.attidentity <> '' OR d.adbin IS NOT NULL) AS has_default
    FROM pg_attribute a
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE a.attrelid = 'public.access_logs'::regclass
      AND a.attnum > 0 AND NOT a.attisdropped
    """
)

DUPLICATE_SQL = text(
    """
    SELECT 1 FROM public.access_logs
    WHERE employee_id::text = :employee_id
      AND access_date::text = :d
      AND left(access_time::text, 5) = :t
    LIMIT 1
    """
)

NAME_SQL = text(
    """
    SELECT first_name, last_name FROM public.access_logs
    WHERE employee_id::text = :employee_id AND first_name IS NOT NULL
    ORDER BY id DESC LIMIT 1
    """
)


def attendance_enabled() -> bool:
    return _sessions is not None


def extract_events(payload: list) -> list[dict]:
    """Достаёт из разобранного тела события прохода с кодом сотрудника."""
    events: list[dict] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("eventType") != "AccessControllerEvent":
            continue
        details = item.get("AccessControllerEvent")
        if isinstance(details, dict) and details.get("employeeNoString"):
            events.append(item)
    return events


def detect_direction(details: dict) -> str | None:
    status = str(details.get("attendanceStatus") or "").replace(" ", "").lower()
    if status in DIRECTIONS:
        return DIRECTIONS[status]
    label = str(details.get("label") or "").replace(" ", "").lower()
    return DIRECTIONS.get(label)


def parse_local_time(item: dict) -> datetime:
    """Время берём с терминала и приводим к Ташкенту; нет времени - берём текущее."""
    raw = item.get("dateTime")
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return datetime.now(LOCAL_TZ)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


async def load_columns(session) -> dict[str, tuple[str, bool]]:
    global _columns
    if _columns is None:
        rows = (await session.execute(COLUMNS_SQL)).mappings().all()
        if not rows:
            raise RuntimeError("Таблица public.access_logs не найдена")
        _columns = {r["name"]: (r["type"], bool(r["has_default"])) for r in rows}
    return _columns


def build_values(item: dict, local: datetime, names: tuple[str | None, str | None]) -> dict:
    details = item["AccessControllerEvent"]
    first_name, last_name = names
    if first_name is None and last_name is None:
        first_name, last_name = details.get("name"), None
    card = details.get("cardNo")
    return {
        "employee_id": str(details["employeeNoString"]),
        "access_date": local.date().isoformat(),
        "access_time": local.strftime("%H:%M:%S"),
        "access_datetime": local.isoformat(),
        "direction": detect_direction(details),
        "auth_type": AUTH_TYPE,
        "device_name": details.get("deviceName"),
        "device_sn": str(item["deviceID"]) if item.get("deviceID") else None,
        "card_no": str(card) if card else None,
        "first_name": first_name,
        "last_name": last_name,
    }


async def insert_row(session, columns: dict[str, tuple[str, bool]], values: dict) -> None:
    names: list[str] = []
    exprs: list[str] = []
    params: dict[str, str | None] = {}
    for name, value in values.items():
        if name not in columns or value is None:
            continue
        names.append(name)
        # Через text, чтобы драйвер не требовал готовых date/datetime
        exprs.append(f"CAST(CAST(:{name} AS text) AS {columns[name][0]})")
        params[name] = value
    id_col = columns.get("id")
    if id_col and not id_col[1]:
        names.insert(0, "id")
        exprs.insert(0, "(SELECT COALESCE(MAX(id), 0) + 1 FROM public.access_logs)")
    sql = f"INSERT INTO public.access_logs ({', '.join(names)}) VALUES ({', '.join(exprs)})"
    await session.execute(text(sql), params)


async def forward_item(session, item: dict) -> bool:
    """Записывает один проход. Возвращает False, если такая отметка уже есть."""
    columns = await load_columns(session)
    local = parse_local_time(item)
    employee_id = str(item["AccessControllerEvent"]["employeeNoString"])
    # Блокировка защищает нумерацию id, если события придут одновременно
    await session.execute(text("SELECT pg_advisory_xact_lock(7001)"))
    duplicate = await session.execute(
        DUPLICATE_SQL,
        {"employee_id": employee_id, "d": local.date().isoformat(), "t": local.strftime("%H:%M")},
    )
    if duplicate.first() is not None:
        return False
    # Имя берём как у этого сотрудника уже записано в базе посещаемости
    names: tuple[str | None, str | None] = (None, None)
    if "first_name" in columns and "last_name" in columns:
        row = (await session.execute(NAME_SQL, {"employee_id": employee_id})).first()
        if row is not None:
            names = (row[0], row[1])
    await insert_row(session, columns, build_values(item, local, names))
    return True


async def forward_payload(payload: list) -> None:
    """Пересылает все проходы из тела события. Не бросает исключений."""
    if _sessions is None:
        return
    for item in extract_events(payload):
        try:
            async with _sessions() as session:
                async with session.begin():
                    added = await forward_item(session, item)
            logger.info(
                "Attendance %s: employee=%s",
                "added" if added else "duplicate skipped",
                item["AccessControllerEvent"]["employeeNoString"],
            )
        except Exception:
            logger.exception("Failed to forward event to attendance DB")
