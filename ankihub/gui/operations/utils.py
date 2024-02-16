import inspect
from concurrent.futures import Future
from functools import partial, wraps
from typing import Callable

import aqt


def future_with_exception(e: BaseException) -> Future:
    future: Future = Future()
    future.set_exception(e)
    return future


def future_with_result(result: object) -> Future:
    future: Future = Future()
    future.set_result(result)
    return future


def pass_exceptions_to_on_done(func: Callable) -> Callable:
    """Decorator that catches exceptions and calls the 'on_done' function with a future containing the exception.
    The 'on_done' function is called on the main thread.
    Note: Exceptions are not backpropagated to the caller. They are only passed to the 'on_done' function.
    """
    sig = inspect.signature(func)
    params = sig.parameters
    if (
        "on_done" not in params
        or params["on_done"].default is not inspect.Parameter.empty
    ):
        raise ValueError(  # pragma: no cover
            f"Function {func.__name__} must have a required 'on_done' parameter"
        )

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            on_done = kwargs.get("on_done")
            aqt.mw.taskman.run_on_main(partial(on_done, future_with_exception(e)))

    return wrapper
