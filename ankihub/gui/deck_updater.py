"""Downloads updates to decks from AnkiHub and imports them."""

import uuid
from datetime import datetime
from functools import cached_property
from typing import Collection, Dict, List, Optional, cast

import aqt
from anki.errors import NotFoundError
from anki.models import NotetypeDict, NotetypeId
from aqt.utils import showInfo, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import AnkiHubHTTPError, DeckExtension
from ..ankihub_client.models import NotesActionChoices
from ..db import ankihub_db
from ..main.importing import AnkiHubImporter, AnkiHubImportResult
from ..main.note_conversion import is_tag_for_group
from ..main.utils import create_backup
from ..settings import config
from .media_sync import media_sync
from .operations.scheduling import unsuspend_notes
from .utils import deck_download_progress_cb, show_error_dialog


class NotLoggedInError(Exception):
    pass


class _AnkiHubDeckUpdater:
    def __init__(self):
        self._importer = AnkiHubImporter()
        self._import_results: Optional[List[AnkiHubImportResult]] = None

    @cached_property
    def _client(self) -> AnkiHubClient:
        # The client can't be initialized in __init__ because the add-on config is not set up yet at that point.
        return AnkiHubClient()

    def update_decks_and_media(
        self,
        ah_dids: Collection[uuid.UUID],
        raise_if_full_sync_required: bool,
        start_media_sync: bool = True,
    ) -> List[AnkiHubImportResult]:
        """Fetch and apply deck updates from AnkiHub for the given decks and start the media download.
        Also updates deck extensions.
        Should be called from a background thread with a progress dialog to avoid blocking the UI.
        Returns the results of the imports of the updates."""
        LOGGER.info(
            "Updating decks and media...",
            ah_dids=ah_dids,
            start_media_sync=start_media_sync,
        )

        self._import_results = None
        self._raise_if_full_sync_required = raise_if_full_sync_required

        if not config.is_logged_in():
            LOGGER.info("User is not logged in, can't update decks.")
            raise NotLoggedInError()

        self._import_results = []
        self._update_decks(ah_dids)

        # The media sync should be started after the deck updates are imported,
        # because the import can add new media references to notes.
        if start_media_sync:
            aqt.mw.taskman.run_on_main(media_sync.start_media_download)

        LOGGER.info("Finished updating decks.")
        return self._import_results

    def last_deck_updates_results(self) -> Optional[List[AnkiHubImportResult]]:
        """Returns the results of the last deck updates. Returns None if no update has been performed yet or
        if the last update process failed."""
        return self._import_results

    def _update_decks(self, ah_dids: Collection[uuid.UUID]) -> None:
        """Fetches and applies updates for the given decks and their extensions."""
        LOGGER.info("Updating decks...", ah_dids=ah_dids)

        create_backup()

        for ah_did in ah_dids:
            try:
                should_continue = self._update_single_deck(ah_did)
                if not should_continue:
                    return
            except AnkiHubHTTPError as e:
                if self._handle_exception(e, ah_did):
                    return
                else:
                    raise e

    def _update_single_deck(self, ankihub_did: uuid.UUID) -> bool:
        """Fetches and applies updates for a single deck. Also updates the deck extensions of the deck.
        Returns True if the update was successful, False if the user cancelled it."""
        self._update_deck_config(ankihub_did)

        result = self._fetch_and_apply_deck_updates(ankihub_did)
        if not result:
            return False

        result = self._fetch_and_apply_deck_extension_updates(ankihub_did)
        if not result:
            return False

        if ankihub_did == config.anking_deck_id:
            self.fetch_and_apply_pending_notes_actions_for_deck(ankihub_did)

        return True

    def _update_deck_config(self, ankihub_did: uuid.UUID) -> None:
        deck = self._client.get_deck_by_id(ankihub_did)
        config.update_deck(deck=deck)

    def _fetch_and_apply_deck_updates(self, ankihub_did) -> bool:
        """Downloads note updates from AnkiHub and imports them into Anki.
        Returns True if the action was successful, False if the user cancelled it."""

        deck_config = config.deck_config(ankihub_did)
        deck_updates = self._client.get_deck_updates(
            ankihub_did,
            since=deck_config.latest_update,
            download_full_deck=deck_config.download_full_deck_on_next_sync,
            updates_download_progress_cb=lambda notes_count: _update_deck_updates_download_progress_cb(
                notes_count, ankihub_did=ankihub_did
            ),
            deck_download_progress_cb=deck_download_progress_cb,
            should_cancel=lambda: aqt.mw.progress.want_cancel(),
        )
        if deck_updates is None:
            LOGGER.info("User cancelled deck update.")
            return False

        note_types = cast(
            Dict[NotetypeId, NotetypeDict],
            self._client.get_note_types_dict_for_deck(ankihub_did),
        )

        import_result = self._importer.import_ankihub_deck(
            ankihub_did=ankihub_did,
            notes=deck_updates.notes,
            note_types=note_types,
            deck_name=deck_config.name,
            is_first_import_of_deck=False,
            behavior_on_remote_note_deleted=deck_config.behavior_on_remote_note_deleted,
            anki_did=deck_config.anki_id,
            protected_fields=deck_updates.protected_fields,
            protected_tags=deck_updates.protected_tags,
            subdecks=deck_config.subdecks_enabled,
            suspend_new_cards_of_new_notes=deck_config.suspend_new_cards_of_new_notes,
            suspend_new_cards_of_existing_notes=deck_config.suspend_new_cards_of_existing_notes,
            raise_if_full_sync_required=self._raise_if_full_sync_required,
            clear_ah_note_types_before_import=True,
        )
        self._import_results.append(import_result)

        if deck_updates.latest_update:
            # latest_update is None if there were no notes in the updates
            config.save_latest_deck_update(ankihub_did, deck_updates.latest_update)

        config.set_download_full_deck_on_next_sync(ankihub_did, False)

        return True

    def fetch_and_apply_pending_notes_actions_for_deck(
        self, ankihub_did: uuid.UUID
    ) -> None:
        pending_notes_actions = self._client.get_pending_notes_actions_for_deck(
            ankihub_did
        )
        if not pending_notes_actions:
            LOGGER.info(
                "No pending notes actions to apply for deck", ah_did=ankihub_did
            )
            return

        for pending_note_action in pending_notes_actions:
            if pending_note_action.action != NotesActionChoices.UNSUSPEND:
                raise NotImplementedError(  # pragma: no cover
                    f"Unsupported pending notes action: {pending_note_action.action}"
                )
            unsuspend_notes(ah_nids=pending_note_action.note_ids)

    def _fetch_and_apply_deck_extension_updates(self, ankihub_did: uuid.UUID) -> bool:
        # returns True if the update was successful, False if the user cancelled it
        if not (
            deck_extensions := self._client.get_deck_extensions_by_deck_id(ankihub_did)
        ):
            LOGGER.info("No extensions to update for deck", ah_did=ankihub_did)
            return True

        for deck_extension in deck_extensions:
            if not self._download_updates_for_extension(deck_extension):
                return False

        return True

    def _download_updates_for_extension(self, deck_extension: DeckExtension) -> bool:
        # returns True if the update was successful, False if the user cancelled it
        config.create_or_update_deck_extension_config(deck_extension)
        deck_extension_config = config.deck_extension_config(deck_extension.id)
        latest_update: Optional[datetime] = None
        updated_notes = []
        for chunk in self._client.get_deck_extension_updates(
            deck_extension_id=deck_extension.id,
            since=deck_extension_config.latest_update,
            download_progress_cb=lambda note_customizations_count: _update_extension_download_progress_cb(
                note_customizations_count, deck_extension.id
            ),
        ):
            if not chunk.note_customizations:
                continue

            if aqt.mw.progress.want_cancel():
                LOGGER.info("User cancelled extension update.")
                return False

            for customization in chunk.note_customizations:
                anki_nid = ankihub_db.anki_nid_for_ankihub_nid(
                    customization.ankihub_nid
                )
                try:
                    note = aqt.mw.col.get_note(anki_nid)
                except NotFoundError:
                    LOGGER.warning(
                        "Tried to apply customization to note but note was not found.",
                        ah_nid=customization.ankihub_nid,
                    )
                    continue
                else:
                    # Only tags from this tag group should be modified.
                    tags_not_from_this_tag_group = [
                        tag
                        for tag in note.tags
                        if not is_tag_for_group(tag, deck_extension.tag_group_name)
                    ]
                    note.tags = tags_not_from_this_tag_group + customization.tags
                    updated_notes.append(note)

            # each chunk contains the latest update timestamp of the notes in it, we need the latest one
            latest_update = max(
                chunk.latest_update, latest_update or chunk.latest_update
            )

        if updated_notes:
            aqt.mw.col.update_notes(updated_notes)

        if latest_update:
            config.save_latest_extension_update(deck_extension.id, latest_update)

        return True

    def _handle_exception(self, exc: AnkiHubHTTPError, ankihub_did: uuid.UUID) -> bool:
        # returns True if the exception was handled

        if "/updates" not in exc.response.url:
            return False

        deck_config = config.deck_config(ankihub_did)

        if exc.response.status_code == 403:
            response_data = exc.response.json()
            error_message = response_data.get("detail")
            if error_message:
                show_error_dialog(
                    error_message,
                    title="Error while downloading updates for deck :(",
                )
                return True
            else:
                raise exc
        elif exc.response.status_code == 404:
            aqt.mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"The deck <b>{deck_config.name}</b> does not exist on the AnkiHub website. "
                    f"Remove it from the subscribed decks to be able to get other deck updates.<br><br>"
                    f"deck id: <i>{ankihub_did}</i>",
                )
            )
            LOGGER.info(
                "Unable to get deck updates because the deck doesn't exist on AnkiHub.",
                ah_did=ankihub_did,
            )
            return True
        return False


