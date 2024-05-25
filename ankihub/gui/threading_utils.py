import threading
import time
from functools import wraps
from typing import Callable, Optional

from .. import LOGGER


def rate_limited(seconds: float, on_done_arg_name: Optional[str] = None) -> Callable:
    """Rate limit a function to be called at most once every `seconds`
    Subsequent calls will be ignored. Thread safe.
    You can pass an optional `on_done_arg_name` argument to the decorator that
    specifies the name of an argument of the wrapped function that is a callback
    to be called when the rate limit is hit."""

    def decorator(func: Callable) -> Callable:
        lock = threading.Lock()
        last_called = [0.0]

        @wraps(func)
        def wrapper(*args, **kwargs) -> None:
            with lock:
                elapsed_time = time.monotonic() - last_called[0]
                if elapsed_time >= seconds:
                    last_called[0] = time.monotonic()
                    return func(*args, **kwargs)
                else:
                    LOGGER.warning("Rate limited a function.", func=func.__name__)
                    if on_done_arg_name and on_done_arg_name in kwargs:
                        LOGGER.info("Calling callback.", callback_name=on_done_arg_name)
                        on_done = kwargs[on_done_arg_name]
                        if on_done:
                            on_done()

        return wrapper

    return decorator
