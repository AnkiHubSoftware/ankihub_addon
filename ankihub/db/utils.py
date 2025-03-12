import threading
from typing import Optional, Type


class TimedLock:
    """Thread lock with a timeout. Raises a RuntimeError if the lock could not be acquired within the timeout."""

    def __init__(self, timeout_seconds: float):
        self.lock: threading.RLock = threading.RLock()
        self.timeout_seconds: float = timeout_seconds

    def __enter__(self) -> threading.RLock:
        acquired = self.lock.acquire(timeout=self.timeout_seconds)
        if not acquired:
            raise RuntimeError(  # pragma: no cover
                "Could not acquire lock within timeout"
            )
        return self.lock

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> None:
        self.lock.release()
