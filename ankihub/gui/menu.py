import re
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Optional

from aqt import (
    AnkiApp,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    mw,
)
from aqt.operations import QueryOp
from aqt.qt import QAction, QDialog, QMenu, Qt, qconnect
from aqt.studydeck import StudyDeck
from aqt.utils import openLink, showInfo, showText, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import AnkiHubRequestError
from ..subdecks import SUBDECK_TAG
from ..error_reporting import upload_logs_in_background
from ..media_import.ui import open_import_dialog
from ..register_decks import create_collaborative_deck
from ..settings import ADDON_VERSION, URL_VIEW_DECK, config
from ..sync import sync_with_progress
from .decks import SubscribedDecksDialog
from .utils import ask_user


class AnkiHubLogin(QWidget):
    _window: Optional["AnkiHubLogin"] = None
    silentlyClose = True

    def __init__(self):
        super(AnkiHubLogin, self).__init__()
        self.results = None
        self.thread = None  # type: ignore
        self.box_top = QVBoxLayout()
        self.box_upper = QHBoxLayout()
        self.box_left = QVBoxLayout()
        self.box_right = QVBoxLayout()
        self.bottom_box_section = QHBoxLayout()

        # Username
        self.username_or_email_box = QHBoxLayout()
        self.username_or_email_box_label = QLabel("Username or E-mail:")
        self.username_or_email_box_text = QLineEdit("", self)
        self.username_or_email_box_text.setMinimumWidth(300)
        self.username_or_email_box.addWidget(self.username_or_email_box_label)
        self.username_or_email_box.addWidget(self.username_or_email_box_text)
        self.box_left.addLayout(self.username_or_email_box)

        # Password
        self.password_box = QHBoxLayout()
        self.password_box_label = QLabel("Password:")
        self.password_box_text = QLineEdit("", self)
        self.password_box_text.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_box_text.setMinimumWidth(300)
        qconnect(self.password_box_text.returnPressed, self.login)
        self.password_box.addWidget(self.password_box_label)
        self.password_box.addWidget(self.password_box_text)
        self.box_left.addLayout(self.password_box)

        # Login
        self.login_button = QPushButton("Login", self)
        self.bottom_box_section.addWidget(self.login_button)
        qconnect(self.login_button.clicked, self.login)
        self.login_button.setDefault(True)

        self.box_left.addLayout(self.bottom_box_section)

        # Add left and right layouts to upper
        self.box_upper.addLayout(self.box_left)
        self.box_upper.addSpacing(20)
        self.box_upper.addLayout(self.box_right)

        # Add all widgets to top layout.
        self.box_top.addLayout(self.box_upper)
        self.box_top.addStretch(1)
        self.setLayout(self.box_top)

        self.setMinimumWidth(500)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWindowTitle("Login to AnkiHub.")
        self.show()

    def login(self):
        username_or_email = self.username_or_email_box_text.text()
        password = self.password_box_text.text()
        if not all([username_or_email, password]):
            showText("Oops! You forgot to put in a username or password!")
            return
        ankihub_client = AnkiHubClient()

        try:
            credentials = {"password": password}
            if self._is_email(username_or_email):
                credentials.update({"email": username_or_email})
            else:
                credentials.update({"username": username_or_email})
            token = ankihub_client.login(credentials=credentials)
        except AnkiHubRequestError as e:
            LOGGER.exception("AnkiHub login failed.")
            config.save_token("")

            if e.response.status_code == 400:
                tooltip("Wrong credentials.", parent=mw)
                return

            raise e

        config.save_token(token)
        config.save_user_email(username_or_email)

        tooltip("Signed into AnkiHub!", parent=mw)
        self.close()

    def _is_email(self, value):
        return re.fullmatch(
            r"^[a-zA-Z0-9.!#$%&’*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*$",
            value,
        )

    @classmethod
    def display_login(cls):
        if cls._window is None:
            cls._window = cls()
        else:
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()
        return cls._window


