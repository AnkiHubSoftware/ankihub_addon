"""Interactive rich-text tooltip for AnkiHub browser sidebar items.

Anki's sidebar only supports a plain ``QToolTip`` (``ToolTipRole``) whose links aren't
clickable and which vanishes as soon as the cursor moves toward it. This module adds a
small frameless popup that opens on hover, stays open while the cursor is over the row
*or* the popup itself, and contains clickable links (opened in the default browser).

It attaches non-invasively via an event filter on the sidebar viewport - no custom item
delegate, so it doesn't touch Anki's own row rendering. A sidebar item opts in by setting
the ``RICH_TOOLTIP_ATTR`` attribute to an HTML string.

Hover state is driven by polling the global cursor position rather than Enter/Leave events,
because a non-activating popup window doesn't reliably receive mouse-move events on macOS.
(For the same reason there's no hover cursor change on the link: macOS won't apply a cursor
over a non-key window. The link is still styled and clickable.)
"""

from typing import Optional, Tuple

from anki.utils import is_mac
from aqt import colors
from aqt.browser.sidebar.item import SidebarItem
from aqt.browser.sidebar.tree import SidebarTreeView
from aqt.qt import (
    QApplication,
    QCursor,
    QEvent,
    QFrame,
    QLabel,
    QModelIndex,
    QObject,
    QPainter,
    QPaintEvent,
    QPen,
    QPoint,
    QStyle,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    sip,
)
from aqt.theme import theme_manager

# Attribute set on a SidebarItem to opt it into the rich tooltip; its value is the HTML.
RICH_TOOLTIP_ATTR = "_ankihub_rich_tooltip_html"

# Fallback for how long (ms) the cursor must rest on the row before the tooltip appears. The
# platform's native tooltip wake-up delay (SH_ToolTip_WakeUpDelay) is used when available.
_SHOW_DELAY_MS = 700

# How often (ms) to poll the cursor position to decide whether to keep the tooltip open.
_WATCH_INTERVAL_MS = 100

# Extra px around the row/popup treated as "still hovering", to bridge the gap between them.
_SAFE_MARGIN = 8

# Width the tooltip text wraps at.
_MAX_WIDTH = 320


def _native_tooltip_delay_ms() -> int:
    """The platform's native tooltip wake-up delay, so our tooltip feels the same."""
    style = QApplication.style()
    delay = style.styleHint(QStyle.StyleHint.SH_ToolTip_WakeUpDelay) if style is not None else 0
    return delay if delay > 0 else _SHOW_DELAY_MS


