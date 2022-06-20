import csv
import tempfile
import requests
from concurrent.futures import Future
from pathlib import Path
from aqt import mw
from aqt.utils import askUser, showText, tooltip, openLink
from aqt.qt import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QDialog,
    QDialogButtonBox,
)

from .. import LOGGER
from ..ankihub_client import AnkiHubClient
from ..config import Config
from ..constants import CSV_DELIMITER, URL_HELP, URL_DECKS
from ..register_decks import modify_note_types, process_csv


class SubscribeToDeck(QDialog):
    def __init__(self):
        super(SubscribeToDeck, self).__init__()
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

        self.config = Config()
        self.client = AnkiHubClient()
        if not self.client.token:
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def subscribe(self):
        ankihub_did = self.deck_id_box_text.text()
        if ankihub_did in self.config.private_config.decks.keys():
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

        data = deck_response.json()
        local_deck_ids = {deck.id for deck in mw.col.decks.all_names_and_ids()}
        first_time_install = data["anki_id"] not in local_deck_ids
        deck_file_name = (
            data["apkg_filename"] if first_time_install else data["csv_notes_filename"]
        )

        presigned_url_response = self.client.get_presigned_url(
            key=deck_file_name, action="download"
        )
        s3_url = presigned_url_response.json()["pre_signed_url"]

        def on_download_done(future: Future):
            s3_response = future.result()
            LOGGER.debug(f"{s3_response.url}")
            LOGGER.debug(f"{s3_response.status_code}")
            # TODO Use io.BytesIO
            out_file = Path(tempfile.mkdtemp()) / f"{deck_file_name}"
            with out_file.open("wb") as f:
                f.write(s3_response.content)
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
                    self.install_deck(out_file, ankihub_did, data["anki_id"])

            tooltip("Successfully subscribed to deck!")
            self.accept()

        mw.taskman.with_progress(
            lambda: requests.get(s3_url),
            on_done=on_download_done,
            parent=self,
            label="Downloading deck",
        )

    def install_deck(self, deck_file: Path, ankihub_did: str, anki_did: int):
        """If we have a .csv, read data from the file and modify the user's note types
        and notes.
        :param: path to the .csv or .apkg file
        """
        if deck_file.suffix == ".apkg":
            self._install_deck_apkg(deck_file)
        elif deck_file.suffix == ".csv":
            self._install_deck_csv(deck_file)

        self.config.save_subscription(ankihub_did, anki_did)
        tooltip("The deck has successfully been installed!")

    def _install_deck_apkg(self, deck_file: Path):
        from aqt import importing

        importing.importFile(mw, str(deck_file.absolute()))

    def _install_deck_csv(self, deck_file: Path):
        tooltip("Configuring the collaborative deck.")
        ankihub_deck_ids, note_type_names = set(), set()
        notes = []
        with deck_file.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=CSV_DELIMITER, quotechar="'")
            for row in reader:
                notes.append(row)
                ankihub_deck_ids.add(row["deck"])
                note_type_names.add(row["note_type"])
        assert len(ankihub_deck_ids) == 1
        mw._create_backup_with_progress(user_initiated=False)
        modify_note_types(note_type_names)
        process_csv(notes)

    def on_browse_deck(self) -> None:
        openLink(URL_DECKS)

    @classmethod
    def display_subscribe_window(cls):
        global __window
        __window = cls()
        return __window
