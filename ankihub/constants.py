import os
import pathlib
from enum import Enum
from aqt import qDebug

URL_BASE = "https://ankihub.net/"
if os.getenv("DEVELOPMENT", False):
    API_URL_BASE = "https://staging.ankihub.net/api"
else:
    API_URL_BASE = "https://app.ankihub.net/api"

qDebug(f"Starting with URL_BASE {API_URL_BASE}")
URL_VIEW_NOTE = URL_BASE + "notes/"
URL_HELP = f"{URL_BASE}/help"
ANKIHUB_NOTE_TYPE_FIELD_NAME = "AnkiHub ID"
ADDON_PATH = pathlib.Path(__file__).parent.absolute()
ICONS_PATH = ADDON_PATH / "icons"

TOKEN_SLUG = "token"
USER_EMAIL_SLUG = "user_email"

CSV_DELIMITER = ";"

USER_SUPPORT_EMAIL_SLUG = "help@ankipalace.com"


class AnkiHubCommands(Enum):
    CHANGE = "Suggest a change"
    NEW = "Suggest a new note"
