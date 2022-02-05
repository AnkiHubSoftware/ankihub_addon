from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy
from aqt import mw
from aqt.qt import QAction, QMenu, qconnect
from aqt.studydeck import StudyDeck

from .ankihub_client import AnkiHubClient
from .register_decks import create_shared_deck


def create_shared_deck_action() -> None:
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
    create_shared_deck(did)


def add_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    ankihub_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ankihub_menu)
    _create_shared_deck_action = QAction("Upload Deck", parent=ankihub_menu)
    qconnect(_create_shared_deck_action.triggered, create_shared_deck_action)
    ankihub_menu.addAction(_create_shared_deck_action)
    sign_in_button = QAction("Sign in", mw)
    sign_in_button.triggered.connect(AnkiHubLogin.display_login)
    ankihub_menu.addAction(sign_in_button)


class AnkiHubLogin(QWidget):
    def __init__(self):
        super(AnkiHubLogin, self).__init__()
        self.results = None
        self.thread = None
        self.init_gui()

    # create GUI skeleton
    def init_gui(self):

        self.box_top = QVBoxLayout()
        self.box_upper = QHBoxLayout()

        # LEFT SECTION
        self.box_left = QVBoxLayout()

        # username
        self.username_box = QHBoxLayout()
        self.username_box_label = QLabel("Username:")
        self.username_box_text = QLineEdit("", self)
        self.username_box_text.setMinimumWidth(300)

        self.username_box.addWidget(self.username_box_label)
        self.username_box.addWidget(self.username_box_text)

        # add layouts to left
        self.box_left.addLayout(self.username_box)

        # password
        self.password_box = QHBoxLayout()
        self.password_box_label = QLabel("Password:")
        self.password_box_text = QLineEdit("", self)
        self.password_box_text.setMinimumWidth(300)

        self.password_box.addWidget(self.password_box_label)
        self.password_box.addWidget(self.password_box_text)

        # add layouts to left
        self.box_left.addLayout(self.password_box)

        # RIGHT SECTION
        self.box_right = QVBoxLayout()

        # code (import set) button
        self.connect_box = QHBoxLayout()
        self.connect_box_button = QPushButton("Connect", self)
        self.connect_box.addStretch(1)
        self.connect_box.addWidget(self.connect_box_button)
        self.connect_box_button.clicked.connect(self.onClickConnnect)

        self.bottom_box_section = QHBoxLayout()

        # Upload your collection to ankihub
        self.upload_but = QPushButton("Upload", self)
        self.bottom_box_section.addWidget(self.upload_but)
        self.upload_but.clicked.connect(self.uploadBut)

        # Download your collection from ankihub
        self.upload_but = QPushButton("Download", self)
        self.bottom_box_section.addWidget(self.upload_but)
        self.upload_but.clicked.connect(self.uploadBut)

        # Signout from AnkiHub
        self.upload_but = QPushButton("Signout", self)
        self.bottom_box_section.addWidget(self.upload_but)
        self.upload_but.clicked.connect(self.uploadBut)

        self.box_left.addLayout(self.bottom_box_section)
        self.box_right.addLayout(self.connect_box)

        # add left and right layouts to upper
        self.box_upper.addLayout(self.box_left)
        self.box_upper.addSpacing(20)
        self.box_upper.addLayout(self.box_right)

        # results label
        self.label_results = QLabel(
            """
            \r\n<i>Use the same username and password
            from AnkiHub to be able to sync</i>
            """
        )

        # add all widgets to top layout
        self.box_top.addLayout(self.box_upper)
        self.box_top.addWidget(self.label_results)
        self.box_top.addStretch(1)
        self.setLayout(self.box_top)

        # go, baby go!
        self.setMinimumWidth(500)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.setWindowTitle("AnkiHub connection setup")
        self.show()

    def uploadBut(self):
        self.label_results.setText(
            """We've started uploading your collection.
            You will be notified on completion. \n"""
        )
        # uploadToMFC()

    def onClickConnnect(self):
        self.label_results.setText("Waiting...")
        # grab input
        username = self.username_box_text.text()
        password = self.password_box_text.text()
        if username == "" or password == "":
            self.label_results.setText(
                "Oops! You forgot to put in a username or password!"
            )

        ankihub_client = AnkiHubClient()
        token = ankihub_client.authenitcateUserGetToken(
            url="auth-token/", data={"username": username, "password": password}
        )

        if token:
            self.label_results.setText("We've connected securely!: " + token)
        else:
            self.label_results.setText("There seems to have been an error!")

    @classmethod
    def display_login(cls):
        global __window
        __window = cls()
        return __window
