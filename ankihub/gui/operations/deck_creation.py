from datetime import datetime, timezone

import aqt
from aqt import QCheckBox, QMessageBox
from aqt.studydeck import StudyDeck
from aqt.utils import showInfo, tooltip

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import get_media_names_from_notes_data
from ...ankihub_client.models import UserDeckRelation
from ...main.deck_creation import DeckCreationResult, create_ankihub_deck
from ...main.subdecks import SUBDECK_TAG
from ...settings import BehaviorOnRemoteNoteDeleted, config, url_view_deck
from ..media_sync import media_sync
from ..operations import AddonQueryOp
from ..utils import ask_user


def create_collaborative_deck() -> None:
    """Creates a new AnkiHub deck and uploads it to AnkiHub.

    Asks the user to confirm, choose a deck to upload and for some additional options,
    and then uploads the deck to AnkiHub.
    When the upload is complete, shows a message to the user with a link to the deck on AnkiHub.
    """

    LOGGER.info("Creating a new AnkiHub deck...")
    config.log_private_config()

    confirm = DeckCreationConfirmationDialog().run()
    if not confirm:
        LOGGER.info("User didn't confirm the deck creation.")
        return

    LOGGER.info("Asking user to choose a deck to upload...")
    StudyDeck(
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
        callback=_on_deck_selected,
    )


def _on_deck_selected(study_deck: StudyDeck) -> None:
    deck_name = study_deck.name
    LOGGER.info("User selected a deck to upload.", deck_name=deck_name)

    if not deck_name:
        return

    client = AnkiHubClient()
    owned_decks = client.get_owned_decks()
    owned_deck_names = {deck.name for deck in owned_decks}
    if deck_name in owned_deck_names:
        showInfo(
            "Select another deck or rename it. You already have a deck with this name on AnkiHub."
        )
        return

    if len(aqt.mw.col.find_cards(f'deck:"{deck_name}"')) == 0:
        showInfo("You can't upload an empty deck.")
        return

    public = ask_user(
        "Would you like to make this deck public?<br><br>"
        'If you choose "No" your deck will be private and only available to users you invite.',
        show_cancel_button=True,
    )
    if public is None:
        return  # pragma: no cover

    private = public is False

    add_subdeck_tags = False
    if aqt.mw.col.decks.children(aqt.mw.col.decks.id_for_name(deck_name)):
        add_subdeck_tags = ask_user(
            "Would you like to add a tag to each note in the deck that indicates which subdeck it belongs to?<br><br>"
            "For example, if you have a deck named <b>My Deck</b> with a subdeck named <b>My Deck::Subdeck</b>, "
            "each note in <b>My Deck::Subdeck</b> will have a tag "
            f"<b>{SUBDECK_TAG}::Subdeck</b> added to it.<br><br>"
            "This allows subscribers to have the same subdeck structure as you have.",
            show_cancel_button=True,
        )
        if add_subdeck_tags is None:
            return  # pragma: no cover

    # TODO Remove this confirmation. We should have a single confirmation that includes all necessary info.
    confirm = ask_user(
        "Uploading the deck to AnkiHub requires modifying notes and note types in "
        f"<b>{deck_name}</b> and will require a full sync afterwards. Would you like to "
        "continue?",
        show_cancel_button=True,
    )
    if not confirm:
        return  # pragma: no cover

    # TODO let's get their confirmation about this in the first dialogue with a checkbox.
    should_upload_media = ask_user(
        "Do you want to upload media for this deck as well? "
        "This will take some extra time but it is required to display images "
        "on AnkiHub and this way subscribers will be able to download media files "
        "when installing the deck. ",
        show_cancel_button=True,
    )
    if should_upload_media is None:
        return  # pragma: no cover

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
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
        )

        # Show a message to the user with a link to the deck on AnkiHub
        deck_url = f"{url_view_deck()}{deck_creation_result.ankihub_did}"
        showInfo(
            "🎉 Deck upload successful!<br><br>"
            "Link to the deck on AnkiHub:<br>"
            f"<a href={deck_url}>{deck_url}</a>"
        )

        LOGGER.info("Deck creation successful.", deck_name=deck_name)
        config.log_private_config()

    def on_failure(exc: Exception) -> None:
        aqt.mw.progress.finish()
        raise exc

    op = AddonQueryOp(
        parent=aqt.mw,
        op=lambda _: create_ankihub_deck(
            deck_name,
            private=private,
            add_subdeck_tags=add_subdeck_tags,
        ),
        success=on_success,
    ).failure(on_failure)
    LOGGER.info("Instantiated QueryOp for creating an AnkiHub deck")
    op.with_progress(label="Creating AnkiHub deck").run_in_background()


class DeckCreationConfirmationDialog(QMessageBox):
    def __init__(self):
        super().__init__(parent=aqt.mw)

        self.setWindowTitle("Confirm AnkiHub Deck Creation")
        self.setIcon(QMessageBox.Icon.Question)
        self.setText(
            "Are you sure you want to create a new AnkiHub deck?<br><br><br>"
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
