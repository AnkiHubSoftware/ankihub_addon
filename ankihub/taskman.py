from threading import Lock
from typing import Any, Callable, Dict

from aqt.qt import pyqtSignal, qconnect
from aqt.taskman import TaskManager

try:
    from PyQt6.QtTest import QSignalSpy  # type: ignore
except Exception:
    from PyQt5.QtTest import QSignalSpy  # type: ignore


class ExtendedTaskManger(TaskManager):

    _blocking_closures_pending = pyqtSignal()
    _blocking_closures_done = pyqtSignal()
    _blocking_closure_results: Dict[Callable, Any] = dict()

    def __init__(self, mw):
        super().__init__(mw)

        self._blocking_closures_lock = Lock()
        self._blocking_closures: list[Callable] = []
        qconnect(self._blocking_closures_pending, self._on_blocking_closures_pending)

    def run_blocking_on_main(self, closure: Callable) -> Any:
        with self._blocking_closures_lock:
            self._blocking_closures.append(closure)
        self._blocking_closures_pending.emit()  # type: ignore
        if QSignalSpy(self._blocking_closures_done).wait(100000000):  # type: ignore
            return self._blocking_closure_results[closure]
        else:
            raise RuntimeError("Timeout waiting for closure to complete")

    def _on_blocking_closures_pending(self) -> None:
        """Run any pending closures. This runs in the main thread."""
        with self._closures_lock:
            closures = self._blocking_closures
            self._blocking_closures = []

        for closure in closures:
            self._blocking_closure_results[closure] = closure()
            self._blocking_closures_done.emit()
