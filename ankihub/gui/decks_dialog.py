"""Dialog for managing subscriptions to AnkiHub decks and deck-specific settings."""
import uuid
from concurrent.futures import Future
from typing import List, Optional
from uuid import UUID

import aqt
from anki.collection import OpChanges
from aqt import gui_hooks
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    Qt,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import openLink, showInfo, showText, tooltip

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import config, url_deck_base, url_decks, url_help
from .deck_options_dialog import DeckOptionsDialog
from .operations.deck_installation import download_and_install_decks
from .utils import ask_user


class SubscribedDecksDialog(QDialog):
    _window: Optional["SubscribedDecksDialog"] = None
    silentlyClose = True

    def __init__(self):
        super(SubscribedDecksDialog, self).__init__()
        self.client = AnkiHubClient()
        self.setWindowTitle("Subscribed AnkiHub Decks")
        self._setup_ui()
        self._on_item_selection_changed()
        self._refresh_decks_list()

        if not config.is_logged_in():
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def _setup_ui(self):
        self.box_top = QVBoxLayout()
        self.box_above = QHBoxLayout()
        self.box_right = QVBoxLayout()

        self.decks_list = QListWidget()
        qconnect(self.decks_list.itemSelectionChanged, self._on_item_selection_changed)

        self.browse_btn = QPushButton("Browse Decks")
        self.box_right.addWidget(self.browse_btn)
        qconnect(self.browse_btn.clicked, lambda: openLink(url_decks()))

        self.unsubscribe_btn = QPushButton("Unsubscribe")
        self.box_right.addWidget(self.unsubscribe_btn)
        qconnect(self.unsubscribe_btn.clicked, self._on_unsubscribe)

        self.open_web_btn = QPushButton("Open on AnkiHub")
        self.box_right.addWidget(self.open_web_btn)
        qconnect(self.open_web_btn.clicked, self._on_open_web)

        self.open_deck_options_btn = QPushButton("Open Deck Options")
        self.box_right.addWidget(self.open_deck_options_btn)
        qconnect(self.open_deck_options_btn.clicked, self._on_open_deck_options)

        self.box_right.addStretch(1)

        self.setLayout(self.box_top)
        self.box_top.addLayout(self.box_above)
        self.box_above.addWidget(self.decks_list)
        self.box_above.addLayout(self.box_right)

    def _refresh_decks_list(self) -> None:
        self.decks_list.clear()
        for deck in self.client.get_deck_subscriptions():
            name = deck.name
            if deck.is_user_relation_owner:
                item = QListWidgetItem(f"{name} (Created by you)")
            elif deck.is_user_relation_maintainer:
                item = QListWidgetItem(f"{name} (Maintained by you)")
            else:
                item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, deck.ah_did)
            self.decks_list.addItem(item)

    def _refresh_anki(self) -> None:
        op = OpChanges()
        op.deck = True
        op.browser_table = True
        op.browser_sidebar = True
        op.study_queues = True
        gui_hooks.operation_did_execute(op, handler=None)

    def _on_add(self) -> None:
        SubscribeDialog().exec()

        self._refresh_decks_list()
        self._refresh_anki()

    def _select_deck(self, ah_did: uuid.UUID):
        deck_item = next(
            (
                item
                for i in range(self.decks_list.count())
                if (item := self.decks_list.item(i)).data(Qt.ItemDataRole.UserRole)
                == ah_did
            ),
            None,
        )
        if deck_item is not None:
            self.decks_list.setCurrentItem(deck_item)

    def _on_unsubscribe(self) -> None:
        deck_names = self._selected_decks_names()
        if len(deck_names) == 0:
            return

        deck_names_text = ", ".join(deck_names)
        confirm = ask_user(
            f"Unsubscribe from deck {deck_names_text}?\n\n"
            "The deck will remain in your collection, but it will no longer sync with AnkiHub.",
            title="Unsubscribe AnkiHub Deck",
        )
        if not confirm:
            return

        for ah_did in self._selected_decks_ah_dids():
            unsubscribe_from_deck_and_uninstall(ah_did)

        tooltip("Unsubscribed from AnkiHub Deck.", parent=aqt.mw)
        self._refresh_decks_list()

    def _on_open_web(self) -> None:
        for ah_did in self._selected_decks_ah_dids():
            openLink(f"{url_deck_base()}/{ah_did}")

    def _on_open_deck_options(self) -> None:
        ah_did = self._selected_decks_ah_dids()[0]
        DeckOptionsDialog(ah_did).exec()

    def _selected_decks_ah_dids(self) -> List[UUID]:
        selection = self.decks_list.selectedItems()
        result = [item.data(Qt.ItemDataRole.UserRole) for item in selection]
        return result

    def _selected_decks_names(self) -> List[str]:
        selection = self.decks_list.selectedItems()
        result = [item.text() for item in selection]
        return result

    def _on_item_selection_changed(self) -> None:
        one_deck_selected: bool = len(self._selected_decks_ah_dids()) == 1
        if one_deck_selected:
            ah_did = self._selected_decks_ah_dids()[0]
            is_deck_installed = bool(config.deck_config(ah_did))
        else:
            is_deck_installed = False

        self.unsubscribe_btn.setEnabled(one_deck_selected)
        self.open_web_btn.setEnabled(one_deck_selected)
        self.open_deck_options_btn.setEnabled(one_deck_selected and is_deck_installed)

    @classmethod
    def display_subscribed_decks_dialog(cls):
        if cls._window is None:
            cls._window = cls()
        else:
            cls._window._refresh_decks_list()
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()
        return cls._window


