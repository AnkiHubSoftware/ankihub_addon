from uuid import UUID

import aqt
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    qconnect,
)
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
        self._deck_config = config.deck_config(ah_did)

        self.setWindowTitle(f"Deck options for {self._deck_config.name}")
        self._setup_ui()

    def _setup_ui(self):
        self.box = QVBoxLayout()

        self.setMinimumWidth(300)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

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
        def update_deck_config(ret: StudyDeck):
            if not ret.name:
                return

            anki_did = aqt.mw.col.decks.id(ret.name)
            config.set_home_deck(ankihub_did=self._ah_did, anki_did=anki_did)
            tooltip("Home deck updated.", parent=self)

        if current_home_deck := aqt.mw.col.decks.get(self._deck_config.anki_id):
            current_home_deck_name = current_home_deck["name"]
        else:
            current_home_deck_name = None

        StudyDeckWithoutHelpButton(
            aqt.mw,
            current=current_home_deck_name,
            accept="Set Home Deck",
            title="Change Home Deck",
            parent=self,
            callback=update_deck_config,
        )

    def _on_toggle_subdecks(self):
        if aqt.mw.col.decks.name_if_exists(self._deck_config.anki_id) is None:
            showInfo(
                (
                    f"Anki deck <b>{self._deck_config.name}</b> doesn't exist in your Anki collection.<br>"
                    "It might help to reset local changes to the deck first.<br>"
                    "(You can do that from the AnkiHub menu in the Anki browser.)"
                ),
            )
            return

        confirm_and_toggle_subdecks(self._ah_did)

        self._refresh_subdecks_button()

    def _refresh_subdecks_button(self):
        self.toggle_subdecks_btn.setText(
            "Disable Subdecks"
            if self._deck_config.subdecks_enabled
            else "Enable Subdecks"
        )


class StudyDeckWithoutHelpButton(StudyDeck):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.form.buttonBox.removeButton(
            self.form.buttonBox.button(QDialogButtonBox.StandardButton.Help)
        )
