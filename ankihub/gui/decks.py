import csv
import tempfile
from concurrent.futures import Future
from pathlib import Path
from typing import Optional

from anki.collection import OpChanges
from anki.decks import DeckId
from aqt import gui_hooks, mw
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
from aqt.utils import askUser, openLink, showText, tooltip

from .. import LOGGER, report_exception
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..config import config
from ..constants import CSV_DELIMITER, URL_DECK_BASE, URL_DECKS, URL_HELP
from ..db import AnkiHubDB
from ..sync import import_ankihub_deck, sync_with_ankihub
from ..utils import create_backup_with_progress, undo_note_type_modfications


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
        ankihub_did = self.deck_id_box_text.text()
        if ankihub_did in config.private_config.decks.keys():
            showText(
                f"You've already subscribed to deck {ankihub_did}. "
                "Syncing with AnkiHub will happen automatically everytime you "
                "restart Anki. You can manually sync with AnkiHub from the AnkiHub "
                f"menu. See {URL_HELP} for more details."
            )
            self.close()
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
                report_exception()
                exc = e

            if success:
                tooltip("The deck has successfully been installed!", parent=mw)
                self.accept()
                mw.reset()
            else:
                LOGGER.exception("Importing deck failed.")
                msg = "Failed to import deck."
                if exc:
                    msg += f"\n\n{str(exc)}"
                showText(msg)
                self.reject()

        deck_response = self.client.get_deck_by_id(ankihub_did)
        if deck_response.status_code == 404:
            showText(
                f"Deck {ankihub_did} doesn't exist. Please make sure you copy/paste "
                f"the correct ID. If you believe this is an error, please reach "
                f"out to user support at help@ankipalace.com."
            )
            return

        if deck_response.status_code != 200:
            return

        data = deck_response.json()
        deck_file_name = data["csv_notes_filename"]
        last_update = data["csv_last_upload"]
        is_creator = bool(data["owner"])

        def on_download_done(future: Future):
            response = future.result()
            if response.status_code != 200:
                return

            out_file = Path(tempfile.mkdtemp()) / f"{deck_file_name}"
            with out_file.open("wb") as f:
                f.write(response.content)
                LOGGER.debug(f"Wrote {deck_file_name} to {out_file}")
                # TODO Validate .csv

            if out_file:
                confirmed = askUser(
                    f"The AnkiHub deck {data['name']} has been downloaded. Would you like to "
                    f"proceed with modifying your personal collection in order to subscribe "
                    f"to the collaborative deck? See {URL_HELP} for "
                    f"details.",
                    title="Please confirm to proceed.",
                )
                if not confirmed:
                    return

                mw.taskman.with_progress(
                    lambda: self.install_deck(
                        deck_file=out_file,
                        deck_name=data["name"],
                        ankihub_did=ankihub_did,
                        last_update=last_update,
                        is_creator=is_creator,
                    ),
                    on_done=on_install_done,
                    parent=mw,
                    label="Installing deck",
                )

        mw.taskman.with_progress(
            lambda: self.client.download_deck(deck_file_name),
            on_done=on_download_done,
            parent=mw,
            label="Downloading deck",
        )

    def install_deck(
        self,
        deck_file: Path,
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

        create_backup_with_progress()
        local_did = self._install_deck_csv(
            ankihub_did=ankihub_did, deck_file=deck_file, deck_name=deck_name
        )
        if local_did is None:
            return None

        config.save_subscription(
            name=deck_name,
            ankihub_did=ankihub_did,
            anki_did=local_did,
            last_update=last_update,
            creator=is_creator,
        )

        LOGGER.debug("Importing deck was succesful.")

        sync_with_ankihub()

        return True

    def _install_deck_csv(
        self,
        ankihub_did: str,
        deck_file: Path,
        deck_name: str,
    ) -> Optional[DeckId]:
        LOGGER.debug("Importing deck as csv....")
        with deck_file.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=CSV_DELIMITER, quotechar="'")
            notes_data = [row for row in reader]

        deck_id = import_ankihub_deck(
            ankihub_did=ankihub_did,
            notes_data=notes_data,
            deck_name=deck_name,
        )
        return deck_id

    def on_browse_deck(self) -> None:
        openLink(URL_DECKS)
