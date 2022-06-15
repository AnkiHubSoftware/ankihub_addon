import csv
import tempfile
from concurrent.futures import Future
from pathlib import Path

import requests
from aqt import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    mw,
)
from aqt.operations import QueryOp
from aqt.qt import QAction, QMenu, qconnect
from aqt.studydeck import StudyDeck
from aqt.utils import askUser, showInfo, showText, tooltip
from requests import Response
from requests.exceptions import HTTPError

from .. import LOGGER
from ..ankihub_client import AnkiHubClient
from ..config import Config
from ..constants import CSV_DELIMITER, URL_HELP
from ..register_decks import create_collaborative_deck, modify_note_types, process_csv
from ..utils import sync_with_ankihub


def main_menu_setup():
    ankihub_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ankihub_menu)
    return ankihub_menu


class AnkiHubLogin(QWidget):
    def __init__(self):
        super(AnkiHubLogin, self).__init__()
        self.results = None
        self.thread = None
        self.box_top = QVBoxLayout()
        self.box_upper = QHBoxLayout()
        self.box_left = QVBoxLayout()
        self.box_right = QVBoxLayout()
        self.bottom_box_section = QHBoxLayout()

        # Username
        self.username_box = QHBoxLayout()
        self.username_box_label = QLabel("Username:")
        self.username_box_text = QLineEdit("", self)
        self.username_box_text.setMinimumWidth(300)
        self.username_box.addWidget(self.username_box_label)
        self.username_box.addWidget(self.username_box_text)
        self.box_left.addLayout(self.username_box)

        # Password
        self.password_box = QHBoxLayout()
        self.password_box_label = QLabel("Password:")
        self.password_box_text = QLineEdit("", self)
        self.password_box_text.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_box_text.setMinimumWidth(300)
        self.password_box.addWidget(self.password_box_label)
        self.password_box.addWidget(self.password_box_text)
        self.box_left.addLayout(self.password_box)

        # Login
        self.login_button = QPushButton("Login", self)
        self.bottom_box_section.addWidget(self.login_button)
        self.login_button.clicked.connect(self.login)

        self.box_left.addLayout(self.bottom_box_section)

        # Add left and right layouts to upper
        self.box_upper.addLayout(self.box_left)
        self.box_upper.addSpacing(20)
        self.box_upper.addLayout(self.box_right)

        self.label_results = QLabel(
            """
            \r\n<center><i>Use your AnkiHub username and password to log in.</i></center>
            """
        )

        # Add all widgets to top layout.
        self.box_top.addLayout(self.box_upper)
        self.box_top.addWidget(self.label_results)
        self.box_top.addStretch(1)
        self.setLayout(self.box_top)

        self.setMinimumWidth(500)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWindowTitle("Login to AnkiHub.")
        config = Config()
        if config.private_config.token:
            self.label_results.setText("✨ You are logged into AnkiHub.")
        self.show()

    def login(self):
        username = self.username_box_text.text()
        password = self.password_box_text.text()
        if not all([username, password]):
            showText("Oops! You forgot to put in a username or password!")
            return
        ankihub_client = AnkiHubClient()
        try:
            ankihub_client.login(
                credentials={"username": username, "password": password}
            )
        except HTTPError as e:
            LOGGER.debug(f"{e}")
            showText(
                "AnkiHub login failed.  Please make sure your username and "
                "password are correct for AnkiHub."
            )
            return
        self.label_results.setText("✨ You are now logged into AnkiHub.")

    @classmethod
    def display_login(cls):
        global __window
        __window = cls()
        return __window


