"""Dialog for managing subscriptions to AnkiHub decks and deck-specific settings."""
import uuid
from typing import Optional
from uuid import UUID

import aqt
from anki.collection import OpChanges
from aqt import gui_hooks
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    Qt,
    QVBoxLayout,
    qconnect,
)
from aqt.studydeck import StudyDeck
from aqt.theme import theme_manager
from aqt.utils import openLink, showInfo, showText, tooltip

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..gui.operations.deck_creation import create_collaborative_deck
from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..main.subdecks import SUBDECK_TAG, deck_contains_subdeck_tags
from ..settings import SuspendNewCardsOfExistingNotes, config, url_deck_base, url_decks
from .operations.subdecks import confirm_and_toggle_subdecks
from .utils import ask_user, clear_layout, tooltip_icon


class DeckManagementDialog(QDialog):
    _window: Optional["DeckManagementDialog"] = None
    silentlyClose = True

    def __init__(self):
        super(DeckManagementDialog, self).__init__()
        self.client = AnkiHubClient()
        self._setup_ui()
        self._refresh_decks_list()

        if not config.is_logged_in():
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def _setup_ui(self):
        self.setWindowTitle("AnkiHub | Deck Management")
        self.setMinimumWidth(640)
        self.setMinimumHeight(500)

        self.box_main = QVBoxLayout()
        self.setLayout(self.box_main)

        self.box_top = self._setup_box_top()
        self.box_main.addLayout(self.box_top)

        self.box_bottom = QHBoxLayout()
        self.box_main.addLayout(self.box_bottom)

        self.box_bottom_left = self._setup_box_bottom_left()
        self.box_bottom.addLayout(self.box_bottom_left)

        self.box_bottom.addSpacing(10)

        self.box_bottom_right = QVBoxLayout()
        self._refresh_box_bottom_right()
        self.box_bottom.addLayout(self.box_bottom_right)

    def _setup_box_top(self) -> QVBoxLayout:
        box = QVBoxLayout()

        self.box_top_buttons = QHBoxLayout()
        box.addLayout(self.box_top_buttons)

        self.browse_btn = QPushButton("🔗 Browse Decks")
        self.browse_btn.setStyleSheet("color: white; background-color: #306bec")

        self.box_top_buttons.addWidget(self.browse_btn)
        qconnect(self.browse_btn.clicked, lambda: openLink(url_decks()))

        self.create_btn = QPushButton("➕ Create Collaborative Deck")
        self.box_top_buttons.addWidget(self.create_btn)
        qconnect(self.create_btn.clicked, create_collaborative_deck)

        box.addSpacing(10)

        return box

    def _setup_box_bottom_left(self) -> QVBoxLayout:
        box = QVBoxLayout()
        self.decks_list_label = QLabel("<b>Subscribed AnkiHub Decks</b>")
        self.decks_list_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        box.addWidget(self.decks_list_label)

        box.addSpacing(5)

        self.decks_list = QListWidget()
        box.addWidget(self.decks_list)
        qconnect(self.decks_list.itemSelectionChanged, self._refresh_box_bottom_right)
        return box

    def _refresh_box_bottom_right(self) -> None:
        clear_layout(self.box_bottom_right)

        self.box_bottom_right.addSpacing(30)

        selected_ah_did = self._selected_ah_did()
        if selected_ah_did is None:

            self.box_no_deck_selected = QHBoxLayout()
            self.box_bottom_right.addLayout(self.box_no_deck_selected)

            self.box_no_deck_selected.addSpacing(5)

            self.no_deck_selected_label = QLabel("Choose deck to adjust settings.")
            self.no_deck_selected_label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            self.box_no_deck_selected.addWidget(self.no_deck_selected_label)

            self.box_bottom_right.addStretch(1)
            return

        # Deck Actions
        self.box_deck_actions = QVBoxLayout()
        self.box_bottom_right.addLayout(self.box_deck_actions)

        deck_name = config.deck_config(selected_ah_did).name
        self.deck_name_label = QLabel(f"<h3>{deck_name}</h3>")
        self.deck_name_label.setWordWrap(True)
        self.deck_name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.box_deck_actions.addWidget(self.deck_name_label)

        self.box_deck_action_buttons = QHBoxLayout()
        self.box_deck_actions.addLayout(self.box_deck_action_buttons)

        self.open_web_btn = QPushButton("Open on AnkiHub")
        self.box_deck_action_buttons.addWidget(self.open_web_btn)
        qconnect(self.open_web_btn.clicked, self._on_open_web)

        self.unsubscribe_btn = QPushButton("Unsubscribe")
        if theme_manager.night_mode:
            self.unsubscribe_btn.setStyleSheet("color: #e29792")
        else:
            self.unsubscribe_btn.setStyleSheet("color: #e2857f")

        self.box_deck_action_buttons.addWidget(self.unsubscribe_btn)
        qconnect(self.unsubscribe_btn.clicked, self._on_unsubscribe)

        self.box_bottom_right.addSpacing(20)

        # Deck Options
        self.box_deck_settings = QVBoxLayout()
        self.box_bottom_right.addLayout(self.box_deck_settings)

        self.deck_settings_label = QLabel("<b>Deck Options</b>")
        self.box_deck_settings.addWidget(self.deck_settings_label)

        self.box_deck_settings_elements = QVBoxLayout()
        self.box_deck_settings.addLayout(self.box_deck_settings_elements)

        # ... Suspend new cards of existing notes
        deck_config = config.deck_config(selected_ah_did)

        self.box_suspend_new_cards_of_existing_notes = QVBoxLayout()
        self.box_deck_settings_elements.addLayout(
            self.box_suspend_new_cards_of_existing_notes
        )

        self.suspend_new_cards_of_existing_notes_label = QLabel(
            "Suspend new cards of existing notes"
        )
        self.box_suspend_new_cards_of_existing_notes.addWidget(
            self.suspend_new_cards_of_existing_notes_label
        )

        self.suspend_new_cards_of_existing_notes = QComboBox()
        self.box_suspend_new_cards_of_existing_notes.addWidget(
            self.suspend_new_cards_of_existing_notes
        )

        self.suspend_new_cards_of_existing_notes.insertItems(
            0, [option.value for option in SuspendNewCardsOfExistingNotes]
        )
        self.suspend_new_cards_of_existing_notes.setCurrentText(
            deck_config.suspend_new_cards_of_existing_notes.name
        )
        qconnect(
            self.suspend_new_cards_of_existing_notes.currentTextChanged,
            lambda: config.set_suspend_new_cards_of_existing_notes(
                selected_ah_did,
                SuspendNewCardsOfExistingNotes(
                    self.suspend_new_cards_of_existing_notes.currentText()
                ),
            ),
        )

        self.box_deck_settings_elements.addSpacing(10)

        # ... Suspend new cards of new notes
        self.suspend_new_cards_of_new_notes_cb = QCheckBox(
            "Suspend new cards of new notes"
        )
        self.suspend_new_cards_of_new_notes_cb.setChecked(
            deck_config.suspend_new_cards_of_new_notes
        )
        qconnect(
            self.suspend_new_cards_of_new_notes_cb.toggled,
            lambda: config.set_suspend_new_cards_of_new_notes(
                selected_ah_did, self.suspend_new_cards_of_new_notes_cb.isChecked()
            ),
        )
        self.box_deck_settings_elements.addWidget(
            self.suspend_new_cards_of_new_notes_cb
        )

        self.box_deck_settings_elements.addSpacing(10)

        # ... Subdecks
        self.subdecks_cb_row = QHBoxLayout()
        self.box_deck_settings_elements.addLayout(self.subdecks_cb_row)

        subdecks_tooltip_message = (
            "Toggle between the deck being organized into subdecks or not.<br>"
            f"This option is only available if notes in the deck have <b>{SUBDECK_TAG}</b> tags."
        )
        self.subdecks_cb = QCheckBox("Enable Subdecks")
        self.subdecks_cb.setToolTip(subdecks_tooltip_message)
        self._refresh_subdecks_checkbox()
        qconnect(self.subdecks_cb.clicked, self._on_toggle_subdecks)
        self.subdecks_cb_row.addWidget(self.subdecks_cb)

        self.subdeck_cb_icon_label = QLabel()
        self.subdeck_cb_icon_label.setPixmap(tooltip_icon().pixmap(16, 16))
        self.subdeck_cb_icon_label.setToolTip(subdecks_tooltip_message)
        self.subdecks_cb_row.addWidget(self.subdeck_cb_icon_label)

        self.subdecks_docs_link_label = QLabel(
            """
            <a href="https://docs.ankihub.net/user_docs/advanced.html#subdecks-and-subdeck-tags">
                More about subdecks
            </a>
            """
        )
        self.subdecks_docs_link_label.setOpenExternalLinks(True)
        self.box_deck_settings_elements.addWidget(self.subdecks_docs_link_label)

        self.box_bottom_right.addSpacing(20)

        # Destination for new cards
        self.box_new_cards_destination = QVBoxLayout()
        self.box_bottom_right.addLayout(self.box_new_cards_destination)

        self.new_cards_destination = QLabel("<b>Destination for New Cards</b>")
        self.box_new_cards_destination.addWidget(self.new_cards_destination)

        self.new_cards_destination_details_label = QLabel()
        self.new_cards_destination_details_label.setWordWrap(True)
        self._refresh_new_cards_destination_details_label(selected_ah_did)
        self.box_new_cards_destination.addWidget(
            self.new_cards_destination_details_label
        )

        self.set_new_cards_destination_btn = QPushButton(
            "Change Destination for New Cards"
        )
        qconnect(
            self.set_new_cards_destination_btn.clicked,
            self._on_new_cards_destination_btn_clicked,
        )
        self.box_new_cards_destination.addWidget(self.set_new_cards_destination_btn)
        self.box_new_cards_destination.addSpacing(7)

        self.new_cards_destination_docs_link_label = QLabel(
            """
            <a href="https://community.ankihub.net/t/how-are-anki-decks-related-to-ankihub-decks/4811">
                More about destinations for new cards
            </a>
            """
        )
        self.new_cards_destination_docs_link_label.setOpenExternalLinks(True)
        self.box_new_cards_destination.addWidget(
            self.new_cards_destination_docs_link_label
        )

        self.box_bottom_right.addStretch()

    def _refresh_new_cards_destination_details_label(self, ah_did: uuid.UUID) -> None:
        deck_config = config.deck_config(ah_did)
        destination_anki_did = deck_config.anki_id
        if name := aqt.mw.col.decks.name_if_exists(destination_anki_did):
            self.new_cards_destination_details_label.setText(
                f"New cards are saved to: {name}."
            )
        else:
            # If the deck doesn't exist, it will be re-created on next sync with the name from the config.
            self.new_cards_destination_details_label.setText(
                f"New cards are saved to: {deck_config.name}."
            )

    def _refresh_decks_list(self) -> None:
        self.decks_list.clear()

        subscribed_decks = self.client.get_deck_subscriptions()
        installed_ah_dids = config.deck_ids()
        subscribed_and_installed_decks = [
            deck for deck in subscribed_decks if deck.ah_did in installed_ah_dids
        ]
        for deck in subscribed_and_installed_decks:
            name = deck.name
            if deck.is_user_relation_owner:
                item = QListWidgetItem(f"{name} (Created by you)")
            elif deck.is_user_relation_maintainer:
                item = QListWidgetItem(f"{name} (Maintained by you)")
            else:
                item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, deck.ah_did)
            self.decks_list.addItem(item)

    def _selected_ah_did(self) -> Optional[UUID]:
        selection = self.decks_list.selectedItems()
        if len(selection) != 1:
            return None

        result = selection[0].data(Qt.ItemDataRole.UserRole)
        return result

    def _refresh_anki(self) -> None:
        op = OpChanges()
        op.deck = True
        op.browser_table = True
        op.browser_sidebar = True
        op.study_queues = True
        gui_hooks.operation_did_execute(op, handler=None)

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
        ah_did = self._selected_ah_did()
        deck_name = config.deck_config(ah_did).name
        confirm = ask_user(
            f"Unsubscribe from deck {deck_name}?\n\n"
            "The deck will remain in your collection, but it will no longer sync with AnkiHub.",
            title="Unsubscribe AnkiHub Deck",
        )
        if not confirm:
            return

        unsubscribe_from_deck_and_uninstall(ah_did)

        tooltip("Unsubscribed from AnkiHub Deck.", parent=aqt.mw)
        self._refresh_decks_list()

    def _on_open_web(self) -> None:
        ah_did = self._selected_ah_did()
        if ah_did is None:
            return

        openLink(f"{url_deck_base()}/{ah_did}")

    def _on_new_cards_destination_btn_clicked(self):
        ah_did = self._selected_ah_did()
        current_destination_deck = aqt.mw.col.decks.get(
            config.deck_config(ah_did).anki_id
        )
        if current_destination_deck is None:
            current = None
        else:
            current = current_destination_deck["name"]

        def update_deck_config(ret: StudyDeck):
            if not ret.name:
                return

            anki_did = aqt.mw.col.decks.id(ret.name)
            config.set_home_deck(ankihub_did=ah_did, anki_did=anki_did)
            self._refresh_new_cards_destination_details_label(ah_did)

        # this lets the user pick a deck
        StudyDeckWithoutHelpButton(
            aqt.mw,
            current=current,
            accept="Accept",
            title="Select Destination for New Cards",
            parent=self,
            callback=update_deck_config,
            buttons=[],  # This removes the "Add" button
        )

    def _on_toggle_subdecks(self):
        ah_did = self._selected_ah_did()
        deck_config = config.deck_config(ah_did)
        if aqt.mw.col.decks.name_if_exists(deck_config.anki_id) is None:
            showInfo(
                (
                    f"Anki deck <b>{deck_config.name}</b> doesn't exist in your Anki collection.<br>"
                    "It might help to reset local changes to the deck first.<br>"
                    "(You can do that from the AnkiHub menu in the Anki browser.)"
                ),
            )
            return

        confirm_and_toggle_subdecks(ah_did)

        self._refresh_subdecks_checkbox()

    def _refresh_subdecks_checkbox(self):
        ah_did = self._selected_ah_did()

        has_subdeck_tags = deck_contains_subdeck_tags(ah_did)
        self.subdecks_cb.setEnabled(has_subdeck_tags)
        self.subdecks_cb.setStyleSheet("color: grey" if not has_subdeck_tags else "")

        deck_config = config.deck_config(ah_did)
        self.subdecks_cb.setChecked(deck_config.subdecks_enabled)

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
