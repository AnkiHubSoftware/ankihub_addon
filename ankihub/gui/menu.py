import re
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aqt
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
)
from aqt.operations import QueryOp
from aqt.qt import QAction, QDialog, QKeySequence, QMenu, Qt, qconnect
from aqt.studydeck import StudyDeck
from aqt.utils import openLink, showInfo, tooltip
from requests.exceptions import ConnectionError

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import AnkiHubRequestError, get_image_names_from_notes_data
from ..db import ankihub_db
from ..error_reporting import upload_logs_in_background
from ..media_import.ui import open_import_dialog
from ..register_decks import create_collaborative_deck
from ..settings import ADDON_VERSION, config, url_view_deck
from ..subdecks import SUBDECK_TAG
from ..sync import ah_sync, show_tooltip_about_last_sync_results
from .db_check import maybe_check_databases
from .decks import SubscribedDecksDialog
from .utils import (
    ask_user,
    check_and_prompt_for_updates_on_main_window,
    choose_ankihub_deck,
)


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
            showInfo("Oops! You forgot to put in a username or password!")
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
                tooltip("Wrong credentials.", parent=aqt.mw)
                return

            raise e

        config.save_token(token)
        config.save_user_email(username_or_email)

        tooltip("Signed into AnkiHub!", parent=aqt.mw)
        self.close()

    def _is_email(self, value):
        return re.fullmatch(
            r"^[a-zA-Z0-9.!#$%&’*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*$",
            value,
        )

    def clear_fields(self):
        self.username_or_email_box_text.setText("")
        self.password_box_text.setText("")

    @classmethod
    def display_login(cls):
        if cls._window is None:
            cls._window = cls()
        else:
            cls._window.clear_fields()
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()
        return cls._window


