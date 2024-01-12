"""Code that modifies Anki's add-ons module.
Handles problems with add-on updates and deletions."""
from concurrent.futures import Future
from typing import Any, Callable

import aqt
from anki.hooks import wrap
from aqt import addons
from aqt.addons import DownloaderInstaller

from .. import LOGGER


def setup_addons():
    _raise_exceptions_on_otherwise_silent_addon_update_failures()

    _prevent_ui_deadlock_of_update_dialog_with_progress_dialog()


def _raise_exceptions_on_otherwise_silent_addon_update_failures():
    # this prevents silent add-on update failures like the ones reported here:
    # https://community.ankihub.net/t/bug-improve-ankihub-addon-update-process/557/5
    # it changes the behavior of _download_done so that it checks if the future has an exception
    DownloaderInstaller._download_done = wrap(  # type: ignore
        old=DownloaderInstaller._download_done,
        new=_check_future_for_exceptions,
        pos="around",
    )


def _check_future_for_exceptions(*args: Any, **kwargs: Any) -> None:
    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    _old(*args, **kwargs)

    # in future Anki version the argument could be passed differently
    # so we check all arguments for a Future
    future: Future = next((x for x in args if isinstance(x, Future)), None)
    if future is None:
        future = kwargs.get("future", None)

    if future is None:
        raise ValueError("Could not find future argument")

    # throws exception if there was one in the future
    future.result()


def _prevent_ui_deadlock_of_update_dialog_with_progress_dialog():
    # prevent the situation that the add-on update dialog is shown while the progress dialog is open which can
    # lead to a deadlock when AnkiHub is syncing and there is an add-on update.
    addons.prompt_to_update = wrap(  # type: ignore
        old=addons.prompt_to_update,
        new=_with_delay_when_progress_dialog_is_open,
        pos="around",
    )


def _with_delay_when_progress_dialog_is_open(*args, **kwargs) -> Any:
    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    def wrapper():
        LOGGER.info("Calling with_delay_when_progress_dialog_is_open._old")
        _old(*args, **kwargs)

        # the documentation of aqt.mw.progress.timer says that the timer has to be deleted to
        # prevent memory leaks
        timer.deleteLater()

    # aqt.mw.progress.timer is there for creating "Custom timers which avoid firing while a progress dialog is active".
    # It's better to use a large delay value because there is a 0.5 second time window in which
    # the func can be called even if the progress dialog is not closed yet.
    # See https://github.com/ankitects/anki/blob/d9f1e2264804481a2549b23dbc8a530857ad57fc/qt/aqt/progress.py#L261-L277
    timer = aqt.mw.progress.timer(
        ms=2000,
        func=wrapper,
        repeat=False,
        requiresCollection=True,
        parent=aqt.mw,
    )
