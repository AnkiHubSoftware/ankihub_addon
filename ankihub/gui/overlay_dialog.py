from typing import Optional, Set, Union

from aqt.qt import (
    QDialog,
    QEvent,
    QMoveEvent,
    QObject,
    QRect,
    QResizeEvent,
    Qt,
    QWidget,
    QWindowStateChangeEvent,
    qconnect,
)


class OverlayTarget:
    def __init__(self, parent: QWidget, element: Union[QWidget, QRect]) -> None:
        self.parent = parent
        self.element = element

    def installEventFilter(self, obj: QObject) -> None:
        if isinstance(self.element, QWidget):
            self.element.installEventFilter(obj)

    def removeEventFilter(self, obj: QObject) -> None:
        if isinstance(self.element, QWidget):
            self.element.removeEventFilter(obj)

    def window(self) -> Optional[QWidget]:
        if isinstance(self.element, QWidget):
            return self.element.window()
        return None

    def rect(self) -> QRect:
        if isinstance(self.element, QWidget):
            geom = self.element.contentsRect()
            top_left = self.element.parentWidget().mapToGlobal(geom.topLeft())
            bottom_right = self.element.parentWidget().mapToGlobal(geom.bottomRight())
            return QRect(top_left, bottom_right)
        else:
            top_left = self.parent.mapToGlobal(self.element.topLeft())
            bottom_right = self.parent.mapToGlobal(self.element.bottomRight())
            return QRect(top_left, bottom_right)


class OverlayDialog(QDialog):
    def __init__(self, parent: QWidget, target: Optional[OverlayTarget]) -> None:
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
        if self.target:
            self._tracked_widgets.add(self.target)
            self.target.installEventFilter(self)
        self._tracked_widgets.add(self.parentWidget())
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
