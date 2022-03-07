import pathlib
from enum import Enum

URL_BASE = "https://hub.ankipalace.com/"
API_URL_BASE = "http://localhost:8000/api"
URL_VIEW_NOTE = URL_BASE + "note/"
ANKIHUB_NOTE_TYPE_FIELD_NAME = "AnkiHub ID (hidden)"
ADDON_PATH = pathlib.Path(__file__).parent.absolute()
ICONS_PATH = ADDON_PATH / "icons"


class AnkiHubCommands(Enum):
    CHANGE = "Suggest a change"
    NEW = "Suggest a new note"
