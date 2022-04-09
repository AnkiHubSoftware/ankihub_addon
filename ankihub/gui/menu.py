import tempfile
from pathlib import Path

import requests

from ankihub.ankihub_client import AnkiHubClient
from ankihub.register_decks import create_collaborative_deck
from aqt import mw
from aqt.qt import QAction, QMenu, qconnect
from aqt.studydeck import StudyDeck
from aqt.utils import showText, tooltip
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from requests.exceptions import HTTPError


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
        self.password_box_text.setEchoMode(QLineEdit.Password)
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
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.setWindowTitle("Login to AnkiHub.")
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
            showText(
                "AnkiHub login failed.  Please make sure your username and "
                "password are correct for AnkiHub."
            )
            return
        self.label_results.setText("You are now logged into AnkiHub.")

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

        self.subscribe_button = QPushButton("Subscribe to Deck", self)
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
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.setWindowTitle("Subscribe to Collaborative Deck")
        self.client = AnkiHubClient()
        if not self.client.token:
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()

    def subscribe(self):
        deck_id = self.deck_id_box_text.text()
        try:
            deck_id = int(deck_id)
        except ValueError:
            showText(
                "Oops! Please copy/paste a Deck ID from AnkiHub.net/browse (numbers only)!"
            )
            return
        # TODO use mw.taskman
        self.label_results.setText("Downloading deck...")
        response = client.get_deck_by_id(deck_id)
        if response.status_code == 404:
            showText(
                f"Deck {deck_id} doesn't exist. Please make sure you copy/paste "
                f"the correct ID."
            )
            self.label_results.setText(self.instructions_label)
            return
        elif response.status_code == 200:
            data = response.json()
            csv_url = data["csv_notes_url"]
            s3_response = requests.get(
                csv_url,
            )
            csv_file = Path(tempfile.mkdtemp()) / f"{deck_id}.csv"
            with csv_file.open("wb") as f:
                f.write(s3_response.content)
            self.label_results.setText("Success!")
            subscribe_response = client.subscribe(deck_id)
            if subscribe_response == 200:
                tooltip("Subscription confirmed!")
            self.close()

    @classmethod
    def display_subscribe_window(cls):
        global __window
        __window = cls()
        return __window


def ankihub_login_setup(parent):
    sign_in_button = QAction("Sign into AnkiHub", mw)
    sign_in_button.triggered.connect(AnkiHubLogin.display_login)
    parent.addAction(sign_in_button)


def create_collaborative_deck_action() -> None:
    diag = StudyDeck(
        mw,
        title="AnkiHub",
        accept="Upload",
        # Removes the "Add" button
        buttons=[],
    )
    deck_name = diag.name
    if not deck_name:
        return
    did = mw.col.decks.id(deck_name)
    create_collaborative_deck(did)


def create_collaborative_deck_setup(parent):
    q_action = QAction("Create collaborative deck", parent=parent)
    qconnect(q_action.triggered, create_collaborative_deck_action)
    parent.addAction(q_action)


def upload_suggestions_action():
    """Action for uploading suggestions in bulk."""
    # TODO Instantiate AnkiHubClient.
    # TODO Query the the note table for mod times that are later than the time
    #  the last sync.
    # TODO Send a request to AnkiHub with the list of modified notes.


def upload_suggestions_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("Upload suggestions to AnkiHub", parent=parent)
    qconnect(q_action.triggered, upload_suggestions_action)
    parent.addAction(q_action)


def subscribe_to_deck_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("Subscribe to collaborative deck", parent=parent)
    qconnect(q_action.triggered, SubscribeToDeck.display_subscribe_window)
    parent.addAction(q_action)


def setup_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    ankihub_menu = main_menu_setup()
    ankihub_login_setup(parent=ankihub_menu)
    create_collaborative_deck_setup(parent=ankihub_menu)
    subscribe_to_deck_setup(parent=ankihub_menu)
    upload_suggestions_setup(parent=ankihub_menu)
