from typing import Callable

import aqt


def wrap_func_with_run_on_main(func: Callable) -> Callable:
    """Returns a wrapper function that runs the given function on the main thread."""

    def wrapper(*args, **kwargs) -> None:
        aqt.mw.taskman.run_on_main(func)

    return wrapper
