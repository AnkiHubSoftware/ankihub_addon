from concurrent.futures import Future
from typing import List, Optional

from anki.collection import OpChanges
from aqt import gui_hooks, mw
from aqt.emptycards import show_empty_cards
from aqt.operations.tag import clear_unused_tags
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
)
from aqt.utils import askUser, openLink, showInfo, showText, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..addon_ankihub_client import AnkiHubRequestError
from ..ankihub_client import NoteUpdate
from ..db import AnkiHubDB
from ..settings import URL_DECK_BASE, URL_DECKS, URL_HELP, URL_VIEW_DECK, config
from ..sync import AnkiHubImporter
from ..utils import create_backup, undo_note_type_modfications


class SubscribedDecksDialog(QDialog):
    _window: Optional["SubscribedDecksDialog"] = None
    silentlyClose = True

    def __init__(self):
        super(SubscribedDecksDialog, self).__init__()
        self.client = AnkiHubClient()
        self.setWindowTitle("Subscribed AnkiHub Decks")

        self.setup_ui()
        self.on_item_selection_changed()
        self.refresh_decks_list()

        if not self.client.has_token():
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def setup_ui(self):
        self.box_top = QVBoxLayout()
        self.box_above = QHBoxLayout()
        self.box_right = QVBoxLayout()

        self.decks_list = QListWidget()
        self.decks_list.itemSelectionChanged.connect(self.on_item_selection_changed)

        self.add_btn = QPushButton("Add")
        self.unsubscribe_btn = QPushButton("Unsubscribe")
        self.open_web_btn = QPushButton("Open on AnkiHub")
        self.add_btn.clicked.connect(self.on_add)
        self.unsubscribe_btn.clicked.connect(self.on_unsubscribe)
        self.open_web_btn.clicked.connect(self.on_open_web)
        self.box_right.addWidget(self.add_btn)
        self.box_right.addWidget(self.unsubscribe_btn)
        self.box_right.addWidget(self.open_web_btn)
        self.box_right.addStretch(1)

        self.setLayout(self.box_top)
        self.box_top.addLayout(self.box_above)
        self.box_above.addWidget(self.decks_list)
        self.box_above.addLayout(self.box_right)

    def refresh_decks_list(self) -> None:
        self.decks_list.clear()
        decks = config.private_config.decks
        for ankihub_id in decks:
            name = decks[ankihub_id]["name"]
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, ankihub_id)
            self.decks_list.addItem(item)

    def refresh_anki(self) -> None:
        op = OpChanges()
        op.deck = True
        op.browser_table = True
        op.browser_sidebar = True
        op.study_queues = True
        gui_hooks.operation_did_execute(op, handler=None)

    def on_add(self) -> None:
        SubscribeDialog().exec()
        self.refresh_decks_list()
        self.refresh_anki()

    def on_unsubscribe(self) -> None:
        items = self.decks_list.selectedItems()
        if len(items) == 0:
            return
        deck_names = [item.text() for item in items]
        deck_names_text = ", ".join(deck_names)
        confirm = askUser(
            f"Unsubscribe from deck {deck_names_text}?\n\n"
            "The deck will remain in your collection, but it will no longer sync with AnkiHub.",
            title="Unsubscribe AnkiHub Deck",
        )
        if not confirm:
            return

        for item in items:
            ankihub_did = item.data(Qt.ItemDataRole.UserRole)
            config.unsubscribe_deck(ankihub_did)
            self.unsubscribe_from_deck(ankihub_did)

        tooltip("Unsubscribed from AnkiHub Deck.", parent=mw)
        self.refresh_decks_list()

    @staticmethod
    def unsubscribe_from_deck(ankihub_did: str) -> None:

        db = AnkiHubDB()
        mids = db.note_types_for_ankihub_deck(ankihub_did=ankihub_did)
        undo_note_type_modfications(mids)
        db.remove_deck(ankihub_did)

    def on_open_web(self) -> None:
        items = self.decks_list.selectedItems()
        if len(items) == 0:
            return
        for item in items:
            ankihub_id = item.data(Qt.ItemDataRole.UserRole)
            openLink(f"{URL_DECK_BASE}/{ankihub_id}")

    def on_item_selection_changed(self) -> None:
        selection = self.decks_list.selectedItems()
        isSelected: bool = len(selection) > 0
        self.unsubscribe_btn.setEnabled(isSelected)
        self.open_web_btn.setEnabled(isSelected)

    @classmethod
    def display_subscribe_window(cls):
        if cls._window is None:
            cls._window = cls()
        else:
            cls._window.refresh_decks_list()
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()
        return cls._window


