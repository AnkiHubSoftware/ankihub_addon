"""Sets up logging for Anki events, for example when specific hooks are called."""

import traceback

from aqt.gui_hooks import (
    collection_did_temporarily_close,
    collection_will_temporarily_close,
    sync_did_finish,
    sync_will_start,
)

from . import LOGGER


def setup() -> None:
    sync_will_start.append(lambda: LOGGER.info("AnkiWeb sync will start."))
    sync_did_finish.append(lambda: LOGGER.info("AnkiWeb sync did finish."))

    collection_will_temporarily_close.append(
        lambda _: LOGGER.info(
            "Collection will temporarily close.", trace=_get_current_trace()
        )
    )
    collection_did_temporarily_close.append(
        lambda _: LOGGER.info(
            "Collection did temporarily close.", trace=_get_current_trace()
        )
    )


def _get_current_trace() -> str:
    return "".join(traceback.format_stack())
