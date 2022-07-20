import os
import pathlib
from enum import Enum

from . import LOGGER
from .config import config

ANKIHUB_APP_URL = os.getenv("ANKIHUB_APP_URL")
if ANKIHUB_APP_URL is None:
    ANKIHUB_APP_URL = config.public_config.get("ankihub_url")
    ANKIHUB_APP_URL = ANKIHUB_APP_URL if ANKIHUB_APP_URL else "https://app.ankihub.net"
API_URL_BASE = f"{ANKIHUB_APP_URL}/api"

LOGGER.debug(f"Starting with URL_BASE {API_URL_BASE}")
URL_VIEW_NOTE = f"{ANKIHUB_APP_URL}/decks/notes/"
URL_HELP = f"{ANKIHUB_APP_URL}/help"
URL_DECKS = f"{ANKIHUB_APP_URL}/explore"
URL_DECK_BASE = f"{ANKIHUB_APP_URL}/decks"
ANKIHUB_NOTE_TYPE_FIELD_NAME = "ankihub_id"
ANKIHUB_NOTE_TYPE_MODIFICATION_STRING = "ANKIHUB MODFICATIONS"
ADDON_PATH = pathlib.Path(__file__).parent.absolute()
ADDON_PACKAGE = __name__.split(".")[0]
ICONS_PATH = ADDON_PATH / "icons"

TOKEN_SLUG = "token"
USER_EMAIL_SLUG = "user_email"

CSV_DELIMITER = ";"

USER_SUPPORT_EMAIL_SLUG = "help@ankipalace.com"
BUG_REPORT_FORM = "https://forms.gle/D5U6QQnLkfynzni59"


class AnkiHubCommands(Enum):
    CHANGE = "Suggest a change"
    NEW = "Suggest a new note"


# TODO Make sure these match up with SuggestionType.choices on AnkiHub
class ChangeTypes(Enum):
    UPDATED_CONTENT = "updated_content", "Updated content"
    NEW_CONTENT = "new_content", "New content"
    SPELLING_GRAMMATICAL = "spelling/grammatical", "Spelling/Grammatical"
    CONTENT_ERROR = "content_error", "Content error"
    NEW_CARD_TO_ADD = "new_card_to_add", "New card to add"
    OTHER = "other", "Other"


RATIONALE_FOR_CHANGE_MAX_LENGTH = 1024
