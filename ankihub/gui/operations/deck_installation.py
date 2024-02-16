"""Code for downloading and installing decks in the background and showing the related dialogs
(install confirmation dialog, import summary dialog, etc.)."""

import uuid
from concurrent.futures import Future
from datetime import datetime
from functools import partial
from typing import Callable, Dict, List

import aqt
from aqt.operations.tag import clear_unused_tags
from aqt.qt import QDialogButtonBox

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import NoteInfo
from ...ankihub_client.ankihub_client import AnkiHubHTTPError
from ...ankihub_client.models import Deck, UserDeckRelation
from ...main.importing import AnkiHubImporter, AnkiHubImportResult
from ...main.note_types import fetch_note_types_based_on_notes
from ...main.subdecks import deck_contains_subdeck_tags
from ...main.utils import clear_empty_cards, create_backup
from ...settings import BehaviorOnRemoteNoteDeleted, DeckConfig, config
from ..configure_deleted_notes_dialog import ConfigureDeletedNotesDialog
from ..exceptions import DeckDownloadAndInstallError, RemoteDeckNotFoundError
from ..media_sync import media_sync
from ..messages import messages
from ..utils import show_dialog, tooltip_icon
from .subdecks import confirm_and_toggle_subdecks
from .utils import future_with_result, pass_exceptions_to_on_done


@pass_exceptions_to_on_done
def download_and_install_decks(
    ankihub_dids: List[uuid.UUID],
    on_done: Callable[[Future], None],
    cleanup: bool = True,
) -> None:
    """Downloads and installs the given decks in the background."""
    aqt.mw.taskman.with_progress(
        task=lambda: _fetch_deck_infos(ankihub_dids),
        on_done=partial(_on_deck_infos_fetched, on_done=on_done, cleanup=cleanup),
        label="Getting deck information...",
    )


@pass_exceptions_to_on_done
def _on_deck_infos_fetched(
    future: Future, on_done: Callable[[Future], None], cleanup: bool
) -> None:
    decks = future.result()

    # Ask the user how to handle deleted notes
    deck_id_name_tuples = [(deck.ah_did, deck.name) for deck in decks]
    dialog = ConfigureDeletedNotesDialog(
        parent=aqt.mw.app.activeWindow(),
        deck_id_and_name_tuples=deck_id_name_tuples,
    )
    dialog.exec()

    ah_did_to_deletion_behavior = (
        dialog.deck_id_to_behavior_on_remote_note_deleted_dict()
    )

    # Download and install the decks
    aqt.mw.taskman.with_progress(
        task=lambda: _download_and_install_decks_inner(
            decks, ah_did_to_deletion_behavior=ah_did_to_deletion_behavior
        ),
        on_done=partial(_on_install_done, on_done=on_done, cleanup=cleanup),
        label="Downloading decks from AnkiHub...",
    )


@pass_exceptions_to_on_done
def _on_install_done(future: Future, on_done: Callable[[Future], None], cleanup: bool):
    import_results: List[AnkiHubImportResult] = future.result()

    if cleanup:
        _cleanup_after_deck_install()

    # Reset the main window so that the decks are displayed
    aqt.mw.reset()

    # Ask user if they want to enable subdecks
    for import_result in import_results:
        ah_did = import_result.ankihub_did
        if deck_contains_subdeck_tags(ah_did):
            confirm_and_toggle_subdecks(ah_did)

    _show_deck_import_summary_dialog(import_results)

    on_done(future_with_result(None))


def _fetch_deck_infos(ankihub_dids: List[uuid.UUID]) -> List[Deck]:
    result: List[Deck] = []
    for ankihub_did in ankihub_dids:
        try:
            deck = AnkiHubClient().get_deck_by_id(ankihub_did)
        except AnkiHubHTTPError as e:
            if e.response.status_code == 404:
                raise DeckDownloadAndInstallError(
                    RemoteDeckNotFoundError(ankihub_did=ankihub_did),
                    ankihub_did=ankihub_did,
                ) from e
            raise e
        result.append(deck)
    return result


