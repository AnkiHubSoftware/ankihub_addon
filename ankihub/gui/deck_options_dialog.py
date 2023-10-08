from uuid import UUID

import aqt
from aqt.qt import QDialog, QDialogButtonBox, QPushButton, Qt, QVBoxLayout, qconnect
from aqt.studydeck import StudyDeck
from aqt.utils import showInfo, tooltip

from ..main.subdecks import SUBDECK_TAG
from ..settings import config
from .operations.subdecks import confirm_and_toggle_subdecks
from .utils import set_tooltip_icon


class DeckOptionsDialog(QDialog):
    def __init__(self, ah_did: UUID):
        super(DeckOptionsDialog, self).__init__()

        self._ah_did = ah_did

        self.setWindowTitle("Subscribed AnkiHub Decks")
        self._setup_ui()

    def run(self) -> None:
        self.exec()

    def _setup_ui(self):
        self.box = QVBoxLayout()

        self.set_home_deck_btn = QPushButton("Set Home deck")
        self.set_home_deck_btn.setToolTip("New cards will be added to this deck.")
        set_tooltip_icon(self.set_home_deck_btn)
        qconnect(self.set_home_deck_btn.clicked, self._on_set_home_deck)
        self.box.addWidget(self.set_home_deck_btn)

        self.toggle_subdecks_btn = QPushButton("Enable Subdecks")
        self.toggle_subdecks_btn.setToolTip(
            "Toggle between the deck being organized into subdecks or not.<br>"
            f"This will only have an effect if notes in the deck have <b>{SUBDECK_TAG}</b> tags."
        )
        set_tooltip_icon(self.toggle_subdecks_btn)
        qconnect(self.toggle_subdecks_btn.clicked, self._on_toggle_subdecks)
        self.box.addWidget(self.toggle_subdecks_btn)

        self.setLayout(self.box)

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


class StudyDeckWithoutHelpButton(StudyDeck):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.form.buttonBox.removeButton(
            self.form.buttonBox.button(QDialogButtonBox.StandardButton.Help)
        )
