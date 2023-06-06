"""Downloads updates to decks from AnkiHub and imports them."""
import uuid
from datetime import datetime
from typing import List, Optional

import aqt
from anki.errors import NotFoundError
from aqt.utils import showInfo, tooltip

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubHTTPError, DeckExtension
from .db import ankihub_db
from .importing import AnkiHubImporter, AnkiHubImportResult
from .media_sync import media_sync
from .settings import config
from .utils import create_backup


class NotLoggedInError(Exception):
    pass


class _AnkiHubSync:
    def __init__(self):
        self._importer = AnkiHubImporter()
        self._import_results: Optional[List[AnkiHubImportResult]] = None

    def sync_all_decks_and_media(
        self, start_media_sync: bool = True
    ) -> List[AnkiHubImportResult]:
        """Syncs all decks with AnkiHub and starts the media download.
        Should be called from a background thread with a progress dialog to avoid blocking the UI.
        Returns the results of the sync."""
        LOGGER.info("Syncing all decks and media...")

        self._import_results = None

        if not config.is_logged_in():
            raise NotLoggedInError()

        self._import_results = []
        self._sync_all_decks()

        # The media sync should be started after the deck updates are imported,
        # because the import can add new media references to notes.
        if start_media_sync:
            media_sync.start_media_download()

        LOGGER.info("Sync finished.")
        return self._import_results

    def last_sync_results(self) -> Optional[List[AnkiHubImportResult]]:
        """Returns the results of the last sync. Returns None if no sync has been performed yet or
        if the last sync failed."""
        return self._import_results

    def _sync_all_decks(self) -> None:
        LOGGER.info("Syncing decks...")

        create_backup()

        client = AnkiHubClient()
        if client.is_feature_flag_enabled("new_subscription_workflow_enabled"):
            with_user_relation_ah_dids = [
                deck.ankihub_deck_uuid for deck in client.get_decks_with_user_relation()
            ]
            installed_ah_dids = config.deck_ids()
            to_sync_ah_dids = list(
                set(with_user_relation_ah_dids).intersection(installed_ah_dids)
            )
        else:
            to_sync_ah_dids = config.deck_ids()

        for ah_did in to_sync_ah_dids:
            try:
                should_continue = self._sync_deck(ah_did)
                if not should_continue:
                    return
            except AnkiHubHTTPError as e:
                if self._handle_exception(e, ah_did):
                    return
                else:
                    raise e

    def _sync_deck(self, ankihub_did: uuid.UUID) -> bool:
        """Syncs a single deck with AnkiHub.
        Returns True if the sync was successful, False if the user cancelled it."""
        result = self._download_updates_for_deck(ankihub_did)
        if not result:
            return False

        result = self._sync_deck_extensions(ankihub_did)
        return result

    def _download_updates_for_deck(self, ankihub_did) -> bool:
        """Downloads note updates from AnkiHub and imports them into Anki.
        Returns True if the sync was successful, False if the user cancelled it."""

        client = AnkiHubClient()
        notes_data = []
        latest_update: Optional[datetime] = None
        deck_config = config.deck_config(ankihub_did)
        for chunk in client.get_deck_updates(
            ankihub_did,
            since=deck_config.latest_update,
            download_progress_cb=lambda notes_count: _update_deck_download_progress_cb(
                notes_count, ankihub_did=ankihub_did
            ),
        ):
            if aqt.mw.progress.want_cancel():
                LOGGER.info("User cancelled sync.")
                return False

            if not chunk.notes:
                continue

            notes_data += chunk.notes

            # each chunk contains the latest update timestamp of the notes in it, we need the latest one
            latest_update = max(
                chunk.latest_update, latest_update or chunk.latest_update
            )

        if notes_data:
            import_result = self._importer.import_ankihub_deck(
                ankihub_did=ankihub_did,
                notes_data=notes_data,
                deck_name=deck_config.name,
                local_did=deck_config.anki_id,
                protected_fields=chunk.protected_fields,
                protected_tags=chunk.protected_tags,
                subdecks=deck_config.subdecks_enabled,
            )
            self._import_results.append(import_result)

            config.save_latest_deck_update(ankihub_did, latest_update)
        else:
            LOGGER.info(f"No new updates to sync for {ankihub_did=}")
        return True

    def _sync_deck_extensions(self, ankihub_did: uuid.UUID) -> bool:
        # returns True if the sync was successful, False if the user cancelled it
        client = AnkiHubClient()
        if not (deck_extensions := client.get_deck_extensions_by_deck_id(ankihub_did)):
            LOGGER.info(f"No extensions to sync for {ankihub_did=}")
            return True

        for deck_extension in deck_extensions:
            if not self._download_updates_for_extension(deck_extension):
                return False

        return True

    def _download_updates_for_extension(self, deck_extension: DeckExtension) -> bool:
        # returns True if the sync was successful, False if the user cancelled it
        config.create_or_update_deck_extension_config(deck_extension)
        deck_extension_config = config.deck_extension_config(deck_extension.id)
        latest_update: Optional[datetime] = None
        updated_notes = []
        client = AnkiHubClient()
        for chunk in client.get_deck_extension_updates(
            deck_extension_id=deck_extension.id,
            since=deck_extension_config.latest_update,
            download_progress_cb=lambda note_customizations_count: _update_extension_download_progress_cb(
                note_customizations_count, deck_extension.id
            ),
        ):
            if not chunk.note_customizations:
                continue

            if aqt.mw.progress.want_cancel():
                LOGGER.debug("User cancelled sync.")
                return False

            for customization in chunk.note_customizations:
                anki_nid = ankihub_db.anki_nid_for_ankihub_nid(
                    customization.ankihub_nid
                )
                try:
                    note = aqt.mw.col.get_note(anki_nid)
                except NotFoundError:
                    LOGGER.warning(
                        f"Tried to apply customization to note {customization.ankihub_nid} but note was not found"
                    )
                    continue
                else:
                    note.tags = list(set(note.tags) | set(customization.tags or []))
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

        if exc.response.status_code == 404:
            aqt.mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"The deck <b>{deck_config.name}</b> does not exist on the AnkiHub website. "
                    f"Remove it from the subscribed decks to be able to sync.<br><br>"
                    f"deck id: <i>{ankihub_did}</i>",
                )
            )
            LOGGER.info("Unable to sync because the deck doesn't exist on AnkiHub.")
            return True
        return False


ah_sync = _AnkiHubSync()


def show_tooltip_about_last_sync_results() -> None:
    sync_results = ah_sync.last_sync_results()
    if sync_results is None:
        return

    created_nids_amount = sum([len(r.created_nids) for r in sync_results])
    updated_nids_amount = sum([len(r.updated_nids) for r in sync_results])
    total = created_nids_amount + updated_nids_amount

    if total == 0:
        tooltip("AnkiHub: No new updates")
    else:
        tooltip(
            f"AnkiHub: Synced {total} note{'' if total == 1 else 's'}.",
            parent=aqt.mw,
        )


def _update_deck_download_progress_cb(notes_count: int, ankihub_did: uuid.UUID):
    aqt.mw.taskman.run_on_main(
        lambda: _update_deck_download_progress_cb_inner(
            notes_count=notes_count, ankihub_did=ankihub_did
        )
    )


def _update_deck_download_progress_cb_inner(notes_count: int, ankihub_did: uuid.UUID):
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
