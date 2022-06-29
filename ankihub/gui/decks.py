import csv
import tempfile
from concurrent.futures import Future
from pathlib import Path

from aqt import QPushButton, mw
from aqt.importing import AnkiPackageImporter
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    Qt,
    QVBoxLayout,
)
from aqt.utils import askUser, openLink, showText, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..config import config
from ..constants import CSV_DELIMITER, URL_DECK_BASE, URL_DECKS, URL_HELP
from ..register_decks import modify_note_types, process_csv
from ..utils import create_backup_with_progress


class SubscribedDecksDialog(QDialog):
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
            anki_id = decks[ankihub_id]["anki_id"]
            deck = mw.col.decks.get(anki_id, default=False)
            name = deck["name"] if deck is not None else ankihub_id
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, ankihub_id)
            self.decks_list.addItem(item)

    def on_add(self) -> None:
        SubscribeDialog().exec()
        self.refresh_decks_list()

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
            ankihub_id = item.data(Qt.ItemDataRole.UserRole)
            config.unsubscribe_deck(ankihub_id)
            # TODO Run clean up when implemented:
            #  https://github.com/ankipalace/ankihub_addon/issues/20

        tooltip("Unsubscribed from AnkiHub Deck.")
        self.refresh_decks_list()

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
        global __window
        __window = cls()
        return __window


class SubscribeDialog(QDialog):
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
        Take the AnkiHub deck id, copyied/pasted by the user and
        1) Download the deck .csv or .apkg, depending on if the user already has
        the deck.

        :param deck_id: the deck's ankihub id
        :return:
        """

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
        local_deck_ids = {deck.id for deck in mw.col.decks.all_names_and_ids()}
        first_time_install = data["anki_id"] not in local_deck_ids
        deck_file_name = (
            data["apkg_filename"] if first_time_install else data["csv_notes_filename"]
        )

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
                    f"The AnkiHub deck {ankihub_did} has been downloaded. Would you like to "
                    f"proceed with modifying your personal collection in order to subscribe "
                    f"to the collaborative deck? See {URL_HELP} for "
                    f"details.",
                    title="Please confirm to proceed.",
                )
                if confirmed:
                    mw.taskman.with_progress(
                        lambda: self.install_deck(
                            out_file, ankihub_did, data["anki_id"]
                        ),
                        label="Installing deck",
                    )

        mw.taskman.with_progress(
            lambda: self.client.download_deck(deck_file_name),
            on_done=on_download_done,
            parent=self,
            label="Downloading deck",
        )

    def install_deck(self, deck_file: Path, ankihub_did: str, anki_did: int):
        """If we have a .csv, read data from the file and modify the user's note types
        and notes.
        :param: path to the .csv or .apkg file
        """

        create_backup_with_progress()

        try:
            if deck_file.suffix == ".apkg":
                self._install_deck_apkg(deck_file)
            elif deck_file.suffix == ".csv":
                self._install_deck_csv(deck_file)
        except Exception as e:  # noqa

            def on_failure():
                showText(f"Failed to import deck.\n\n{str(e)}")  # noqa
                mw.progress.finish()  # this needs to be called before self.reject or reject will not work
                self.reject()

            LOGGER.exception("Importing deck failed.")
            mw.taskman.run_on_main(on_failure)
        else:

            def on_success():
                tooltip("The deck has successfully been installed!")
                mw.progress.finish()  # this needs to be called before self.accept or accept will not work
                self.accept()
                mw.reset()  # without this you have to click on "Decks" for the deck to appear in the main window

            LOGGER.debug("Importing deck was succesful.")
            config.save_subscription(ankihub_did, anki_did)
            mw.taskman.run_on_main(on_success)

    def _install_deck_apkg(
        self,
        deck_file: Path,
    ) -> None:
        LOGGER.debug("Importing deck as apkg....")
        file = str(deck_file.absolute())
        importer = AnkiPackageImporter(mw.col, file)
        importer.run()

    def _install_deck_csv(
        self,
        deck_file: Path,
    ) -> None:
        LOGGER.debug("Importing deck as csv....")
        ankihub_deck_ids, note_type_names = set(), set()
        notes = []
        with deck_file.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=CSV_DELIMITER, quotechar="'")
            for row in reader:
                notes.append(row)
                ankihub_deck_ids.add(row["deck"])
                note_type_names.add(row["note_type"])
        assert len(ankihub_deck_ids) == 1
        modify_note_types(note_type_names)
        process_csv(notes)

    def on_browse_deck(self) -> None:
        openLink(URL_DECKS)
