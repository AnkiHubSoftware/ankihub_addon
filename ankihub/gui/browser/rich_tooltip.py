"""A reusable interactive tooltip: it can host clickable links and stays open while the
cursor moves onto it - neither of which a plain ``QToolTip`` supports.

Attach a :class:`RichTooltip` to a widget and give it a ``target_at`` callback. For the
current global cursor position the callback returns ``(html, anchor_rect)`` - where
``anchor_rect`` is in global screen coordinates and the tooltip opens just below it - or
``None`` when there's nothing to show. The tooltip handles the native wake-up delay, opening
and closing by polling the cursor position (so it stays open when the cursor moves onto it),
and styling that matches Anki's native tooltips across themes/platforms.

Implementation notes:
- It's a frameless, non-activating popup window, attached non-invasively (an event filter on
  the watched widget) so it doesn't interfere with that widget's own rendering.
- Hover state is polled rather than driven by Enter/Leave events, because a non-activating
  popup window doesn't reliably receive mouse-move events on macOS. For the same reason there's
  no hover cursor change on links: macOS won't apply a cursor over a non-key window. Links are
  still styled and clickable.
"""

import re
from typing import Callable, Optional, Tuple

from anki.utils import is_mac
from aqt import colors
from aqt.qt import (
    QApplication,
    QCursor,
    QEvent,
    QFrame,
    QLabel,
    QObject,
    QPainter,
    QPaintEvent,
    QPoint,
    QRect,
    QRectF,
    QStyle,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    sip,
)
from aqt.theme import theme_manager

# Callback: given the global cursor position, return the tooltip HTML and the anchor rect (in
# global coordinates) to position below, or None when there's nothing to show there.
TargetAt = Callable[[QPoint], Optional[Tuple[str, QRect]]]

# Fallback for how long (ms) the cursor must rest before the tooltip appears. The platform's
# native tooltip wake-up delay (SH_ToolTip_WakeUpDelay) is used when available.
_SHOW_DELAY_MS = 700

# How often (ms) to poll the cursor position to decide whether to keep the tooltip open.
_WATCH_INTERVAL_MS = 100

# Extra px around the target/popup treated as "still hovering", to bridge the gap between them.
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

        # Only the label text color is set via QSS. The background is painted in paintEvent
        # (not via QSS) because QSS background-color isn't reliably applied to a top-level
        # window - relying on it let the system palette (dark desktop) show through in light mode.
        self._apply_label_color()

    def _apply_label_color(self) -> None:
        self._label.setStyleSheet(f"QLabel {{ color: {theme_manager.var(colors.FG)}; background: transparent; }}")

    def set_html(self, html: str) -> None:
        self._apply_label_color()
        # Force the link color inline. The default <a> color (palette Link role) renders too
        # light on light themes and too dark on dark; a widget stylesheet can't target links and
        # would override a palette change anyway. An inline style on the anchor is always honored.
        link_color = theme_manager.var(colors.FG_LINK)
        html = re.sub(r"<a (?![^>]*\bstyle=)", f'<a style="color: {link_color};" ', html)
        self._label.setText(html)
        self.adjustSize()

    def paintEvent(self, event: Optional[QPaintEvent]) -> None:
        # Fill the background and draw the border ourselves. Match Anki's native tooltip colors
        # (see stylesheets.py: QToolTip uses FG / CANVAS); colors are resolved here so a
        # mid-session theme switch is picked up.
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, theme_manager.qcolor(colors.CANVAS))
        # Border drawn as four filled edges (not a pen + drawRect, whose centered stroke makes the
        # right/bottom edges look thicker than the half-clipped left/top edges). macOS native
        # tooltips have a thin (1-device-pixel) muted-gray (FG_FAINT) border; other platforms
        # (Fusion / Windows) use a 1-logical-pixel border in the text color (FG). Thickness is in
        # logical px, so on macOS it's divided by the device pixel ratio to stay 1 physical pixel.
        border_color = theme_manager.qcolor(colors.FG_FAINT if is_mac else colors.FG)
        dpr = self.devicePixelRatioF() or 1.0
        t = (1.0 / dpr) if is_mac else 1.0
        w, h = rect.width(), rect.height()
        painter.fillRect(QRectF(0, 0, w, t), border_color)  # top
        painter.fillRect(QRectF(0, h - t, w, t), border_color)  # bottom
        painter.fillRect(QRectF(0, 0, t, h), border_color)  # left
        painter.fillRect(QRectF(w - t, 0, t, h), border_color)  # right
        painter.end()


