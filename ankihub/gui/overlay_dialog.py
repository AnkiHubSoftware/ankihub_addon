from typing import Optional, Set, Union

import aqt
from anki.utils import is_mac
from aqt.browser import Browser
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
            geom = self.element.rect()
            top_left = self.element.mapToGlobal(geom.topLeft())
            bottom_right = self.element.mapToGlobal(geom.bottomRight())
            return QRect(top_left, bottom_right)
        else:
            top_left = self.parent.mapToGlobal(self.element.topLeft())
            bottom_right = self.parent.mapToGlobal(self.element.bottomRight())
            return QRect(top_left, bottom_right)


class OverlayDialog(QDialog):
    def __init__(self, parent: QWidget, target: Optional[OverlayTarget]) -> None:
        self._tracked_widgets: Set[Union[QWidget, OverlayTarget]] = set()
        self._browser_search_focus_policy: Optional[Qt.FocusPolicy] = None
        window_flags = Qt.WindowType.FramelessWindowHint
        if is_mac:
            # On macOS a window-modal QDialog is rendered as a native "sheet"
            # with an opaque background, which defeats the translucent overlay.
            # Keep the overlay above its parent with a stays-on-top hint instead.
            window_flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(parent, window_flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.target = target
        self.setup_ui()
        self._install_event_filter()
        self._configure_browser_focus(parent)
        self.on_position()
        qconnect(self.finished, self._on_finished)

    def setup_ui(self) -> None:
        pass

    @staticmethod
    def _browser_from_widget(widget: Optional[QWidget]) -> Optional[Browser]:
        if widget is None:
            return None
        window = widget if isinstance(widget, Browser) else widget.window()
        return window if isinstance(window, Browser) else None

    def _configure_browser_focus(self, parent: QWidget) -> None:
        browser = self._browser_from_widget(parent)
        if browser is None:
            return
        search_edit = browser.form.searchEdit
        self._browser_search_focus_policy = search_edit.focusPolicy()
        search_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        browser.clearFocus()

    def _restore_browser_focus(self) -> None:
        if self._browser_search_focus_policy is None:
            return
        browser = self._browser_from_widget(self.parentWidget())
        if browser is not None:
            browser.form.searchEdit.setFocusPolicy(self._browser_search_focus_policy)
        self._browser_search_focus_policy = None

    def _install_event_filter(self) -> None:
        if self.target:
            self._tracked_widgets.add(self.target)
            self.target.installEventFilter(self)
        parent = self.parentWidget()
        self._tracked_widgets.add(parent)
        parent.installEventFilter(self)
        browser = self._browser_from_widget(parent)
        if browser is not None:
            search_edit = browser.form.searchEdit
            self._tracked_widgets.add(search_edit)
            search_edit.installEventFilter(self)

    def _focus_overlay(self) -> None:
        pass

    def _bring_to_front(self) -> None:
        self.on_position()
        self.raise_()
        self._focus_overlay()

    def eventFilter(self, obj: Optional[QObject], event: QEvent) -> bool:
        if obj in self._tracked_widgets and event is not None:
            if isinstance(event, (QMoveEvent, QResizeEvent, QWindowStateChangeEvent)):
                self.on_position()
            elif event.type() == QEvent.Type.WindowActivate:
                self._bring_to_front()
            elif event.type() == QEvent.Type.FocusIn:
                browser = self._browser_from_widget(self.parentWidget())
                if browser is not None and obj is browser.form.searchEdit:
                    browser.form.searchEdit.clearFocus()
                    self._bring_to_front()
                    return True
        return super().eventFilter(obj, event)

    def on_position(self) -> None:
        self.setGeometry(self.parentWidget().geometry())
        self.raise_()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._bring_to_front()
        if is_mac:
            aqt.mw.progress.single_shot(0, self._bring_to_front)

    def _on_finished(self) -> None:
        self._restore_browser_focus()
        for widget in self._tracked_widgets:
            if widget:
                widget.removeEventFilter(self)
        self._tracked_widgets.clear()