class _RichTooltipPopup(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("ankihubRichTooltip")

        self._label = QLabel(self)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setWordWrap(True)
        self._label.setOpenExternalLinks(True)
        # Links clickable, but the text itself isn't selectable - like a native tooltip.
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._label.setMaximumWidth(_MAX_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(self._label)

        # Match Anki's native tooltip colors (see stylesheets.py: QToolTip uses FG / CANVAS).
        # The border is drawn in paintEvent (not here) so it stays a single device pixel.
        self.setStyleSheet(
            f"#ankihubRichTooltip {{ background-color: {theme_manager.var(colors.CANVAS)}; }}"
            f" QLabel {{ color: {theme_manager.var(colors.FG)}; background: transparent; }}"
        )

    def set_html(self, html: str) -> None:
        self._label.setText(html)
        self.adjustSize()

    def paintEvent(self, event: Optional[QPaintEvent]) -> None:
        super().paintEvent(event)
        # Draw a square 1-device-pixel border like a native QToolTip. A cosmetic pen (width 0)
        # stays 1px regardless of display scaling, unlike a QSS "1px" (= 1 logical pixel).
        # macOS native tooltips use a muted gray border (FG_FAINT); other platforms (Fusion /
        # Windows styles) draw the frame in the text color (FG).
        painter = QPainter(self)
        pen = QPen(theme_manager.qcolor(colors.FG_FAINT if is_mac else colors.FG))
        pen.setCosmetic(True)
        pen.setWidth(0)
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class SidebarTooltipController(QObject):
    """Shows an interactive popup when the cursor hovers a sidebar item that opted in."""

    def __init__(self, sidebar: SidebarTreeView) -> None:
        super().__init__(sidebar)
        self._sidebar = sidebar
        self._popup = _RichTooltipPopup(sidebar)
        self._current_item: Optional[SidebarItem] = None
        self._pending_item: Optional[SidebarItem] = None

        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(_native_tooltip_delay_ms())
        self._show_timer.timeout.connect(self._show_pending)

        # Repeating poll that keeps the tooltip open while the cursor is over the row or popup
        # and hides it otherwise. Polling the global cursor position is more reliable than
        # Enter/Leave events across two top-level windows (which race, notably on macOS).
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(_WATCH_INTERVAL_MS)
        self._watch_timer.timeout.connect(self._watch)

        viewport = sidebar.viewport()
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)

    def _alive(self) -> bool:
        """False once the browser (and its sidebar/popup C++ objects) has been destroyed."""
        return not (sip.isdeleted(self._sidebar) or sip.isdeleted(self._popup))

    def eventFilter(self, obj: Optional[QObject], event: Optional[QEvent]) -> bool:
        if event is None or not self._alive():
            return False
        if obj is self._sidebar.viewport():
            etype = event.type()
            if etype == QEvent.Type.MouseMove:
                self._on_viewport_move()
            elif etype == QEvent.Type.Leave:
                # Cancel a not-yet-shown tooltip when leaving the sidebar. A visible one is
                # managed by the watch timer (the cursor may be heading toward the popup).
                self._show_timer.stop()
                self._pending_item = None
        return False

    def _index_and_item_under_cursor(self) -> Tuple[QModelIndex, Optional[SidebarItem]]:
        model = self._sidebar.model()
        index = self._sidebar.indexAt(self._sidebar.viewport().mapFromGlobal(QCursor.pos()))
        item = model.item_for_index(index) if (model is not None and index.isValid()) else None
        return index, item

    def _on_viewport_move(self) -> None:
        _, item = self._index_and_item_under_cursor()
        html = getattr(item, RICH_TOOLTIP_ATTR, None) if item is not None else None

        if html:
            if item is self._current_item and self._popup.isVisible():
                return
            # Wait for the cursor to rest on the row before showing, like a native tooltip.
            if item is not self._pending_item:
                self._pending_item = item
                self._show_timer.start()
        else:
            # Over a non-tooltip row: cancel any pending show. A visible popup is left to the
            # watch timer, so moving across rows toward the popup doesn't dismiss it abruptly.
            self._pending_item = None
            self._show_timer.stop()

    def _show_pending(self) -> None:
        if not self._alive():
            return
        index, item = self._index_and_item_under_cursor()
        # The cursor may have moved off the row during the delay.
        if item is None or item is not self._pending_item:
            return
        html = getattr(item, RICH_TOOLTIP_ATTR, None)
        if not html:
            return
        self._current_item = item
        self._popup.set_html(html)
        rect = self._sidebar.visualRect(index)
        self._popup.move(self._sidebar.viewport().mapToGlobal(rect.bottomLeft()) + QPoint(0, 2))
        self._popup.show()
        self._watch_timer.start()

    def _watch(self) -> None:
        if not self._alive() or not self._popup.isVisible():
            self._watch_timer.stop()
            return
        if not self._cursor_in_safe_zone():
            self._hide()

    def _cursor_in_safe_zone(self) -> bool:
        pos = QCursor.pos()
        margin = _SAFE_MARGIN
        if self._popup.frameGeometry().adjusted(-margin, -margin, margin, margin).contains(pos):
            return True
        # Still hovering the row the tooltip belongs to?
        _, item = self._index_and_item_under_cursor()
        return item is not None and item is self._current_item

    def _hide(self) -> None:
        self._watch_timer.stop()
        # Also stop a pending show so hiding the current popup can't strand a just-scheduled one.
        self._show_timer.stop()
        self._popup.hide()
        self._current_item = None
        self._pending_item = None


def setup_sidebar_rich_tooltip(sidebar: SidebarTreeView) -> None:
    """Install the interactive tooltip on a browser sidebar (idempotent per sidebar)."""
    if getattr(sidebar, "_ankihub_tooltip_controller", None) is not None:
        return
    sidebar._ankihub_tooltip_controller = SidebarTooltipController(sidebar)  # type: ignore[attr-defined]