class SubscribeToDeck(QWidget):
    def __init__(self):
        super(SubscribeToDeck, self).__init__()
        self.results = None
        self.thread = None
        self.box_top = QVBoxLayout()
        self.box_upper = QHBoxLayout()
        self.box_left = QVBoxLayout()
        self.box_right = QVBoxLayout()
        self.bottom_box_section = QHBoxLayout()

        self.deck_id_box = QHBoxLayout()
        self.deck_id_box_label = QLabel("AnkiHub Deck ID:")
        self.deck_id_box_text = QLineEdit("", self)
        self.deck_id_box_text.setMinimumWidth(300)
        self.deck_id_box.addWidget(self.deck_id_box_label)
        self.deck_id_box.addWidget(self.deck_id_box_text)
        self.box_left.addLayout(self.deck_id_box)

        self.subscribe_button = QPushButton("Subscribe to AnkiHub Deck", self)
        self.bottom_box_section.addWidget(self.subscribe_button)
        self.subscribe_button.clicked.connect(self.subscribe)
        self.box_left.addLayout(self.bottom_box_section)

        self.box_upper.addLayout(self.box_left)
        self.box_upper.addSpacing(20)
        self.box_upper.addLayout(self.box_right)

        self.instructions_label = """
        \r\n<center>Copy/Paste a Deck ID from AnkiHub.net/decks to subscribe.</center>
        """
        self.label_results = QLabel(self.instructions_label)
        # Add all widgets to top layout.
        self.box_top.addLayout(self.box_upper)
        self.box_top.addWidget(self.label_results)
        self.box_top.addStretch(1)
        self.setLayout(self.box_top)

        self.setMinimumWidth(500)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWindowTitle("Subscribe to Collaborative Deck")

        self.config = Config()
        self.client = AnkiHubClient()
        if not self.client.token:
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def subscribe(self):
        ankihub_did = self.deck_id_box_text.text()
        try:
            ankihub_did = int(ankihub_did)
        except ValueError:
            showText(
                "Oops! Please copy/paste a Deck ID from AnkiHub.net/browse (numbers only)!"
            )
            return

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

    def download_and_install_deck(self, ankihub_did):
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
            self.label_results.setText(self.instructions_label)
            return
        elif deck_response.status_code != 200:
            return

        data = deck_response.json()
        first_time_install = mw.col.decks.get(data["anki_id"]) is not None
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
            self.label_results.setText("Deck download successful!")

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

            self.close()

        mw.taskman.with_progress(
            lambda: requests.get(s3_url),
            on_done=on_download_done,
            parent=self,
            label="Downloading deck",
        )

    def install_deck(self, deck_file: Path, ankihub_did: int, anki_did: int):
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

    def _install_deck_csv(self, deck_file):
        tooltip("Configuring the collaborative deck.")
        ankihub_deck_ids, note_type_names = set(), set()
        notes = []
        with deck_file.open() as f:
            reader = csv.DictReader(f, delimiter=CSV_DELIMITER)
            for row in reader:
                notes.append(row)
                ankihub_deck_ids.add(row["deck"])
                note_type_names.add(row["note_type"])
        assert len(ankihub_deck_ids) == 1
        mw._create_backup_with_progress(user_initiated=False)
        modify_note_types(note_type_names)
        process_csv(notes)

    @classmethod
    def display_subscribe_window(cls):
        global __window
        __window = cls()
        return __window


def ankihub_login_setup(parent):
    sign_in_button = QAction("🔑 Sign into AnkiHub", mw)
    sign_in_button.triggered.connect(AnkiHubLogin.display_login)
    parent.addAction(sign_in_button)


def create_collaborative_deck_action() -> None:
    deck_chooser = StudyDeck(
        mw,
        title="AnkiHub",
        accept="Upload",
        # Removes the "Add" button
        buttons=[],
        names=lambda: [
            d.name
            for d in mw.col.decks.all_names_and_ids(
                include_filtered=True, skip_empty_default=True
            )
            if "::" not in d.name
        ],
    )
    deck_name = deck_chooser.name
    if not deck_name:
        return
    confirm = askUser(
        "Uploading the deck to AnkiHub requires modifying notes and note types in "
        f"{deck_name} and will require a full sync afterwards.  Would you like to "
        "continue?",
    )
    if not confirm:
        tooltip("Cancelled Upload to AnkiHub")
        return

    def on_success(response: Response) -> None:
        # TODO save subscription to config
        # currently the reponse does not contain the ankihub_did...
        # config = Config()
        # data = response.json()
        # config.save_subscription((data["ankihub_id"], anki_id), creator=True)

        if response.status_code == 201:
            msg = "🎉 Deck upload successful!"
        else:
            msg = f"😔 Deck upload failed: {response.text}"
        showInfo(msg)

    op = QueryOp(
        parent=mw,
        op=lambda col: create_collaborative_deck(deck_name),
        success=on_success,
    )
    LOGGER.debug("Instantiated QueryOp")
    op.with_progress().run_in_background()


def create_collaborative_deck_setup(parent):
    q_action = QAction("🛠️ Create Collaborative Deck", parent=parent)
    qconnect(q_action.triggered, create_collaborative_deck_action)
    parent.addAction(q_action)


def upload_suggestions_action():
    """Action for uploading suggestions in bulk."""
    # TODO Instantiate AnkiHubClient.
    # TODO Query the the note table for mod times that are later than the time
    #  the last sync.
    # TODO Send a request to AnkiHub with the list of modified notes.


def sync_with_ankihub_action():
    sync_with_ankihub()


def upload_suggestions_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("⬆️ Upload suggestions to AnkiHub", parent=parent)
    qconnect(q_action.triggered, upload_suggestions_action)
    parent.addAction(q_action)


def subscribe_to_deck_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("📚 Subscribe to a Deck", mw)
    q_action.triggered.connect(SubscribeToDeck.display_subscribe_window)
    parent.addAction(q_action)


def sync_with_ankihub_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("🔃️ Sync with AnkiHub", mw)
    q_action.triggered.connect(sync_with_ankihub_action)
    parent.addAction(q_action)


def setup_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    ankihub_menu = main_menu_setup()
    ankihub_login_setup(parent=ankihub_menu)
    create_collaborative_deck_setup(parent=ankihub_menu)
    subscribe_to_deck_setup(parent=ankihub_menu)
    sync_with_ankihub_setup(parent=ankihub_menu)
    # upload_suggestions_setup(parent=ankihub_menu)