class DeckCreationConfirmationDialog(QMessageBox):
    def __init__(self):
        super().__init__(parent=mw)

        self.setWindowTitle("Confirm AnkiHub Deck Creation")
        self.setIcon(QMessageBox.Icon.Question)
        self.setText(
            "Are you sure you want to create a new collaborative deck?<br><br><br>"
            'Terms of use: <a href="https://www.ankihub.net/terms">https://www.ankihub.net/terms</a><br>'
            'Privacy Policy: <a href="https://www.ankihub.net/privacy">https://www.ankihub.net/privacy</a><br>',
        )
        self.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel  # type: ignore
        )
        self.confirmation_cb = QCheckBox(
            text=" by checking this checkbox you agree to the terms of use",
            parent=self,
        )
        self.setCheckBox(self.confirmation_cb)

    def run(self) -> bool:
        clicked_ok = self.exec() == QMessageBox.StandardButton.Yes
        if not clicked_ok:
            return False

        if not self.confirmation_cb.isChecked():
            tooltip("You didn't agree to the terms of use.")
            return False

        return True


def create_collaborative_deck_action() -> None:

    confirm = DeckCreationConfirmationDialog().run()
    if not confirm:
        return

    deck_chooser = StudyDeck(
        mw,
        title="AnkiHub",
        accept="Upload",
        # Removes the "Add" button
        buttons=[],
        names=lambda: [
            d.name
            for d in mw.col.decks.all_names_and_ids(include_filtered=False)
            if "::" not in d.name and d.id != 1
        ],
    )
    deck_name = deck_chooser.name
    if not deck_name:
        return

    if len(mw.col.find_cards(f'deck:"{deck_name}"')) == 0:
        showText("You can't upload an empty deck.")
        return

    public = ask_user(
        "Would you like to make this deck public?<br><br>"
        'If you chose "No" it will be private and only people with a link '
        "will be able to see it on the AnkiHub website."
    )
    if public is None:
        return

    private = public is False

    add_subdeck_tags = False
    if mw.col.decks.children(mw.col.decks.id_for_name(deck_name)):
        add_subdeck_tags = ask_user(
            "Would you like to add a tag to each note in the deck that indicates which subdeck it belongs to?<br><br>"
            "For example, if you have a deck named <b>My Deck</b> with a subdeck named <b>My Deck::Subdeck</b>, "
            "each note in <b>My Deck::Subdeck</b> will have a tag "
            f"<b>{SUBDECK_TAG}::Subdeck</b> added to it."
        )
        if add_subdeck_tags is None:
            return

    confirm = ask_user(
        "Uploading the deck to AnkiHub requires modifying notes and note types in "
        f"<b>{deck_name}</b> and will require a full sync afterwards. Would you like to "
        "continue?",
    )
    if not confirm:
        return

    def on_success(ankihub_did: uuid.UUID) -> None:
        anki_did = mw.col.decks.id_for_name(deck_name)
        creation_time = datetime.now(tz=timezone.utc)
        config.save_subscription(
            deck_name,
            ankihub_did,
            anki_did,
            creator=True,
            latest_udpate=creation_time,
        )
        deck_url = f"{URL_VIEW_DECK}{ankihub_did}"
        showInfo(
            "🎉 Deck upload successful!<br><br>"
            "Link to the deck on AnkiHub:<br>"
            f"<a href={deck_url}>{deck_url}</a>"
        )

    def on_failure(exc: Exception):
        mw.progress.finish()
        raise exc

    op = QueryOp(
        parent=mw,
        op=lambda col: create_collaborative_deck(
            deck_name, private=private, add_subdeck_tags=add_subdeck_tags
        ),
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
    sync_with_progress()


def sign_out_action():
    try:
        AnkiHubClient().signout()
    finally:
        config.save_token("")
        tooltip("Signed out of AnkiHub!", parent=mw)


class LogUploadResultDialog(QDialog):
    def __init__(self, log_file_name: str):
        super().__init__(parent=mw)

        self.setWindowTitle("AnkiHub")

        self.layout_ = QVBoxLayout()
        self.setLayout(self.layout_)

        self.label = QLabel(
            "Logs uploaded successfully!<br><br>"
            " Please copy this file name and include it in your bug report:<br><br>"
            f"<b>{log_file_name}</b>",
        )
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.layout_.addWidget(self.label)

        self.layout_.addSpacing(8)

        def on_click() -> None:
            AnkiApp.clipboard().setText(log_file_name)

        self.button = QPushButton("Copy to clipboard")
        self.button.clicked.connect(on_click)  # type: ignore
        self.layout_.addWidget(self.button)


def upload_logs_action():
    if not ask_user(
        "Do you want to upload the add-on's logs to AnkiHub to go along a bug report?"
    ):
        return

    def on_done(future: Future):
        mw.progress.finish()
        log_file_name = future.result()
        LogUploadResultDialog(log_file_name=log_file_name).exec()

    mw.progress.start(label="Uploading logs...", parent=mw, immediate=True)
    upload_logs_in_background(on_done=on_done, hide_username=True)


def ankihub_login_setup(parent):
    sign_in_button = QAction("🔑 Sign into AnkiHub", mw)
    qconnect(sign_in_button.triggered, AnkiHubLogin.display_login)
    parent.addAction(sign_in_button)


def upload_suggestions_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("⬆️ Upload suggestions to AnkiHub", parent=parent)
    qconnect(q_action.triggered, upload_suggestions_action)
    parent.addAction(q_action)


def subscribe_to_deck_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("📚 Subscribed Decks", mw)
    qconnect(q_action.triggered, SubscribedDecksDialog.display_subscribe_window)
    parent.addAction(q_action)


def import_media_setup(parent):
    q_action = QAction("🖼️ Import media", mw)
    qconnect(q_action.triggered, open_import_dialog)
    parent.addAction(q_action)


def sync_with_ankihub_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("🔃️ Sync with AnkiHub", mw)
    qconnect(q_action.triggered, sync_with_ankihub_action)
    if not config.deck_ids():
        q_action.setDisabled(True)
    parent.addAction(q_action)


def ankihub_help_setup(parent):
    """Set up the sub menu for help related items."""
    help_menu = QMenu("🆘 Help", parent)

    # && is an escaped & in qt
    q_notion_action = QAction("Instructions && Changelog", help_menu)
    qconnect(
        q_notion_action.triggered,
        lambda: openLink(
            "https://www.notion.so/ankipalace/AnkiHub-Documentation-dd8584f3e6c04068ab47e072c17b3a0a"
        ),
    )
    help_menu.addAction(q_notion_action)

    q_get_help_action = QAction("Get Help", help_menu)
    qconnect(
        q_get_help_action.triggered, lambda: openLink("https://www.ankihub.net/support")
    )
    help_menu.addAction(q_get_help_action)

    q_upload_logs_action = QAction("Upload logs", help_menu)
    qconnect(q_upload_logs_action.triggered, upload_logs_action)
    help_menu.addAction(q_upload_logs_action)

    q_version_action = QAction(f"Version {ADDON_VERSION}", help_menu)
    q_version_action.setEnabled(False)
    help_menu.addAction(q_version_action)

    help_menu.setMinimumWidth(250)

    parent.addMenu(help_menu)


def ankihub_logout_setup(parent):
    q_action = QAction("🔑 Sign out", mw)
    qconnect(q_action.triggered, sign_out_action)
    parent.addAction(q_action)


ankihub_menu: QMenu


def setup_ankihub_menu() -> None:
    global ankihub_menu
    ankihub_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ankihub_menu)
    refresh_ankihub_menu()
    config.token_change_hook = lambda: mw.taskman.run_on_main(refresh_ankihub_menu)
    config.subscriptions_change_hook = lambda: mw.taskman.run_on_main(
        refresh_ankihub_menu
    )


def refresh_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    global ankihub_menu
    ankihub_menu.clear()

    if config.token():
        create_collaborative_deck_setup(parent=ankihub_menu)
        subscribe_to_deck_setup(parent=ankihub_menu)
        import_media_setup(parent=ankihub_menu)
        sync_with_ankihub_setup(parent=ankihub_menu)
        ankihub_logout_setup(parent=ankihub_menu)
        # upload_suggestions_setup(parent=ankihub_menu)
    else:
        ankihub_login_setup(parent=ankihub_menu)

    ankihub_help_setup(parent=ankihub_menu)
