import os
import pathlib
from enum import Enum

from . import LOGGER

ANKIHUB_SITE_URL = "https://ankihub.net"
API_URL_BASE = os.getenv("API_URL_BASE")
API_URL_BASE = API_URL_BASE if API_URL_BASE else "https://app.ankihub.net/api"

LOGGER.debug(f"Starting with URL_BASE {API_URL_BASE}")
URL_VIEW_NOTE = f"{ANKIHUB_SITE_URL}/notes/"
URL_HELP = f"{ANKIHUB_SITE_URL}/help"
ANKIHUB_NOTE_TYPE_FIELD_NAME = "ankihub_id"
ANKIHUB_NOTE_TYPE_MODIFICATION_STRING = "ANKIHUB MODFICATIONS"
ADDON_PATH = pathlib.Path(__file__).parent.absolute()
ICONS_PATH = ADDON_PATH / "icons"

TOKEN_SLUG = "token"
USER_EMAIL_SLUG = "user_email"

CSV_DELIMITER = ";"

USER_SUPPORT_EMAIL_SLUG = "help@ankipalace.com"


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
