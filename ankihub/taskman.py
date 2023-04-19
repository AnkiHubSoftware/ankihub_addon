"""Modifies Anki's task manager (aqt.taskman). """
from threading import current_thread, main_thread

from anki.hooks import wrap
from aqt.taskman import TaskManager

from . import LOGGER


def setup():
    """Modifies TaskManager._on_closures_pending() to only run in the main thread.
    This prevents Anki from crashing when a background thread calls it and
    a closure tries to call a function that tries to change the Qt GUI.
    This workaround is made necessary because we are nesting TaskManager.run_in_background calls
    in the add-on code. (_on_closures_pending() is called in run_in_background,
    run_in_background expects to be called from the main thread, but when the run_in_background_calls are nested,
    it is called from a background thread. Then any waiting closures are also called from a background thread.)
    The crashes caused by this are very rare even without this workaround."""
    TaskManager._on_closures_pending = wrap(  # type: ignore
        TaskManager._on_closures_pending,
        _only_run_in_main_thread,
        "around",
    )


def _only_run_in_main_thread(*args, **kwargs):
    _old = kwargs["_old"]
    del kwargs["_old"]

    if current_thread() == main_thread():
        _old(*args, **kwargs)
    else:
        LOGGER.warning(
            "TaskManager._on_closures_pending() skipped, because it was called from a background thread."
        )
