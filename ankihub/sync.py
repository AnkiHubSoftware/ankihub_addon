import uuid
from concurrent.futures import Future
from datetime import datetime
from time import sleep
from typing import Callable, List, Optional

import aqt
from anki.errors import NotFoundError
from aqt.utils import showInfo, tooltip

from . import LOGGER, settings
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubRequestError, DeckExtension
from .db import ankihub_db
from .importing import AnkiHubImporter, AnkiHubImportResult
from .media_download import media_downloader
from .settings import ANKI_MINOR, config
from .utils import create_backup


class AnkiHubSync:
    def __init__(self):
        self._importer = AnkiHubImporter()
        self._import_results: List[AnkiHubImportResult] = []

    def sync_all_decks_and_media(self) -> List[AnkiHubImportResult]:
        LOGGER.info("Syncing all decks...")

        create_backup()

        for ah_did in config.deck_ids():
            try:
                should_continue = self._sync_deck(ah_did)
                if not should_continue:
                    return self._import_results
            except AnkiHubRequestError as e:
                if self._handle_exception(e, ah_did):
                    return self._import_results
                else:
                    raise e

        if AnkiHubClient().is_feature_flag_enabled("image_support_enabled"):
            media_downloader.start_media_download()

        return self._import_results

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

    def _handle_exception(
        self, exc: AnkiHubRequestError, ankihub_did: uuid.UUID
    ) -> bool:
        # returns True if the exception was handled

        if "/updates" not in exc.response.url:
            return False

        deck_config = config.deck_config(ankihub_did)

        if exc.response.status_code == 403:
            url = f"{settings.url_view_deck()}{ankihub_did}"
            aqt.mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"Please subscribe to the deck <br><b>{deck_config.name}</b><br>on the AnkiHub website to "
                    "be able to sync.<br><br>"
                    f'Link to the deck: <a href="{url}">{url}</a><br><br>'
                    f"Note that you also need an active AnkiHub subscription.",
                )
            )
            LOGGER.info(
                "Unable to sync because of user not being subscribed to a deck."
            )
            return True
        elif exc.response.status_code == 404:
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


sync = AnkiHubSync()


def sync_with_progress(
    on_done: Optional[Callable[[bool], None]] = None, parent=None
) -> None:
    # If the sync is successful this will show tooltips about the number of notes that were synced.
    # on_done is called with a boolean indicating if the sync was successful.
    # If there is an error during the sync it will be raised and on_done will not be called.

    LOGGER.info("Starting sync.")

    def sync_with_ankihub_after_delay() -> List[AnkiHubImportResult]:

        # sync_with_ankihub creates a backup before syncing and creating a backup requires to close
        # the collection in Anki versions lower than 2.1.50.
        # When other add-ons try to access the collection while it is closed they will get an error.
        # Many add-ons are added to the profile_did_open hook so we can wait until they will probably finish
        # and sync then.
        # Another way to deal with that is to tell users to set the auto_sync option to "never" and
        # to sync manually.
        if ANKI_MINOR < 50:
            sleep(3)

        result = sync.sync_all_decks_and_media()
        LOGGER.info("Sync finished.")
        return result

    def on_syncing_done(future: Future):
        try:
            import_results: List[AnkiHubImportResult] = future.result()
        except Exception as exc:
            LOGGER.info("Unable to sync.")
            raise exc

        created_nids_amount = sum([len(r.created_nids) for r in import_results])
        updated_nids_amount = sum([len(r.updated_nids) for r in import_results])
        total = created_nids_amount + updated_nids_amount

        if total == 0:
            tooltip("AnkiHub: No new updates")
        else:
            tooltip(
                f"AnkiHub: Synced {total} note{'' if total == 1 else 's'}.",
                parent=aqt.mw,
            )
        aqt.mw.reset()

        if on_done is not None:
            on_done(True)

    if config.token():
        aqt.mw.taskman.with_progress(
            sync_with_ankihub_after_delay,
            label="Synchronizing with AnkiHub",
            on_done=on_syncing_done,
            parent=aqt.mw if parent is None else parent,
            immediate=True,
        )
    else:
        LOGGER.info("Skipping sync due to no token.")
        if on_done is not None:
            on_done(False)


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

        LOGGER.exception(
            "Unable to update progress bar to show download progress of deck updates."
        )


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
        LOGGER.exception(
            "Unable to update progress bar to show download progress of deck updates."
        )
