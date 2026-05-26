"""A stable parent widget for dialogs and progress popups shown during the AnkiHub sync flow.

The sync flow is asynchronous and multi-step. If each dialog/progress popup resolved its own
parent at construction time (via ``active_window_or_mw()``), a popup created while a just-closed
``ProgressDialog`` is still the active window would fall back to ``aqt.mw``. On macOS that renders
it as a sheet attached to the main window, hidden behind whichever dialog launched the sync (e.g.
the ApplicationModal Deck Management dialog) — the NRT-764 deadlock.

Instead we capture the parent once, at the start of the sync, when the launching dialog is reliably
the active window, and reuse it for every dialog/progress in the flow.

Sync is single-flight (rate-limited, and Anki operations serialize), so a module-level holder is
safe here — the same assumption the sync flow already makes with its other shared state.
"""

from typing import Optional

import aqt
from aqt.qt import QWidget, sip

from .utils import active_window_or_mw

_sync_dialog_parent: Optional[QWidget] = None


def capture_sync_dialog_parent() -> QWidget:
    """Capture and remember the parent to use for dialogs shown during the current sync.

    Call this once at the very start of the sync flow, before any progress popup is shown, so the
    active window is still the dialog (if any) that launched the sync.
    """
    global _sync_dialog_parent
    _sync_dialog_parent = active_window_or_mw()
    return sync_dialog_parent()


def sync_dialog_parent() -> QWidget:
    """The parent captured for the current sync, or ``active_window_or_mw()`` if it is unusable.

    The captured widget may have been closed since capture. ``DeckManagementDialog`` is a singleton
    that is hidden (not deleted) on close, so we check both ``sip.isdeleted`` and ``isVisible``.
    """
    parent = _sync_dialog_parent
    if parent is not None and not sip.isdeleted(parent) and (parent is aqt.mw or parent.isVisible()):
        return parent
    return active_window_or_mw()
