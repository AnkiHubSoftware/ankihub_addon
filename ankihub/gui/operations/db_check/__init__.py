from .... import LOGGER
from .ah_db_check import check_ankihub_db
from .anki_db_check import check_anki_db

# This variable is used to make sure that the database check is only run once per Anki start.
# The reason for this is that if the user has a problem with one of the decks, the database check
# will show a dialog and will require input from the user. We don't want to show the dialog multiple
# times, because it would be annoying.
ATTEMPTED_DATABASE_CHECK = False


def maybe_check_databases() -> None:
    """Checks the AnkiHub and Anki databases. Shows a dialog if there is a problem with them.
    Has to be run in the main thread.
    Will do nothing if it was run before on this start of Anki."""
    global ATTEMPTED_DATABASE_CHECK
    if ATTEMPTED_DATABASE_CHECK:
        LOGGER.info("Database check was already run, skipping.")
        return

    ATTEMPTED_DATABASE_CHECK = True

    check_ankihub_db(on_success=check_anki_db)
