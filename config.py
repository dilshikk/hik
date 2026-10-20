"""Общие настройки шлюза и панели просмотра."""
import os
from pathlib import Path

# Папка с файлами, которые присылают устройства
FILES_DIR = Path("images")
FILES_DIR.mkdir(exist_ok=True)

# Токен панели. Он отдельный от GATEWAY_TOKEN: тот лежит в адресе камеры
# и может попасть в логи прокси, а этот открывает доступ к самим данным.
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
