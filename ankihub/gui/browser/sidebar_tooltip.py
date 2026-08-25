"""Wires the generic :class:`~ankihub.gui.browser.rich_tooltip.RichTooltip` onto the AnkiHub
browser sidebar.

A sidebar item opts into an interactive tooltip by setting the ``RICH_TOOLTIP_ATTR`` attribute
to an HTML string. This module supplies the adapter that maps a cursor position to the hovered
item's HTML and row rect; all the tooltip behavior/styling lives in ``rich_tooltip``.
"""

from typing import Optional, Tuple

from aqt.browser.sidebar.tree import SidebarTreeView
from aqt.qt import QPoint, QRect

from .rich_tooltip import RichTooltip

# Attribute set on a SidebarItem to opt it into the rich tooltip; its value is the HTML.
RICH_TOOLTIP_ATTR = "_ankihub_rich_tooltip_html"


def _sidebar_target_at(sidebar: SidebarTreeView, global_pos: QPoint) -> Optional[Tuple[str, QRect]]:
    """Map a global cursor position to the hovered sidebar item's tooltip HTML and global row rect."""
    model = sidebar.model()
    if model is None:
        return None
    viewport = sidebar.viewport()
    index = sidebar.indexAt(viewport.mapFromGlobal(global_pos))
    if not index.isValid():
        return None
    item = model.item_for_index(index)
    html = getattr(item, RICH_TOOLTIP_ATTR, None)
    if not html:
        return None
    row_rect = sidebar.visualRect(index)
    return html, QRect(viewport.mapToGlobal(row_rect.topLeft()), row_rect.size())


def setup_sidebar_rich_tooltip(sidebar: SidebarTreeView) -> None:
    """Install the interactive tooltip on a browser sidebar (idempotent per sidebar)."""
    if getattr(sidebar, "_ankihub_tooltip", None) is not None:
        return
    viewport = sidebar.viewport()
    sidebar._ankihub_tooltip = RichTooltip(  # type: ignore[attr-defined]
        viewport, lambda pos: _sidebar_target_at(sidebar, pos)
    )
