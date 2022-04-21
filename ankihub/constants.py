import pathlib
from enum import Enum

URL_BASE = "https://ankihub.net/"
API_URL_BASE = "http://localhost:8000/api"
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