class SubscribeDialog(QDialog):
    silentlyClose = True

    def __init__(self):
        super(SubscribeDialog, self).__init__()

        self.thread = None  # type: ignore
        self.box_top = QVBoxLayout()
        self.box_mid = QHBoxLayout()
        self.box_left = QVBoxLayout()
        self.box_right = QVBoxLayout()

        self.deck_id_box = QHBoxLayout()
        self.deck_id_box_label = QLabel("Deck ID:")
        self.deck_id_box_text = QLineEdit("", self)
        self.deck_id_box_text.setMinimumWidth(300)
        self.deck_id_box.addWidget(self.deck_id_box_label)
        self.deck_id_box.addWidget(self.deck_id_box_text)
        self.box_left.addLayout(self.deck_id_box)

        self.box_mid.addLayout(self.box_left)
        self.box_mid.addSpacing(20)
        self.box_mid.addLayout(self.box_right)

        self.buttonbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel  # type: ignore
        )
        self.buttonbox.button(QDialogButtonBox.StandardButton.Ok).setText("Subscribe")
        self.browse_btn = self.buttonbox.addButton(
            "Browse Decks", QDialogButtonBox.ButtonRole.ActionRole
        )
        qconnect(self.browse_btn.clicked, self._on_browse_deck)
        qconnect(self.buttonbox.accepted, self._subscribe)
        self.buttonbox.rejected.connect(self.close)

        self.instructions_label = QLabel(
            "<center>Copy/Paste a Deck ID from AnkiHub.net/decks to subscribe.</center>"
        )
        # Add all widgets to top layout.
        self.box_top.addWidget(self.instructions_label)
        self.box_top.addSpacing(10)
        self.box_top.addLayout(self.box_mid)
        self.box_top.addStretch(1)
        self.box_top.addWidget(self.buttonbox)
        self.setLayout(self.box_top)

        self.setMinimumWidth(500)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWindowTitle("Subscribe to AnkiHub Deck")

        self.client = AnkiHubClient()
        if not config.is_logged_in():
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def _subscribe(self) -> None:
        ah_did_str = self.deck_id_box_text.text().strip()

        try:
            ah_did = uuid.UUID(ah_did_str)
        except ValueError:
            showInfo(
                "The format of the Deck ID is invalid. Please make sure you copied the Deck ID correctly."
            )
            return

        if ah_did in config.deck_ids():
            showText(
                f"You've already subscribed to deck {ah_did}. "
                "Syncing with AnkiHub will happen automatically everytime you "
                "restart Anki. You can manually sync with AnkiHub from the AnkiHub "
                f"menu. See {url_help()} for more details."
            )
            self.close()
            return

        confirmed = ask_user(
            f"Would you like to proceed with downloading and installing the deck? "
            f"Your personal collection will be modified.<br><br>"
            f"See <a href='{url_help()}'>{url_help()}</a> for details.",
            title="Please confirm to proceed.",
        )
        if not confirmed:
            return

        def on_done(future: Future) -> None:
            future.result()

            self.accept()

        download_and_install_decks([ah_did], on_done=on_done)

    def _on_browse_deck(self) -> None:
        openLink(url_decks())
