"""Dialog for managing subscriptions to AnkiHub decks and deck-specific settings."""
import uuid
from typing import Optional
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
from aqt.studydeck import StudyDeck
from aqt.utils import openLink, showInfo, showText, tooltip

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..db import ankihub_db
from ..settings import config, url_deck_base, url_decks, url_help
from ..subdecks import SUBDECK_TAG
from ..utils import undo_note_type_modfications
from .operations.deck_installation import download_and_install_decks
from .operations.subdecks import confirm_and_toggle_subdecks
from .utils import ask_user, set_tooltip_icon


class SubscribedDecksDialog(QDialog):
    _window: Optional["SubscribedDecksDialog"] = None
    silentlyClose = True

    def __init__(self):
        super(SubscribedDecksDialog, self).__init__()
        self.client = AnkiHubClient()
        self.setWindowTitle("Subscribed AnkiHub Decks")
        self.new_subscription_workflow_enabled = self.client.is_feature_flag_enabled(
            "new_subscription_workflow_enabled"
        )
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

        if self.new_subscription_workflow_enabled:
            self.browse_btn = QPushButton("Browse Decks")
            self.box_right.addWidget(self.browse_btn)
            qconnect(self.browse_btn.clicked, lambda: openLink(url_decks()))
        else:
            self.add_btn = QPushButton("Add")
            self.box_right.addWidget(self.add_btn)
            qconnect(self.add_btn.clicked, self._on_add)

        self.unsubscribe_btn = QPushButton("Unsubscribe")
        self.box_right.addWidget(self.unsubscribe_btn)
        qconnect(self.unsubscribe_btn.clicked, self._on_unsubscribe)

        self.open_web_btn = QPushButton("Open on AnkiHub")
        self.box_right.addWidget(self.open_web_btn)
        qconnect(self.open_web_btn.clicked, self._on_open_web)

        self.set_home_deck_btn = QPushButton("Set Home deck")
        self.set_home_deck_btn.setToolTip("New cards will be added to this deck.")
        set_tooltip_icon(self.set_home_deck_btn)
        qconnect(self.set_home_deck_btn.clicked, self._on_set_home_deck)
        self.box_right.addWidget(self.set_home_deck_btn)

        self.toggle_subdecks_btn = QPushButton("Enable Subdecks")
        self.toggle_subdecks_btn.setToolTip(
            "Toggle between the deck being organized into subdecks or not.<br>"
            f"This will only have an effect if notes in the deck have <b>{SUBDECK_TAG}</b> tags."
        )
        set_tooltip_icon(self.toggle_subdecks_btn)
        qconnect(self.toggle_subdecks_btn.clicked, self._on_toggle_subdecks)
        self.box_right.addWidget(self.toggle_subdecks_btn)

        self.box_right.addStretch(1)

        self.setLayout(self.box_top)
        self.box_top.addLayout(self.box_above)
        self.box_above.addWidget(self.decks_list)
        self.box_above.addLayout(self.box_right)

    def _refresh_decks_list(self) -> None:
        self.decks_list.clear()
        if self.new_subscription_workflow_enabled:
            self.decks_with_user_relation = self.client.get_decks_with_user_relation()
            for deck in self.decks_with_user_relation:
                name = deck.name
                if deck.is_user_relation_owner:
                    item = QListWidgetItem(f"{name} (Owner)")
                elif deck.is_user_relation_maintainer:
                    item = QListWidgetItem(f"{name} (Maintaining)")
                else:
                    item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, deck.ankihub_deck_uuid)
                self.decks_list.addItem(item)
        else:
            for ah_did in config.deck_ids():
                name = config.deck_config(ah_did).name
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, ah_did)
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
        items = self.decks_list.selectedItems()
        if len(items) == 0:
            return
        deck_names = [item.text() for item in items]
        deck_names_text = ", ".join(deck_names)
        confirm = ask_user(
            f"Unsubscribe from deck {deck_names_text}?\n\n"
            "The deck will remain in your collection, but it will no longer sync with AnkiHub.",
            title="Unsubscribe AnkiHub Deck",
        )
        if not confirm:
            return

        for item in items:
            ankihub_did: UUID = item.data(Qt.ItemDataRole.UserRole)
            if self.new_subscription_workflow_enabled:
                self.client.unsubscribe_from_deck(ankihub_did)
            config.unsubscribe_deck(ankihub_did)
            self._clear_deck_changes(ankihub_did)

        tooltip("Unsubscribed from AnkiHub Deck.", parent=aqt.mw)
        self._refresh_decks_list()

    def _clear_deck_changes(self, ankihub_did: UUID) -> None:
        mids = ankihub_db.note_types_for_ankihub_deck(ankihub_did)
        undo_note_type_modfications(mids)
        ankihub_db.remove_deck(ankihub_did)

    def _on_open_web(self) -> None:
        items = self.decks_list.selectedItems()
        if len(items) == 0:
            return

        for item in items:
            ankihub_id: UUID = item.data(Qt.ItemDataRole.UserRole)
            openLink(f"{url_deck_base()}/{ankihub_id}")

    def _on_set_home_deck(self):
        deck_names = self.decks_list.selectedItems()
        if len(deck_names) == 0:
            return

        deck_name = deck_names[0]
        ankihub_id: UUID = deck_name.data(Qt.ItemDataRole.UserRole)
        current_home_deck = aqt.mw.col.decks.get(config.deck_config(ankihub_id).anki_id)
        if current_home_deck is None:
            current = None
        else:
            current = current_home_deck["name"]

        def update_deck_config(ret: StudyDeck):
            if not ret.name:
                return

            anki_did = aqt.mw.col.decks.id(ret.name)
            config.set_home_deck(ankihub_did=ankihub_id, anki_did=anki_did)
            tooltip("Home deck updated.", parent=self)

        # this lets the user pick a deck
        StudyDeckWithoutHelpButton(
            aqt.mw,
            current=current,
            accept="Set Home Deck",
            title="Change Home Deck",
            parent=self,
            callback=update_deck_config,
        )

    def _on_toggle_subdecks(self):
        deck_items = self.decks_list.selectedItems()
        if len(deck_items) == 0:
            return

        deck_item = deck_items[0]
        ankihub_id: UUID = deck_item.data(Qt.ItemDataRole.UserRole)

        deck_config = config.deck_config(ankihub_id)
        if aqt.mw.col.decks.name_if_exists(deck_config.anki_id) is None:
            showInfo(
                (
                    f"Anki deck <b>{deck_config.name}</b> doesn't exist in your Anki collection.<br>"
                    "It might help to reset local changes to the deck first.<br>"
                    "(You can do that from the AnkiHub menu in the Anki browser.)"
                ),
            )
            return

        confirm_and_toggle_subdecks(ankihub_id)

        self._refresh_subdecks_button()

    def _refresh_subdecks_button(self):
        selection = self.decks_list.selectedItems()
        one_selected: bool = len(selection) == 1

        if not one_selected:
            self.toggle_subdecks_btn.setEnabled(False)
            return

        ankihub_did: UUID = selection[0].data(Qt.ItemDataRole.UserRole)
        using_subdecks = False
        if deck_from_config := config.deck_config(ankihub_did):
            using_subdecks = deck_from_config.subdecks_enabled
            self.toggle_subdecks_btn.setEnabled(True)
        else:
            self.toggle_subdecks_btn.setEnabled(False)
        self.toggle_subdecks_btn.setText(
            "Disable Subdecks" if using_subdecks else "Enable Subdecks"
        )

    def _on_item_selection_changed(self) -> None:
        selection = self.decks_list.selectedItems()
        one_selected: bool = len(selection) == 1
        is_deck_installed = False
        if one_selected:
            selected = selection[0]
            ankihub_did: UUID = selected.data(Qt.ItemDataRole.UserRole)
            is_deck_installed = bool(config.deck_config(ankihub_did))
            if self.new_subscription_workflow_enabled:
                deck_obj = next(
                    (
                        deck
                        for deck in self.decks_with_user_relation
                        if deck.ankihub_deck_uuid == ankihub_did
                    ),
                    None,
                )

        if self.new_subscription_workflow_enabled:
            self.unsubscribe_btn.setEnabled(
                one_selected and not deck_obj.is_user_relation_owner_or_maintainer
            )
        else:
            self.unsubscribe_btn.setEnabled(one_selected)

        self.open_web_btn.setEnabled(one_selected)
        self.set_home_deck_btn.setEnabled(one_selected and is_deck_installed)

        self._refresh_subdecks_button()

    @classmethod
    def display_subscribe_window(cls):
        if cls._window is None:
            cls._window = cls()
        else:
            cls._window._refresh_decks_list()
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()
        return cls._window


class StudyDeckWithoutHelpButton(StudyDeck):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.form.buttonBox.removeButton(
            self.form.buttonBox.button(QDialogButtonBox.StandardButton.Help)
        )


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

        download_and_install_decks([ah_did], on_success=self.accept)

    def _on_browse_deck(self) -> None:
        openLink(url_decks())