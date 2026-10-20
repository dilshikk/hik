"""Отправка уведомлений о проходах (вход/выход) в Telegram-группу.

Включается переменными TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID. Если хотя бы
одна из них не задана, отправка отключена. Ошибки отправки только пишутся в
лог: приём событий от камеры от них не зависит.

Формат сообщения: фото сотрудника (если прислано камерой), а под ним имя и
дата/время прохода с хэштегом #checkin или #checkout.
"""
import logging

import httpx

from attendance import detect_direction, extract_events, parse_local_time
from config import FILES_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("hikvision.telegram")

API_URL = "https://api.telegram.org/bot{token}/{method}"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")

# Направление прохода -> хэштег в подписи
HASHTAGS = {"in": "#checkin", "out": "#checkout"}


def telegram_enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def employee_label(details: dict) -> str:
    name = str(details.get("name") or "").strip()
    if name:
        return name
    employee_no = details.get("employeeNoString")
    return f"Сотрудник {employee_no}" if employee_no else "Неизвестный сотрудник"


def build_caption(details: dict, local, direction: str | None) -> str:
    hashtag = HASHTAGS.get(direction or "", "#событие")
    date_str = local.strftime("%d.%m.%Y %H:%M")
    return f"{employee_label(details)}\n{date_str} {hashtag}"


def pick_photo(files: list[str]) -> str | None:
    """Файл с фото лица из этого же запроса камеры, если он есть."""
    for name in files:
        if name.lower().endswith(IMAGE_EXT):
            return name
    return None


async def send_item(client: httpx.AsyncClient, item: dict, files: list[str]) -> None:
    details = item["AccessControllerEvent"]
    local = parse_local_time(item)
    direction = detect_direction(details)
    caption = build_caption(details, local, direction)
    photo_name = pick_photo(files)

    if photo_name:
        photo_path = FILES_DIR / photo_name
        with photo_path.open("rb") as fh:
            response = await client.post(
                API_URL.format(token=TELEGRAM_BOT_TOKEN, method="sendPhoto"),
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"photo": (photo_name, fh, "image/jpeg")},
            )
    else:
        response = await client.post(
            API_URL.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage"),
            data={"chat_id": TELEGRAM_CHAT_ID, "text": caption},
        )
    if response.status_code != 200:
        logger.warning("Telegram API error %s: %s", response.status_code, response.text)


async def forward_payload(payload: list, files: list[str]) -> None:
    """Отправляет уведомления обо всех проходах из тела события. Не бросает исключений."""
    if not telegram_enabled():
        return
    events = extract_events(payload)
    if not events:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        for item in events:
            try:
                await send_item(client, item, files)
            except Exception:
                logger.exception("Failed to send Telegram notification")
