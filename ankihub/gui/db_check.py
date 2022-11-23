from .. import LOGGER
from ..db import ankihub_db


def check_db():
    dids_with_missing_values = ankihub_db.ankihub_dids_of_decks_with_missing_values()
    LOGGER.debug(f"dids_with_missing_values: {dids_with_missing_values}")
