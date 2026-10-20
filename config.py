"""Общие настройки шлюза и панели просмотра."""
import os
from pathlib import Path

# Папка с файлами, которые присылают устройства
FILES_DIR = Path("images")
FILES_DIR.mkdir(exist_ok=True)

# Токен панели. Он отдельный от GATEWAY_TOKEN: тот лежит в адресе камеры
# и может попасть в логи прокси, а этот открывает доступ к самим данным.
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")

# База посещаемости FaceID (resto). Если не задана, пересылка отключена.
# Формат: postgresql+asyncpg://user:password@host:5432/dbname
ATTENDANCE_DATABASE_URL = os.environ.get("ATTENDANCE_DATABASE_URL", "")

# Уведомления о проходах в Telegram-группу. Если одна из переменных не
# задана, отправка отключена (шлюз при этом работает как обычно).
# TELEGRAM_BOT_TOKEN - токен бота от @BotFather.
# TELEGRAM_CHAT_ID - id группы/канала (бот должен быть добавлен туда как участник/админ).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
