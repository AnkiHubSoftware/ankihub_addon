from copy import deepcopy
from uuid import UUID

import aqt
from aqt.qt import (
    QBoxLayout,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    Qt,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.studydeck import StudyDeck
from aqt.utils import showInfo

from ..main.subdecks import SUBDECK_TAG
from ..settings import config
from .operations.subdecks import confirm_and_toggle_subdecks


class DeckOptionsDialog(QDialog):
    def __init__(self, ah_did: UUID):
        super(DeckOptionsDialog, self).__init__()

        self._ah_did = ah_did
        self._deck_config = deepcopy(config.deck_config(ah_did))

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle(f"Deck options for {self._deck_config.name}")
        self.setMinimumWidth(350)
        self.setMinimumHeight(400)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        self._outer_layout = QVBoxLayout()
        self._main_layout = QVBoxLayout()
        self._btn_layout = QHBoxLayout()
        self._outer_layout.addLayout(self._main_layout)
        self._outer_layout.addLayout(self._btn_layout)
        self.setLayout(self._outer_layout)

        self._tab_widget = QTabWidget()
        self._tab_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._main_layout.addWidget(self._tab_widget)

        self._tab = QWidget(self)
        self._tab_layout = QVBoxLayout()
        self._tab.setLayout(self._tab_layout)
        self._tab_widget.addTab(self._tab, "General")

        self._setup_buttons(self._btn_layout)

        self._current_home_deck_label = QLabel("")
        self._current_home_deck_label.setToolTip(
            "New cards will be added to this deck."
        )
        self._tab_layout.addWidget(self._current_home_deck_label)

        self.set_home_deck_btn = QPushButton("Set Home deck")
        qconnect(self.set_home_deck_btn.clicked, self._on_set_home_deck)
        self._refresh_home_deck_display()
        self._tab_layout.addWidget(self.set_home_deck_btn)

        self._tab_layout.addSpacing(15)

        self._subdecks_cb = QCheckBox("Subdecks")
        self._subdecks_cb.setToolTip(
            "Whether the deck should be organized into subdecks or not.<br>"
            f"This will only have an effect if notes in the deck have <b>{SUBDECK_TAG}</b> tags."
        )
        self._subdecks_cb.setChecked(self._deck_config.subdecks_enabled)

        def update_subdecks_enabled():
            self._deck_config.subdecks_enabled = self._subdecks_cb.isChecked()

        qconnect(self._subdecks_cb.stateChanged, update_subdecks_enabled)

        self._tab_layout.addWidget(self._subdecks_cb)

        self._tab_layout.addStretch()

    def _setup_buttons(self, btn_box: QBoxLayout) -> None:
        btn_box.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        qconnect(self.cancel_btn.clicked, self._on_cancel)
        btn_box.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.setDefault(True)
        self.save_btn.setShortcut("Ctrl+Return")
        qconnect(self.save_btn.clicked, self._on_save)
        btn_box.addWidget(self.save_btn)

    def _on_save(self) -> None:
        # Update subdecks enabled - or cancel save if user cancels toggling subdecks
        if aqt.mw.col.decks.name_if_exists(self._deck_config.anki_id) is None:
            showInfo(
                (
                    f"Anki deck <b>{self._deck_config.name}</b> doesn't exist in your Anki collection.<br>"
                    "It might help to reset local changes to the deck first.<br>"
                    "(You can do that from the AnkiHub menu in the Anki browser.)"
                ),
            )
        else:
            if (
                self._deck_config.subdecks_enabled
                != config.deck_config(self._ah_did).subdecks_enabled
            ):
                if not confirm_and_toggle_subdecks(self._ah_did):
                    # User cancelled
                    return

        # Update home deck
        config.set_home_deck(
            ankihub_did=self._ah_did, anki_did=self._deck_config.anki_id
        )

        self.close()

    def _on_cancel(self) -> None:
        self.close()

    def _refresh_home_deck_display(self) -> None:
        home_deck_name = aqt.mw.col.decks.name_if_exists(self._deck_config.anki_id)
        self._current_home_deck_label.setText(
            f"Home deck: <b>{home_deck_name if home_deck_name else 'None'}</b>"
        )

    def _on_set_home_deck(self) -> None:
        if current_home_deck := aqt.mw.col.decks.get(self._deck_config.anki_id):
            current_home_deck_name = current_home_deck["name"]
        else:
            current_home_deck_name = None

        def update_home_deck(study_deck: StudyDeck) -> None:
            if not study_deck.name:
                return

            anki_did = aqt.mw.col.decks.id(study_deck.name)
            self._deck_config.anki_id = anki_did
            self._refresh_home_deck_display()

        StudyDeckWithoutHelpButton(
            aqt.mw,
            current=current_home_deck_name,
            accept="Set Home Deck",
            title="Choose Home Deck",
            parent=self,
            callback=update_home_deck,
        )


class StudyDeckWithoutHelpButton(StudyDeck):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.form.buttonBox.removeButton(
            self.form.buttonBox.button(QDialogButtonBox.StandardButton.Help)
        )
