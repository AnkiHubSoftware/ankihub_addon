from aqt.qt import (
    QDialog,
    QEvent,
    QMoveEvent,
    QPoint,
    QResizeEvent,
    Qt,
    QWidget,
    QWindowStateChangeEvent,
    qconnect,
)


class OverlayDialog(QDialog):
    def __init__(self, parent: QWidget, target: QWidget) -> None:
        self._tracked_widgets = set()
        super().__init__(parent, Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.target = target
        self.setup_ui()
        self._install_event_filter()
        self._position_relative_to_target()
        qconnect(self.finished, self._on_finished)

    def setup_ui(self) -> None:
        pass

    def _install_event_filter(self) -> None:
        self._tracked_widgets.add(self.target)
        self._tracked_widgets.add(self.parentWidget())
        self.target.installEventFilter(self)
        self.parentWidget().installEventFilter(self)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:
        if obj in self._tracked_widgets:
            if isinstance(event, (QMoveEvent, QResizeEvent, QWindowStateChangeEvent)):
                self._position_relative_to_target()
        return super().eventFilter(obj, event)

    def _position_relative_to_target(self) -> None:
        target_global_pos = self.target.mapToGlobal(self.target.rect().topLeft())
        target_size = self.target.size()
        dialog_size = self.size()
        target_center_global_x = target_global_pos.x() + target_size.width() // 2
        target_center_global_y = target_global_pos.y() + target_size.height() // 2
        dialog_center_global_x = target_center_global_x - dialog_size.width() // 2
        dialog_center_global_y = target_center_global_y - dialog_size.height() // 2
        dialog_global_pos = QPoint(dialog_center_global_x, dialog_center_global_y)
        x = dialog_global_pos.x()
        y = dialog_global_pos.y()
        self.move(x, y)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_relative_to_target()

    def _on_finished(self) -> None:
        for widget in self._tracked_widgets:
            if widget:
                widget.removeEventFilter(self)
        self._tracked_widgets.clear()
