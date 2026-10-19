"""Приём событий Hikvision (HTTP Listening) через FastAPI с сохранением в PostgreSQL.

Настройка камеры: Configuration -> Network -> Advanced -> HTTP Listening
  Адрес: hik-getaway.mado.uz, URL: /api/v1/getaway/hikvision, Port: 80, Protocol: HTTP
"""
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.datastructures import UploadFile

from db import init_db, save_event

logger = logging.getLogger("hikvision")
logging.basicConfig(level=logging.INFO)

IMAGES_DIR = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Hikvision Gateway", lifespan=lifespan)


def parse_text_part(raw: bytes) -> dict | str:
    """Метаданные приходят как JSON или XML; JSON разбираем, XML отдаём строкой."""
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


@app.post("/api/v1/getaway/hikvision")
async def hikvision_webhook(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    events: list[dict | str] = []
    saved_images: list[str] = []

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        for name, value in form.multi_items():
            if isinstance(value, UploadFile):
                data = await value.read()
                if (value.content_type or "").startswith("image/"):
                    # basename защищает от выхода за пределы папки,
                    # uuid - от перезаписи файлов с одинаковым именем
                    original = Path(value.filename or f"{name}.jpg").name
                    filename = f"{uuid.uuid4().hex}_{original}"
                    (IMAGES_DIR / filename).write_bytes(data)
                    saved_images.append(filename)
                else:
                    events.append(parse_text_part(data))
            else:
                events.append(parse_text_part(value.encode()))
    else:
        events.append(parse_text_part(await request.body()))

    try:
        client_ip = request.client.host if request.client else None
        await save_event(client_ip, events, saved_images)
    except Exception:
        # Ошибка БД не должна приводить к повторной отправке события камерой
        logger.exception("Failed to save Hikvision event")

    # Всегда 200 OK, иначе камера будет повторно слать событие
    return {"status": "ok"}