class SubscribeDialog(QDialog):
    silentlyClose = True

    def __init__(self):
        super(SubscribeDialog, self).__init__()
        self.results = None
        self.thread = None
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
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttonbox.button(QDialogButtonBox.StandardButton.Ok).setText("Subscribe")
        self.browse_btn = self.buttonbox.addButton(
            "Browse Decks", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.browse_btn.clicked.connect(self.on_browse_deck)
        self.buttonbox.accepted.connect(self.subscribe)
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
        if not self.client.has_token():
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def subscribe(self):
        ankihub_did = self.deck_id_box_text.text().strip()
        if ankihub_did in config.private_config.decks.keys():
            showText(
                f"You've already subscribed to deck {ankihub_did}. "
                "Syncing with AnkiHub will happen automatically everytime you "
                "restart Anki. You can manually sync with AnkiHub from the AnkiHub "
                f"menu. See {URL_HELP} for more details."
            )
            self.close()
            return

        confirmed = askUser(
            f"Would you like to proceed with downloading and installing the deck? "
            f"Your personal collection will be modified.<br><br>"
            f"See <a href='{URL_HELP}'>{URL_HELP}</a> for details.",
            title="Please confirm to proceed.",
        )
        if not confirmed:
            return

        self.download_and_install_deck(ankihub_did)

    def download_and_install_deck(self, ankihub_did: str):
        """
        Take the AnkiHub deck id, copied/pasted by the user and
        1) Download the deck .csv

        :param deck_id: the deck's ankihub id
        :return:
        """

        def on_install_done(future: Future):
            success = False
            exc = None
            try:
                success = future.result()
            except Exception as e:
                LOGGER.debug("Error installing deck.")
                exc = e

            if success:
                self.accept()
                mw.reset()

                if askUser(
                    "The deck has successfully been installed!<br><br>"
                    "Do you want to clear unused tags and empty cards from your collection? (recommended if you had "
                    " a previous version of the deck in your collection)",
                    title="AnkiHub",
                    parent=self,
                ):
                    clear_unused_tags(parent=self).run_in_background()
                    show_empty_cards(mw)

            else:
                LOGGER.warning("Importing deck failed.")
                self.reject()

            if exc:
                raise exc

        try:
            deck_info = self.client.get_deck_by_id(ankihub_did)
        except AnkiHubRequestError as e:
            if e.response.status_code == 404:
                showText(
                    f"Deck {ankihub_did} doesn't exist. Please make sure you copy/paste "
                    f"the correct ID. If you believe this is an error, please reach "
                    f"out to user support at help@ankipalace.com."
                )
                return
            elif e.response.status_code == 403:
                url_view_deck = f"{URL_VIEW_DECK}{ankihub_did}"
                showInfo(
                    f"Please first subscribe to the deck on the AnkiHub website.<br><br>"
                    f'Link to the deck: <a href="{url_view_deck}">{url_view_deck}</a>',
                )
                return
            else:
                raise e

        def on_download_done(future: Future) -> None:
            notes_data: List[NoteUpdate] = future.result()

            mw.taskman.with_progress(
                lambda: self.install_deck(
                    notes_data=notes_data,
                    deck_name=deck_info.name,
                    ankihub_did=ankihub_did,
                    last_update=deck_info.csv_last_upload,
                    is_creator=deck_info.owner,
                ),
                on_done=on_install_done,
                parent=mw,
                label="Installing deck",
            )

        mw.taskman.with_progress(
            lambda: self.client.download_deck(deck_info.ankihub_deck_uuid),
            on_done=on_download_done,
            parent=mw,
            label="Downloading deck",
        )

    def install_deck(
        self,
        notes_data: List[NoteUpdate],
        deck_name: str,
        ankihub_did: str,
        last_update: str,
        is_creator: bool,
    ) -> bool:
        """If we have a .csv, read data from the file and modify the user's note types
        and notes.
        :param: path to the .csv or .apkg file
        :return: True if successful, False if not
        """

        create_backup()

        importer = AnkiHubImporter()
        local_did = importer.import_ankihub_deck(
            ankihub_did=ankihub_did,
            notes_data=notes_data,
            deck_name=deck_name,
        )

        config.save_subscription(
            name=deck_name,
            ankihub_did=ankihub_did,
            anki_did=local_did,
            last_update=last_update,
            creator=is_creator,
        )

        LOGGER.debug("Importing deck was succesful.")

        return True

    def on_browse_deck(self) -> None:
        openLink(URL_DECKS)
