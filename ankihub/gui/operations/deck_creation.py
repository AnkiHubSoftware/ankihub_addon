from datetime import datetime, timezone

import aqt
from aqt import QCheckBox, QMessageBox
from aqt.operations import QueryOp
from aqt.studydeck import StudyDeck
from aqt.utils import showInfo, tooltip

from ... import LOGGER
from ...ankihub_client import get_media_names_from_notes_data
from ...ankihub_client.models import UserDeckRelation
from ...main.deck_creation import DeckCreationResult, create_ankihub_deck
from ...main.subdecks import SUBDECK_TAG
from ...settings import config, url_view_deck
from ..media_sync import media_sync
from ..utils import ask_user


def create_collaborative_deck() -> None:
    """Creates a new collaborative deck and uploads it to AnkiHub.

    Asks the user to confirm, choose a deck to upload and for some additional options,
    and then uploads the deck to AnkiHub.
    When the upload is complete, shows a message to the user with a link to the deck on AnkiHub.
    """

    confirm = DeckCreationConfirmationDialog().run()
    if not confirm:
        return

    LOGGER.info("Asking user to choose a deck to upload...")
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
        parent=aqt.mw,
    )
    LOGGER.info(f"Closed deck chooser dialog: {deck_chooser}")
    LOGGER.info(f"Chosen deck name: {deck_chooser.name}")
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

    should_upload_media = ask_user(
        "Do you want to upload media for this deck as well? "
        "This will take some extra time but it is required to display images "
        "on AnkiHub and this way subscribers will be able to download media files "
        "when installing the deck. "
    )

    def on_success(deck_creation_result: DeckCreationResult) -> None:

        # Upload all existing local media for this deck
        # (media files that are referenced on Deck's notes)
        if should_upload_media:
            media_names = get_media_names_from_notes_data(
                deck_creation_result.notes_data
            )
            media_sync.start_media_upload(media_names, deck_creation_result.ankihub_did)

        # Add the deck to the list of decks the user owns
        anki_did = aqt.mw.col.decks.id_for_name(deck_name)
        creation_time = datetime.now(tz=timezone.utc)
        config.add_deck(
            deck_name,
            deck_creation_result.ankihub_did,
            anki_did,
            user_relation=UserDeckRelation.OWNER,
            latest_udpate=creation_time,
        )

        # Show a message to the user with a link to the deck on AnkiHub
        deck_url = f"{url_view_deck()}{deck_creation_result.ankihub_did}"
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
        op=lambda col: create_ankihub_deck(
            deck_name,
            private=private,
            add_subdeck_tags=add_subdeck_tags,
        ),
        success=on_success,
    ).failure(on_failure)
    LOGGER.info("Instantiated QueryOp for creating collaborative deck")
    op.with_progress(label="Creating collaborative deck").run_in_background()


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