def _show_deck_import_summary_dialog(
    import_results: List[AnkiHubImportResult],
) -> None:
    ankihub_dids = [import_result.ankihub_did for import_result in import_results]
    ankihub_deck_names = [config.deck_config(ah_did).name for ah_did in ankihub_dids]
    anki_deck_names = [
        aqt.mw.col.decks.name(config.deck_config(ah_did).anki_id)
        for ah_did in ankihub_dids
    ]
    message = messages.deck_import_summary(
        ankihub_deck_names=ankihub_deck_names,
        anki_deck_names=anki_deck_names,
        import_results=import_results,
    )

    def on_button_clicked(button_index: int) -> None:
        from ..decks_dialog import DeckManagementDialog

        if button_index == 0:
            DeckManagementDialog.display_subscribe_window()

    show_dialog(
        message,
        title="AnkiHub | Deck Import Summary",
        buttons=["Go to Deck Management", QDialogButtonBox.StandardButton.Ok],
        default_button_idx=1,
        scrollable=True,
        icon=tooltip_icon(),
        callback=on_button_clicked,
    )


def _download_and_install_decks_inner(
    decks: List[Deck],
    ah_did_to_deletion_behavior: Dict[uuid.UUID, BehaviorOnRemoteNoteDeleted],
) -> List[AnkiHubImportResult]:
    """Downloads and installs the given decks.
    Attempts to install all decks even if some fail."""
    result = []
    exceptions = []
    for deck in decks:
        try:
            result.append(
                _download_and_install_single_deck(
                    deck,
                    behavior_on_remote_note_deleted=ah_did_to_deletion_behavior[
                        deck.ah_did
                    ],
                )
            )
        except Exception as e:
            exceptions.append(
                DeckDownloadAndInstallError(
                    original_exception=e,
                    ankihub_did=deck.ah_did,
                )
            )
            LOGGER.warning(
                f"Failed to download and install deck {deck.ah_did}.", exc_info=e
            )

    if exceptions:
        # Raise the first exception that occurred
        raise exceptions[0]

    return result


def _download_and_install_single_deck(
    deck: Deck, behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted
) -> AnkiHubImportResult:
    notes_data: List[NoteInfo] = AnkiHubClient().download_deck(
        deck.ah_did, download_progress_cb=_download_progress_cb
    )

    aqt.mw.taskman.run_on_main(
        lambda: aqt.mw.progress.update(label="Installing deck...", max=0, value=0)
    )
    result = _install_deck(
        notes_data=notes_data,
        deck_name=deck.name,
        ankihub_did=deck.ah_did,
        user_relation=deck.user_relation,
        behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
        latest_update=deck.csv_last_upload,
    )

    return result


def _install_deck(
    notes_data: List[NoteInfo],
    deck_name: str,
    ankihub_did: uuid.UUID,
    user_relation: UserDeckRelation,
    behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted,
    latest_update: datetime,
) -> AnkiHubImportResult:
    """Imports the notes_data into the Anki collection.
    Saves the deck subscription to the config file.
    Starts the media download.
    Returns information about the import.
    """
    create_backup()

    importer = AnkiHubImporter()
    client = AnkiHubClient()
    protected_fields = client.get_protected_fields(ah_did=ankihub_did)
    protected_tags = client.get_protected_tags(ah_did=ankihub_did)
    note_types = fetch_note_types_based_on_notes(notes_data=notes_data)
    import_result = importer.import_ankihub_deck(
        ankihub_did=ankihub_did,
        notes=notes_data,
        note_types=note_types,
        deck_name=deck_name,
        is_first_import_of_deck=True,
        behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
        protected_fields=protected_fields,
        protected_tags=protected_tags,
        suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
        suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
            ankihub_did
        ),
    )

    config.add_deck(
        name=deck_name,
        ankihub_did=ankihub_did,
        anki_did=import_result.anki_did,
        user_relation=user_relation,
        latest_udpate=latest_update,
        behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
    )

    aqt.mw.taskman.run_on_main(media_sync.start_media_download)

    LOGGER.info("Importing deck was succesful.")

    return import_result


def _download_progress_cb(percent: int):
    # adding +1 to avoid progress increasing while at 0% progress
    # (the aqt.mw.progress.update function does that)
    aqt.mw.taskman.run_on_main(
        lambda: aqt.mw.progress.update(
            label="Downloading deck...",
            value=percent + 1,
            max=101,
        )
    )


def _cleanup_after_deck_install() -> None:
    """Clears unused tags and empty cards. We do this because importing a deck which the user
    already has in their collection can result in many unused tags and empty cards."""
    clear_unused_tags(parent=aqt.mw).run_in_background()
    clear_empty_cards()
