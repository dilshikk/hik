"""Приём событий Hikvision (HTTP Listening) через FastAPI с сохранением в PostgreSQL.

Настройка камеры: Configuration -> Network -> Advanced -> HTTP Listening
  Адрес: hik-getaway.mado.uz, URL: /api/v1/getaway/hikvision, Port: 80, Protocol: HTTP

Сохраняется всё, что присылает камера: заголовки, сырое тело, все части
multipart (JSON/XML и файлы любого типа).
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

FILES_DIR = Path("images")
FILES_DIR.mkdir(exist_ok=True)

TEXT_TYPES = ("application/json", "application/xml", "text/")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Hikvision Gateway", lifespan=lifespan)


def decode_text(raw: bytes) -> str:
    # PostgreSQL не принимает нулевой символ в тексте
    return raw.decode("utf-8", errors="replace").replace("\x00", "").strip()


def parse_text(text: str) -> dict | list | str:
    """JSON разбираем в объект, XML и прочий текст оставляем строкой."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def is_text_type(content_type: str) -> bool:
    return content_type.startswith(TEXT_TYPES)


def save_file(data: bytes, original_name: str) -> str:
    # basename защищает от выхода за пределы папки, uuid - от перезаписи
    filename = f"{uuid.uuid4().hex}_{Path(original_name).name}"
    (FILES_DIR / filename).write_bytes(data)
    return filename


async def read_multipart(request: Request) -> tuple[list, list, list[str]]:
    parts: list[dict] = []
    payload: list = []
    files: list[str] = []
    form = await request.form()
    for name, value in form.multi_items():
        if isinstance(value, UploadFile):
            data = await value.read()
            part_type = value.content_type or ""
            part: dict = {
                "name": name,
                "filename": value.filename,
                "content_type": part_type,
                "size": len(data),
            }
            if is_text_type(part_type):
                text = decode_text(data)
                part["text"] = text
                payload.append(parse_text(text))
            else:
                part["file"] = save_file(data, value.filename or f"{name}.bin")
                files.append(part["file"])
            parts.append(part)
        else:
            text = value.replace("\x00", "").strip()
            parts.append({"name": name, "text": text})
            payload.append(parse_text(text))
    return parts, payload, files


@app.post("/api/v1/getaway/hikvision")
async def hikvision_webhook(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    parts: list = []
    payload: list = []
    files: list[str] = []
    raw_body: str | None = None

    try:
        if content_type.startswith("multipart/form-data"):
            parts, payload, files = await read_multipart(request)
        else:
            body = await request.body()
            if is_text_type(content_type) or not content_type:
                raw_body = decode_text(body)
                payload.append(parse_text(raw_body))
            else:
                # Бинарное тело без multipart сохраняем файлом
                files.append(save_file(body, "body.bin"))
        client_ip = request.client.host if request.client else None
        await save_event(
            client_ip=client_ip,
            method=request.method,
            path=request.url.path,
            query=request.url.query,
            content_type=content_type,
            headers=dict(request.headers),
            raw_body=raw_body,
            parts=parts,
            payload=payload,
            files=files,
        )
        logger.info("Hikvision event from %s: %s | files: %s", client_ip, payload, files)
    except Exception:
        # Ошибка разбора или БД не должна приводить к повторной отправке события
        logger.exception("Failed to process Hikvision event")

    # Всегда 200 OK, иначе камера будет повторно слать событие
    return {"status": "ok"}
