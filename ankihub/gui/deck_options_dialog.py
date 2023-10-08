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
from .utils import info_icon_label


class DeckOptionsDialog(QDialog):
    def __init__(self, ah_did: UUID):
        super(DeckOptionsDialog, self).__init__()

        self._ah_did = ah_did
        self._deck_config = config.deck_config(ah_did)

        self.setWindowTitle(f"Deck options for {self._deck_config.name}")
        self._setup_ui()

    def _setup_ui(self) -> None:
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

        self.setMinimumWidth(350)
        self.setMinimumHeight(400)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)

        self._home_deck_row = QHBoxLayout()

        home_deck_tooltip_text = "New cards will be added to this deck."
        self._home_deck_info_icon_label = info_icon_label(home_deck_tooltip_text)
        self._home_deck_row.addWidget(self._home_deck_info_icon_label)

        self.current_home_deck_label = QLabel("")
        self.current_home_deck_label.setToolTip(home_deck_tooltip_text)
        self._home_deck_row.addWidget(self.current_home_deck_label)

        self._home_deck_row.addStretch()

        self._tab_layout.addLayout(self._home_deck_row)

        self.set_home_deck_btn = QPushButton("Change Home deck")
        qconnect(self.set_home_deck_btn.clicked, self._on_set_home_deck)
        self._refresh_home_deck_display()
        self._tab_layout.addWidget(self.set_home_deck_btn)

        self._tab_layout.addSpacing(15)

        self.subdecks_cb = QCheckBox("Subdecks")
        self.subdecks_cb.setToolTip(
            "Whether the deck should be organized into subdecks or not.<br>"
            f"This will only have an effect if notes in the deck have <b>{SUBDECK_TAG}</b> tags."
        )
        self._tab_layout.addWidget(self.subdecks_cb)

        self._tab_layout.addStretch()

    def _setup_buttons(self, btn_box: QBoxLayout) -> None:
        btn_box.addStretch(1)

        self.cancel_btn = QPushButton("Cancel")
        # self.cancel_btn.clicked.connect(self.on_cancel)  # type: ignore
        btn_box.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.setDefault(True)
        self.save_btn.setShortcut("Ctrl+Return")
        # self.save_btn.clicked.connect(self.on_save)  # type: ignore
        btn_box.addWidget(self.save_btn)

    def _refresh_home_deck_display(self) -> None:
        home_deck_name = aqt.mw.col.decks.name_if_exists(self._deck_config.anki_id)
        self.current_home_deck_label.setText(
            f"Home deck: {home_deck_name if home_deck_name else 'None'}"
        )

    def _on_set_home_deck(self) -> None:
        def update_deck_config(ret: StudyDeck):
            if not ret.name:
                return

            anki_did = aqt.mw.col.decks.id(ret.name)
            config.set_home_deck(ankihub_did=self._ah_did, anki_did=anki_did)
            self._refresh_home_deck_display()

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

    def _on_toggle_subdecks(self) -> None:
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


class StudyDeckWithoutHelpButton(StudyDeck):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.form.buttonBox.removeButton(
            self.form.buttonBox.button(QDialogButtonBox.StandardButton.Help)
        )
