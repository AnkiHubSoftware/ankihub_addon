"""Stable dialog parent shared across the steps of an async UI flow.

When a multi-step flow (sync, install, future workflows) launches from a dialog and shows further
dialogs/progress over the course of the flow, resolving the parent independently at each step is
fragile: a popup constructed while a just-closed ``ProgressDialog`` is still the active window
falls back to ``aqt.mw``. On macOS that renders it as a sheet attached to the main window, hidden
behind whichever dialog launched the flow (e.g. an ApplicationModal Deck Management dialog) —
a hidden-sheet deadlock on macOS.

Instead capture the parent once at the flow entry point, when the launching dialog is reliably
the active window, and reuse it for every dialog/progress in the flow.

Flows are assumed single-flight (Anki's modal-blocking and rate limits make overlap impractical),
so a singleton holder is safe — the same assumption ``sync_state`` already makes.
"""

from dataclasses import dataclass
from typing import Optional

import aqt
from aqt.qt import QWidget, sip

from .utils import active_window_or_mw


@dataclass
class _DialogParentState:
    _captured: Optional[QWidget] = None

    def capture(self) -> QWidget:
        """Capture the active window as the parent for dialogs in the current flow.

        Call this once at the entry point of an async UI flow, before any progress popup is shown,
        so the active window is still the launching dialog (if any) rather than a transient
        ProgressDialog.
        """
        self._captured = active_window_or_mw()
        return self.get()

    def get(self) -> QWidget:
        """The parent captured for the current flow, or ``active_window_or_mw()`` if it is unusable.

        The captured widget may have been closed since capture. ``DeckManagementDialog`` is a
        singleton that is hidden (not deleted) on close, so we check both ``sip.isdeleted`` and
        ``isVisible``.
        """
        parent = self._captured
        if parent is not None and not sip.isdeleted(parent) and (parent is aqt.mw or parent.isVisible()):
            return parent
        return active_window_or_mw()


dialog_parent_state = _DialogParentState()
