"""Приём событий Hikvision (HTTP Listening) через FastAPI.

Настройка камеры: Configuration -> Network -> Advanced -> HTTP Listening
  Адрес: hik-getaway.imbtech.uz, URL: /api/v1/getaway/hikvision, Port: 80, Protocol: HTTP
"""
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.datastructures import UploadFile

logger = logging.getLogger("hikvision")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Hikvision Gateway")

IMAGES_DIR = Path("images")
IMAGES_DIR.mkdir(exist_ok=True)


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
                    # basename защищает от выхода за пределы папки
                    filename = Path(value.filename or f"{name}.jpg").name
                    (IMAGES_DIR / filename).write_bytes(data)
                    saved_images.append(filename)
                else:
                    events.append(parse_text_part(data))
            else:
                events.append(parse_text_part(value.encode()))
    else:
        events.append(parse_text_part(await request.body()))

    logger.info("Hikvision event: %s, images: %s", events, saved_images)

    # Всегда 200 OK, иначе камера будет повторно слать событие
    return {"status": "ok"}
