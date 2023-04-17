"""Log or report extra information to sentry in certain situations to help debug issues."""
import traceback

from anki.dbproxy import DBProxy
from anki.hooks import wrap
from aqt.main import AnkiQt

from . import LOGGER


def setup():
    _setup_logging_for_sync_collection_and_media()
    _setup_logging_for_db_begin()


def _log_stack(title: str):
    stack_trace = "\n".join(traceback.format_stack())
    LOGGER.info(f"Stack trace ({title}):\n{stack_trace}")


def _setup_logging_for_sync_collection_and_media():
    # Log stack trace when mw._sync_collection_and_media is called to debug the
    # "Cannot start transaction within transaction" error.
    AnkiQt._sync_collection_and_media = wrap(  # type: ignore
        AnkiQt._sync_collection_and_media,
        lambda *args, **kwargs: _log_stack("mw._sync_collection_and_media"),
        "before",
    )


def _setup_logging_for_db_begin():
    # Log stack trace when db.begin is called to debug the
    # "Cannot start transaction within transaction" error.
    DBProxy.begin = wrap(  # type: ignore
        DBProxy.begin,
        lambda *args, **kwargs: _log_stack("db.begin"),
        "before",
    )
