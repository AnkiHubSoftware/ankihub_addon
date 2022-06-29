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
from aqt.utils import askUser, showInfo, showText, showWarning, tooltip
from requests import Response

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..config import config
from ..register_decks import create_collaborative_deck
from ..utils import sync_with_ankihub
from .decks import SubscribedDecksDialog


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
        self.password_box_text.returnPressed.connect(self.login)
        self.password_box.addWidget(self.password_box_label)
        self.password_box.addWidget(self.password_box_text)
        self.box_left.addLayout(self.password_box)

        # Login
        self.login_button = QPushButton("Login", self)
        self.bottom_box_section.addWidget(self.login_button)
        self.login_button.clicked.connect(self.login)
        self.login_button.setDefault(True)

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
        self.show()

    def login(self):
        username = self.username_box_text.text()
        password = self.password_box_text.text()
        if not all([username, password]):
            showText("Oops! You forgot to put in a username or password!")
            return
        ankihub_client = AnkiHubClient()
        ankihub_client.signout()

        response = ankihub_client.login(
            credentials={"username": username, "password": password}
        )
        if not response or response.status_code != 200:
            return

        self.close()

    @classmethod
    def display_login(cls):
        global __window
        __window = cls()
        return __window


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
        if response.status_code == 201:
            msg = "🎉 Deck upload successful!"

            data = response.json()
            anki_did = mw.col.decks.id_for_name(deck_name)
            ankihub_did = data["deck_id"]
            config.save_subscription(ankihub_did, anki_did, creator=True)
        else:
            msg = f"😔 Deck upload failed: {response.text}"
        showInfo(msg)

    def on_failure(exc: Exception):
        mw.progress.finish()
        raise exc

    op = QueryOp(
        parent=mw,
        op=lambda col: create_collaborative_deck(deck_name),
        success=on_success,
    ).failure(on_failure)
    LOGGER.debug("Instantiated QueryOp for creating collaborative deck")
    op.with_progress(label="Creating collaborative deck").run_in_background()


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


def ankihub_login_setup(parent):
    sign_in_button = QAction("🔑 Sign into AnkiHub", mw)
    sign_in_button.triggered.connect(AnkiHubLogin.display_login)
    parent.addAction(sign_in_button)


def upload_suggestions_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("⬆️ Upload suggestions to AnkiHub", parent=parent)
    qconnect(q_action.triggered, upload_suggestions_action)
    parent.addAction(q_action)


def subscribe_to_deck_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("📚 Subscribed Decks", mw)
    q_action.triggered.connect(SubscribedDecksDialog.display_subscribe_window)
    parent.addAction(q_action)


def sync_with_ankihub_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("🔃️ Sync with AnkiHub", mw)
    q_action.triggered.connect(sync_with_ankihub_action)
    parent.addAction(q_action)


def ankihub_logout_setup(parent):
    q_action = QAction("🔑 Sign out", mw)
    q_action.triggered.connect(lambda: AnkiHubClient().signout())
    parent.addAction(q_action)


ankihub_menu: QMenu


def setup_ankihub_menu() -> None:
    global ankihub_menu
    ankihub_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ankihub_menu)
    refresh_ankihub_menu()
    config.token_change_hook = refresh_ankihub_menu


def refresh_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    global ankihub_menu
    ankihub_menu.clear()

    if config.private_config.token:
        create_collaborative_deck_setup(parent=ankihub_menu)
        subscribe_to_deck_setup(parent=ankihub_menu)
        sync_with_ankihub_setup(parent=ankihub_menu)
        ankihub_logout_setup(parent=ankihub_menu)
        # upload_suggestions_setup(parent=ankihub_menu)
    else:
        ankihub_login_setup(parent=ankihub_menu)