ah_deck_updater = _AnkiHubDeckUpdater()


def show_tooltip_about_last_deck_updates_results() -> None:
    sync_results = ah_deck_updater.last_deck_updates_results()
    if sync_results is None:
        return

    created_nids_amount = sum([len(r.created_nids) for r in sync_results])
    updated_nids_amount = sum([len(r.updated_nids) for r in sync_results])
    total = created_nids_amount + updated_nids_amount

    if total == 0:
        tooltip("AnkiHub: No new updates", parent=aqt.mw)
    else:
        tooltip(
            f"AnkiHub: Updated {total} note{'' if total == 1 else 's'}.",
            parent=aqt.mw,
        )


def _update_deck_updates_download_progress_cb(
    notes_count: int, ankihub_did: uuid.UUID
) -> None:
    aqt.mw.taskman.run_on_main(
        lambda: _update_deck_updates_download_progress_cb_inner(
            notes_count=notes_count, ankihub_did=ankihub_did
        )
    )


def _update_deck_updates_download_progress_cb_inner(
    notes_count: int, ankihub_did: uuid.UUID
) -> None:
    try:
        aqt.mw.progress.update(
            "Downloading updates\n"
            f"for {config.deck_config(ankihub_did).name}\n"
            f"Notes downloaded: {notes_count}"
        )
    except AttributeError:
        # There were sentry reports of this error:
        # AttributeError: 'NoneType' object has no attribute 'form'
        # See https://sentry.io/organizations/ankihub/issues/3655573546
        # It seems that this happens when the progress dialog window is closed.
        # It could be that this happens when AnkiHub syncs during an add-on update
        # (which also uses the progress dialog).
        # aqt.mw.progress.finish (which will be called when the sync is finished)
        # does not cause this error, because it checks if the window is open before
        # calling its methods (at least in Anki 2.1.54).
        # It should be safe to ignore this error and let the sync continue.

        LOGGER.warning(
            "Unable to update progress bar to show download progress of deck updates."
        )
    except RuntimeError as e:
        if "wrapped C/C++ object of type" in str(e) and "has been deleted" in str(e):
            return
        else:
            raise e


def _update_extension_download_progress_cb(
    note_customizations_count: int, deck_extension_id: int
):
    aqt.mw.taskman.run_on_main(
        lambda: _update_extension_download_progress_cb_inner(
            note_customizations_count, deck_extension_id
        )
    )


def _update_extension_download_progress_cb_inner(
    note_customizations_count: int, deck_extension_id: int
):
    try:
        aqt.mw.progress.update(
            "Downloading extension updates\n"
            f"for {config.deck_extension_config(deck_extension_id).name}\n"
            f"Note customizations downloaded: {note_customizations_count}"
        )
    except AttributeError:
        LOGGER.warning(
            "Unable to update progress bar to show download progress of deck updates."
        )
