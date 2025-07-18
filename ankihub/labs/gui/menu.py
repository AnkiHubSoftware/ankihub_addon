"""AnkiHub Labs menu setup."""

from aqt.qt import QAction, QMenu, qconnect

from ..secrets import open_secrets_dialog


def setup_labs_menu(parent_menu: QMenu) -> None:
    """Set up the AnkiHub Labs menu if labs are enabled."""
    # Create Labs submenu
    labs_menu = QMenu("🧪 AnkiHub Labs", parent_menu)

    # Add Secrets submenu
    secrets_action = QAction("🔑 Secrets", labs_menu)
    qconnect(secrets_action.triggered, open_secrets_dialog)
    labs_menu.addAction(secrets_action)

    parent_menu.addMenu(labs_menu)
