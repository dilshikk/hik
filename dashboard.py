"""Панель просмотра событий: страница и JSON API (всё под токеном DASHBOARD_TOKEN).

Токен передаётся в заголовке X-Dashboard-Token, в адрес он не попадает.
Сама страница /dashboard публичная, но это только оболочка без данных.
"""
import hmac
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse

from config import DASHBOARD_TOKEN, FILES_DIR
from queries import EventFilters, get_event, get_stats, list_event_types, list_events

logger = logging.getLogger("hikvision.dashboard")

if not DASHBOARD_TOKEN:
    logger.warning("DASHBOARD_TOKEN не задан: панель просмотра отключена")

PAGE_PATH = Path(__file__).parent / "static" / "dashboard.html"

# Скрипт встроен в страницу, поэтому разрешён inline; всё остальное закрыто.
# Данные от устройств в страницу попадают только как текст, не как HTML.
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src blob:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def verify_dashboard_token(x_dashboard_token: Annotated[str | None, Header()] = None) -> None:
    if not DASHBOARD_TOKEN:
        # Без токена панель закрыта полностью, а не открыта всем
        raise HTTPException(status_code=503, detail="Панель отключена: не задан DASHBOARD_TOKEN")
    supplied = x_dashboard_token or ""
    if not hmac.compare_digest(supplied.encode(), DASHBOARD_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Invalid token")


def as_utc(value: datetime | None) -> datetime | None:
    # Дата без пояса считается UTC, чтобы сравнение с timestamptz не падало
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


router = APIRouter()
api = APIRouter(prefix="/dashboard/api", dependencies=[Depends(verify_dashboard_token)])


@router.get("/dashboard", include_in_schema=False)
async def dashboard_page() -> FileResponse:
    return FileResponse(PAGE_PATH, media_type="text/html; charset=utf-8", headers=PAGE_HEADERS)


@api.get("/events")
async def events(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_id: int | None = None,
    after_id: int | None = None,
    event_type: Annotated[str | None, Query(max_length=128)] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    with_images: bool = False,
) -> dict:
    filters = EventFilters(
        event_type=event_type,
        date_from=as_utc(date_from),
        date_to=as_utc(date_to),
        query=q.strip() if q else None,
        with_images=with_images,
    )
    items, has_more = await list_events(filters, limit=limit, before_id=before_id, after_id=after_id)
    return {"events": items, "has_more": has_more}


@api.get("/events/{event_id}")
async def event_detail(event_id: int) -> dict:
    event = await get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return event


@api.get("/types")
async def event_types() -> dict:
    return {"types": await list_event_types()}


@api.get("/stats")
async def stats(hours: Annotated[int, Query(ge=1, le=720)] = 24) -> dict:
    return await get_stats(hours)


@api.get("/files/{name}")
async def get_file(name: str) -> FileResponse:
    base = FILES_DIR.resolve()
    path = (base / Path(name).name).resolve()
    # Только файлы прямо внутри папки images, без вложенных путей и ..
    if Path(name).name != name or path.parent != base or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    media_type = mimetypes.guess_type(path.name)[0] or ""
    # Всё, кроме обычных картинок, отдаём как скачиваемые данные: svg/html от устройства не должны исполняться
    if not media_type.startswith("image/") or media_type == "image/svg+xml":
        media_type = "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})


router.include_router(api)
