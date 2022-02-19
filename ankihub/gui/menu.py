from aqt import mw
from aqt.qt import QAction, QMenu, qconnect
from aqt.studydeck import StudyDeck
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ankihub.ankihub_client import AnkiHubClient
from ankihub.register_decks import create_shared_deck


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
            \r\n<i>Use your AnkiHub username and password to log in.</i>
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
        self.label_results.setText("Waiting...")
        # grab input
        username = self.username_box_text.text()
        password = self.password_box_text.text()
        if not all([username, password]):
            self.label_results.setText(
                "Oops! You forgot to put in a username or password!"
            )

        ankihub_client = AnkiHubClient()
        token = ankihub_client.authenticate_user(
            url="auth-token/", data={"username": username, "password": password}
        )
        if token:
            self.label_results.setText("You are now logged into AnkiHub.")
        else:
            self.label_results.setText(
                "AnkiHub login failed.  Please make sure your username and "
                "password are correct for AnkiHub."
            )
        # TODO write the token to disk to persist credentials.

    @classmethod
    def display_login(cls):
        global __window
        __window = cls()
        return __window


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


def create_shared_deck_setup(parent):
    q_action = QAction("Create shared deck", parent=parent)
    qconnect(q_action.triggered, create_shared_deck_action)
    parent.addAction(q_action)


def setup_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    ankihub_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ankihub_menu)
    create_shared_deck_setup(parent=ankihub_menu)
    sign_in_button = QAction("Sign in", mw)
    sign_in_button.triggered.connect(AnkiHubLogin.display_login)
    ankihub_menu.addAction(sign_in_button)