class DeckCreationConfirmationDialog(QMessageBox):
    def __init__(self):
        super().__init__(parent=aqt.mw)

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
        aqt.mw,
        title="AnkiHub",
        accept="Upload",
        # Removes the "Add" button
        buttons=[],
        names=lambda: [
            d.name
            for d in aqt.mw.col.decks.all_names_and_ids(include_filtered=False)
            if "::" not in d.name and d.id != 1
        ],
    )
    deck_name = deck_chooser.name
    if not deck_name:
        return

    if len(aqt.mw.col.find_cards(f'deck:"{deck_name}"')) == 0:
        showInfo("You can't upload an empty deck.")
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
    if aqt.mw.col.decks.children(aqt.mw.col.decks.id_for_name(deck_name)):
        add_subdeck_tags = ask_user(
            "Would you like to add a tag to each note in the deck that indicates which subdeck it belongs to?<br><br>"
            "For example, if you have a deck named <b>My Deck</b> with a subdeck named <b>My Deck::Subdeck</b>, "
            "each note in <b>My Deck::Subdeck</b> will have a tag "
            f"<b>{SUBDECK_TAG}::Subdeck</b> added to it.<br><br>"
            "This allows subscribers to have the same subdeck structure as you have."
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

    should_upload_assets = False
    if AnkiHubClient().is_feature_flag_enabled("image_support_enabled"):
        confirm = ask_user(
            "Do you want to upload images for this deck as well? "
            "This will take some extra time but it is required to display the images "
            "on AnkiHub and this way subscribers will be able to download the images "
            "when installing the deck. "
        )
        if confirm:
            should_upload_assets = True

    def on_success(ankihub_did: uuid.UUID) -> None:
        anki_did = aqt.mw.col.decks.id_for_name(deck_name)
        creation_time = datetime.now(tz=timezone.utc)
        config.save_subscription(
            deck_name,
            ankihub_did,
            anki_did,
            creator=True,
            latest_udpate=creation_time,
        )
        deck_url = f"{url_view_deck()}{ankihub_did}"
        showInfo(
            "🎉 Deck upload successful!<br><br>"
            "Link to the deck on AnkiHub:<br>"
            f"<a href={deck_url}>{deck_url}</a>"
        )

    def on_failure(exc: Exception):
        aqt.mw.progress.finish()
        raise exc

    op = QueryOp(
        parent=aqt.mw,
        op=lambda col: create_collaborative_deck(
            deck_name,
            private=private,
            add_subdeck_tags=add_subdeck_tags,
            should_upload_assets=should_upload_assets,
        ),
        success=on_success,
    ).failure(on_failure)
    LOGGER.info("Instantiated QueryOp for creating collaborative deck")
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
    aqt.mw.taskman.with_progress(
        task=ah_sync.sync_all_decks_and_media,
        immediate=True,
        label="Syncing with AnkiHub",
        on_done=on_sync_done,
    )


def on_sync_done(future: Future) -> None:
    future.result()

    show_tooltip_about_last_sync_results()

    maybe_check_databases()


def sign_out_action():
    try:
        AnkiHubClient().signout()
    finally:
        config.save_token("")
        tooltip("Signed out of AnkiHub!", parent=aqt.mw)


class LogUploadResultDialog(QDialog):
    def __init__(self, log_file_name: str):
        super().__init__(parent=aqt.mw)

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
        aqt.mw.progress.finish()
        log_file_name = future.result()
        LogUploadResultDialog(log_file_name=log_file_name).exec()

    aqt.mw.progress.start(label="Uploading logs...", parent=aqt.mw, immediate=True)
    upload_logs_in_background(on_done=on_done, hide_username=True)


def ankihub_login_setup(parent):
    sign_in_button = QAction("🔑 Sign into AnkiHub", aqt.mw)
    qconnect(sign_in_button.triggered, AnkiHubLogin.display_login)
    parent.addAction(sign_in_button)


def upload_suggestions_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("⬆️ Upload suggestions to AnkiHub", parent=parent)
    qconnect(q_action.triggered, upload_suggestions_action)
    parent.addAction(q_action)


def subscribe_to_deck_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("📚 Subscribed Decks", aqt.mw)
    qconnect(q_action.triggered, SubscribedDecksDialog.display_subscribe_window)
    parent.addAction(q_action)


def import_media_setup(parent):
    q_action = QAction("🖼️ Import media", aqt.mw)
    qconnect(q_action.triggered, open_import_dialog)
    parent.addAction(q_action)


def sync_with_ankihub_setup(parent):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("🔃️ Sync with AnkiHub", aqt.mw)
    qconnect(q_action.triggered, sync_with_ankihub_action)
    if sync_hotkey := config.public_config["sync_hotkey"]:
        try:
            q_action.setShortcut(QKeySequence(sync_hotkey))
        except Exception:
            LOGGER.exception(f"Failed to set sync hotkey to {sync_hotkey}")
    if not config.deck_ids():
        q_action.setDisabled(True)
    parent.addAction(q_action)


def upload_deck_assets_setup(parent):
    """Set up the menu item for manually triggering the upload
    of all the assets for a given deck (logged user MUST be the
    deck owner)"""

    q_action = QAction("📸 Upload images for deck", aqt.mw)
    qconnect(q_action.triggered, upload_deck_assets_action)
    parent.addAction(q_action)


def upload_deck_assets_action() -> None:
    client = AnkiHubClient()

    if not client.is_feature_flag_enabled("image_support_enabled"):
        showInfo(
            "The image support feature is not enabled yet for your account.<br>"
            "We are working on it and it will be available soon for everyone 📸"
        )
        return

    # Fetch the ankihub deck ids of all decks the user owns
    owned_ah_dids = client.owned_deck_ids()

    # If the user has no owned decks, we should show a message informing them
    # about this and not allow them to upload images.
    if not owned_ah_dids:
        showInfo(
            "<b>Oh no!</b> 🙁<br>"
            "You do not own any AnkiHub decks. You can only perform a full image upload for decks that you own.<br><br>"
            "Maybe try creating a new AnkiHub deck for yourself, or create a note suggestion instead? 🙂"
        )
        return

    # The user owns one or more Decks but they are not installed locally
    if owned_ah_dids and not any(
        [did for did in owned_ah_dids if did in config.deck_ids()]
    ):
        showInfo(
            "<b>Oh no!</b> 🙁<br>"
            "It seems that you have deck(s) that you own at AnkiHub, but none of them are installed locally.<br><br>"
            "Plase subscribe to the deck from the add-on before trying to upload images for it 🙂"
        )
        return

    # Displays a window for the user to select which Deck they want to upload images for.
    # This will only display Decks that the user owns AND are installed locally. Maintainers
    # and subscribers should not be able to upload images for Decks they maintain/subscribe.
    ah_did = choose_ankihub_deck(
        "Choose the AnkiHub deck for which<br>you want to upload images.",
        parent=aqt.mw,
        ah_dids=owned_ah_dids,
    )
    if ah_did is None:
        return

    deck_config = config.deck_config(ah_did)
    deck_name = deck_config.name

    nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
    if not nids:
        showInfo("You can't upload images for an empty deck.")
        return

    # Obtain a list of NoteInfo objects from nids
    notes_data = [ankihub_db.note_data(nid) for nid in nids]

    image_names = get_image_names_from_notes_data(notes_data)
    image_paths = [
        Path(aqt.mw.col.media.dir()) / image_name for image_name in image_names
    ]

    # Check if the deck references any local asset, if it does
    # not, no point on trying to upload it
    if not image_paths:
        showInfo("This deck has no images to upload.")
        return

    # Check if the files referenced by the deck exists locally, if none exist, no point in uploading.
    if not any([image_path.is_file() for image_path in image_paths]):
        showInfo(
            "You can't upload images for this deck because none of the referenced images are present in your "
            "local media folder."
        )
        return

    confirm = ask_user(
        f"Uploading all images for the deck <b>{deck_name}</b> to AnkiHub "
        "might take a while depending on the number of images that the deck uses.<br><br>"
        "Would you like to continue?",
    )
    if not confirm:
        return

    def on_done(future: Future) -> None:
        future.result()
        showInfo("🎉 Successfuly uploaded all images for the deck!")
        LOGGER.info("Finished uploading assets for deck")

    # Extract the AnkiHub deck ID using a sample note id
    ah_did = ankihub_db.ankihub_did_for_anki_nid(nids[0])

    aqt.mw.taskman.run_in_background(
        task=client.upload_assets_for_deck,
        args={"ah_did": ah_did, "notes_data": notes_data},
        on_done=on_done,
    )
    showInfo(
        "🖼️ Upload started! You can continue using Anki in the meantime."
        "<br><br>We'll notify you when the upload process finishes 👍"
    )


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

    q_downgrade_from_beta_version_action = QAction(
        "Downgrade from add-on beta version", help_menu
    )
    qconnect(
        q_downgrade_from_beta_version_action.triggered, trigger_install_release_version
    )
    help_menu.addAction(q_downgrade_from_beta_version_action)

    q_version_action = QAction(f"Version {ADDON_VERSION}", help_menu)
    q_version_action.setEnabled(False)
    help_menu.addAction(q_version_action)
    help_menu.setMinimumWidth(250)

    parent.addMenu(help_menu)


def trigger_install_release_version():
    showInfo(
        "When you click OK, the add-on update dialog will open in a couple of seconds "
        "and you will be able to install the version of the add-on that is available on AnkiWeb.<br><br>"
        "If it doesn't show up (which can happen when you e.g. have no internet connection), "
        "try checking for add-on updates manually later."
    )

    # Set the installed_at timestamp to make Anki think that the add-on has an older version
    # installed than the one that is available on AnkiWeb.
    addon_module = aqt.mw.addonManager.addonFromModule(__name__)
    addon_meta = aqt.mw.addonManager.addon_meta(addon_module)
    addon_meta.installed_at = 0
    aqt.mw.addonManager.write_addon_meta(addon_meta)

    check_and_prompt_for_updates_on_main_window()


def ankihub_logout_setup(parent):
    q_action = QAction("🔑 Sign out", aqt.mw)
    qconnect(q_action.triggered, sign_out_action)
    parent.addAction(q_action)


def media_download_status_setup(parent: QMenu):
    image_support_enabled = False
    try:
        image_support_enabled = AnkiHubClient().is_feature_flag_enabled(
            "image_support_enabled"
        )
    except ConnectionError:
        # It's ok to not setup the menu when the feature flag status can't be retrieved.
        return

    if not image_support_enabled:
        return

    global media_download_status_action
    media_download_status_action = QAction("Media download: Idle.", parent)
    parent.addAction(media_download_status_action)


ankihub_menu: Optional[QMenu] = None
media_download_status_action: Optional[QAction] = None


def setup_ankihub_menu() -> None:
    global ankihub_menu
    ankihub_menu = QMenu("&AnkiHub", parent=aqt.mw)
    aqt.mw.form.menubar.addMenu(ankihub_menu)
    config.token_change_hook = lambda: aqt.mw.taskman.run_on_main(refresh_ankihub_menu)
    config.subscriptions_change_hook = lambda: aqt.mw.taskman.run_on_main(
        refresh_ankihub_menu
    )
    refresh_ankihub_menu()


def refresh_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    global ankihub_menu
    ankihub_menu.clear()

    if config.is_logged_in():
        create_collaborative_deck_setup(parent=ankihub_menu)
        subscribe_to_deck_setup(parent=ankihub_menu)
        import_media_setup(parent=ankihub_menu)
        sync_with_ankihub_setup(parent=ankihub_menu)
        upload_deck_assets_setup(parent=ankihub_menu)
        ankihub_logout_setup(parent=ankihub_menu)
        media_download_status_setup(parent=ankihub_menu)
        # upload_suggestions_setup(parent=ankihub_menu)
    else:
        ankihub_login_setup(parent=ankihub_menu)

    ankihub_help_setup(parent=ankihub_menu)