class RichTooltip(QObject):
    """Interactive tooltip with clickable links that stays open while the cursor is over it.

    ``widget`` is watched for hover (mouse-move/leave) and parents the popup. ``target_at`` maps
    the global cursor position to ``(html, anchor_rect)`` or None (see :data:`TargetAt`).
    """

    def __init__(self, widget: QWidget, target_at: TargetAt) -> None:
        super().__init__(widget)
        self._widget = widget
        self._target_at = target_at
        self._popup = _RichTooltipPopup(widget)
        self._current_rect: Optional[QRect] = None
        self._pending_rect: Optional[QRect] = None

        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.setInterval(_native_tooltip_delay_ms())
        self._show_timer.timeout.connect(self._show_pending)

        # Repeating poll that keeps the tooltip open while the cursor is over the target or popup
        # and hides it otherwise. Polling the global cursor position is more reliable than
        # Enter/Leave events across two top-level windows (which race, notably on macOS).
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(_WATCH_INTERVAL_MS)
        self._watch_timer.timeout.connect(self._watch)

        widget.setMouseTracking(True)
        widget.installEventFilter(self)

    def _alive(self) -> bool:
        """False once the watched widget (and the popup) C++ objects have been destroyed."""
        return not (sip.isdeleted(self._widget) or sip.isdeleted(self._popup))

    def eventFilter(self, obj: Optional[QObject], event: Optional[QEvent]) -> bool:
        if event is None or not self._alive():
            return False
        if obj is self._widget:
            etype = event.type()
            if etype == QEvent.Type.MouseMove:
                self._on_move()
            elif etype == QEvent.Type.Leave:
                # Cancel a not-yet-shown tooltip when leaving the widget. A visible one is
                # managed by the watch timer (the cursor may be heading toward the popup).
                self._show_timer.stop()
                self._pending_rect = None
        return False

    def _on_move(self) -> None:
        target = self._target_at(QCursor.pos())
        if target is not None:
            _, rect = target
            if rect == self._current_rect and self._popup.isVisible():
                return
            # Wait for the cursor to rest on the target before showing, like a native tooltip.
            if rect != self._pending_rect:
                self._pending_rect = rect
                self._show_timer.start()
        else:
            # Over nothing with a tooltip: cancel any pending show. A visible popup is left to the
            # watch timer, so moving across the widget toward the popup doesn't dismiss it abruptly.
            self._pending_rect = None
            self._show_timer.stop()

    def _show_pending(self) -> None:
        if not self._alive():
            return
        target = self._target_at(QCursor.pos())
        if target is None:
            return
        html, rect = target
        # The cursor may have moved to a different target during the delay.
        if rect != self._pending_rect:
            return
        self._current_rect = rect
        self._popup.set_html(html)
        self._popup.move(rect.bottomLeft() + QPoint(0, 2))
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
        # Still hovering the target the tooltip belongs to?
        target = self._target_at(pos)
        return target is not None and target[1] == self._current_rect

    def _hide(self) -> None:
        self._watch_timer.stop()
        # Also stop a pending show so hiding the current popup can't strand a just-scheduled one.
        self._show_timer.stop()
        self._popup.hide()
        self._current_rect = None
        self._pending_rect = None
