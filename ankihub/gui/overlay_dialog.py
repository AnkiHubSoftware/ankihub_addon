from typing import Optional, Set

from aqt.qt import (
    QDialog,
    QEvent,
    QMoveEvent,
    QObject,
    QResizeEvent,
    Qt,
    QWidget,
    QWindowStateChangeEvent,
    qconnect,
)


class OverlayDialog(QDialog):
    def __init__(self, parent: QWidget, target: QWidget) -> None:
        self._tracked_widgets: Set[QWidget] = set()
        super().__init__(parent, Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.target = target
        self.setup_ui()
        self._install_event_filter()
        self.on_position()
        qconnect(self.finished, self._on_finished)

    def setup_ui(self) -> None:
        pass

    def _install_event_filter(self) -> None:
        self._tracked_widgets.add(self.target)
        self._tracked_widgets.add(self.parentWidget())
        self.target.installEventFilter(self)
        self.parentWidget().installEventFilter(self)

    def eventFilter(self, obj: Optional[QObject], event: QEvent) -> bool:
        if obj in self._tracked_widgets:
            if isinstance(event, (QMoveEvent, QResizeEvent, QWindowStateChangeEvent)):
                self.on_position()
        return super().eventFilter(obj, event)

    def on_position(self) -> None:
        self.setGeometry(self.parentWidget().geometry())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.on_position()

    def _on_finished(self) -> None:
        for widget in self._tracked_widgets:
            if widget:
                widget.removeEventFilter(self)
        self._tracked_widgets.clear()
