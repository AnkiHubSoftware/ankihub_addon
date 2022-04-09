import pathlib
from enum import Enum

URL_BASE = "https://hub.ankipalace.com/"
API_URL_BASE = "http://localhost:8000/api"
URL_VIEW_NOTE = URL_BASE + "note/"
ANKIHUB_NOTE_TYPE_FIELD_NAME = "AnkiHub ID (hidden)"
ADDON_PATH = pathlib.Path(__file__).parent.absolute()
ICONS_PATH = ADDON_PATH / "icons"

LAST_SYNC_SLUG = "last_sync"
TOKEN_SLUG = "token"
USER_EMAIL_SLUG = "user_email"

CSV_DELIMITER = ";"

USER_SUPPORT_EMAIL_SLUG = "help@ankipalace.com"


class AnkiHubCommands(Enum):
    CHANGE = "Suggest a change"
    NEW = "Suggest a new note"
