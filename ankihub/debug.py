"""Instrument Anki to log stack traces when certain functions are called to help debug issues."""
import traceback

import aqt
from anki.hooks import wrap

from . import LOGGER


def log_stack(title: str):
    stack_trace = "\n".join(traceback.format_stack())
    LOGGER.info(f"Stack trace ({title}):\n{stack_trace}")


def setup():

    # Log stack trace when mw._sync_collection_and_media is called to debug the
    # "Cannot start transaction within transaction" error that occurs when two syncs
    # are started at the same time.
    aqt.mw._sync_collection_and_media = wrap(  # type: ignore
        aqt.mw._sync_collection_and_media,
        lambda *args, **kwargs: log_stack("mw._sync_collection_and_media"),
        "before",
    )
