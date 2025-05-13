from typing import Optional

from aqt import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
    qconnect,
)

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..gui.overview import show_flashcard_selector


class SmartSearchDecksSelectorDialog(QDialog):
    _window: Optional["SmartSearchDecksSelectorDialog"] = None

    def __init__(self, parent=None):
        if not isinstance(parent, (QWidget, type(None))):
            parent = None

        super().__init__(parent)
        self._parent = parent
        self._setup_data()
        self._setup_ui()

    def _setup_data(self):
        # Get subscribed decks
        self.client = AnkiHubClient()
        self.decks_list = self.client.get_deck_subscriptions()
        self.decks_dict = {deck.name: deck for deck in self.decks_list}

    def _setup_ui(self):
        # Set window properties
        self.setWindowTitle("Your Subscribed AnkiHub Decks")
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)

        # Main layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(28, 28, 28, 28)

        # Search bar
        self.search_layout = QHBoxLayout()
        self.search_layout.setContentsMargins(0, 0, 0, 16)
        self.search_label = QLabel("Search:")
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search decks...")
        self.search_layout.addWidget(self.search_label)
        self.search_layout.addWidget(self.search_bar)

        # List widget
        self.list_widget = QListWidget()
        for deck in self.decks_list:
            self.list_widget.addItem(deck.name)

        # Buttons
        self.button_layout = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.open_button = QPushButton("Open")
        self.open_button.setEnabled(False)
        self.button_layout.addWidget(self.cancel_button)
        self.button_layout.addWidget(self.open_button)

        # Add all layouts to main layout
        self.main_layout.addLayout(self.search_layout)
        self.main_layout.addWidget(self.list_widget)
        self.main_layout.addLayout(self.button_layout)

        qconnect(self.search_bar.textChanged, self._filter_decks)
        qconnect(self.list_widget.currentItemChanged, self._update_open_button_state)
        qconnect(self.cancel_button.clicked, self.reject)
        qconnect(self.open_button.clicked, self._on_open_clicked)

    def _filter_decks(self):
        search_text = self.search_bar.text().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(search_text not in item.text().lower())

    def _update_open_button_state(self):
        self.open_button.setEnabled(self.list_widget.currentItem() is not None)

    def _on_open_clicked(self):
        current_item = self.list_widget.currentItem()
        if current_item:
            deck = self.decks_dict.get(current_item.text())
            if deck:
                show_flashcard_selector(ah_did=deck.ah_did)
                self.close()

    @classmethod
    def show_dialog(cls):
        LOGGER.info("SmartSearchDecksSelectorDialog opened")

        if cls._window is None:
            cls._window = cls()
        else:
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()
        return cls._window
