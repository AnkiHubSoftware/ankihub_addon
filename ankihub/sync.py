import uuid
from concurrent.futures import Future
from datetime import datetime
from time import sleep
from typing import Callable, Optional

from aqt import mw
from aqt.utils import showInfo, tooltip

from . import LOGGER, settings
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubRequestError
from .importing import AnkiHubImporter
from .settings import ANKI_MINOR, ANKIHUB_DATETIME_FORMAT_STR, config
from .utils import create_backup
from .db import ankihub_db

from anki.errors import NotFoundError


class AnkiHubSync:
    def __init__(self):
        self.importer = AnkiHubImporter()

    def sync_all_decks(self) -> None:
        LOGGER.debug("Trying to sync with AnkiHub.")

        create_backup()

        for ah_did in config.deck_ids():
            try:
                should_continue = self._sync_deck(ah_did)
                if not should_continue:
                    return
            except AnkiHubRequestError as e:
                if self._handle_exception(e, ah_did):
                    return
                else:
                    raise e

    def _sync_deck(self, ankihub_did: uuid.UUID) -> bool:
        """Syncs a single deck with AnkiHub.
        Returns True if the sync was successful, False if the user cancelled it."""
        success = self._download_note_updates(ankihub_did)
        if not success:
            return False

        self._add_optional_content_to_notes(ankihub_did)
        return True

    def _download_note_updates(self, ankihub_did) -> bool:
        """Downloads note updates from AnkiHub and imports them into Anki.
        Returns True if the sync was successful, False if the user cancelled it."""

        def download_progress_cb(notes_count: int):
            mw.taskman.run_on_main(
                lambda: mw.progress.update(
                    "Downloading updates\n"
                    f"for {config.deck_config(ankihub_did).name}\n"
                    f"Notes downloaded: {notes_count}"
                )
            )

        client = AnkiHubClient()
        notes_data = []
        latest_update: Optional[datetime] = None
        deck_config = config.deck_config(ankihub_did)
        for chunk in client.get_deck_updates(
            ankihub_did,
            since=datetime.strptime(
                deck_config.latest_update, ANKIHUB_DATETIME_FORMAT_STR
            )
            if deck_config.latest_update
            else None,
            download_progress_cb=download_progress_cb,
        ):
            if mw.progress.want_cancel():
                LOGGER.debug("User cancelled sync.")
                return False

            if not chunk.notes:
                continue

            notes_data += chunk.notes

            # each chunk contains the latest update timestamp of the notes in it, we need the latest one
            latest_update = max(
                chunk.latest_update, latest_update or chunk.latest_update
            )

        if notes_data:
            self.importer.import_ankihub_deck(
                ankihub_did=ankihub_did,
                notes_data=notes_data,
                deck_name=deck_config.name,
                local_did=deck_config.anki_id,
                protected_fields=chunk.protected_fields,
                protected_tags=chunk.protected_tags,
                subdecks=deck_config.subdecks_enabled,
            )
            config.save_latest_update(ankihub_did, latest_update)
        else:
            LOGGER.debug(f"No new updates to sync for {ankihub_did=}")
        return True

    def _add_optional_content_to_notes(self, ankihub_did: uuid.UUID):
        client = AnkiHubClient()
        result = client.get_deck_extensions_by_deck_id(ankihub_did)
        extensions = result.get("deck_extensions", [])

        for extension in extensions:
            updated_notes = []
            for chunk in client.get_note_customizations_by_deck_extension_id(
                extension.get("id")
            ):
                customizations = chunk.get("note_customizations", [])
                for customization in customizations:
                    note_anki_id = ankihub_db.anki_nid_for_ankihub_nid(
                        customization.get("note")
                    )
                    try:
                        note = mw.col.get_note(note_anki_id)
                        updated_notes.append(note)
                    except NotFoundError:
                        LOGGER.warning(
                            f"""Tried to apply customization #{customization.id}
                            for note #{customization.get('note')} but note was not found"""
                        )
                        continue
                    else:
                        note.tags = list(
                            set(note.tags) | set(customization.get("tags", []))
                        )

            mw.col.update_notes(updated_notes)

    def _handle_exception(
        self, exc: AnkiHubRequestError, ankihub_did: uuid.UUID
    ) -> bool:
        # returns True if the exception was handled

        if "/updates" not in exc.response.url:
            return False

        deck_config = config.deck_config(ankihub_did)

        if exc.response.status_code == 403:
            url_view_deck = f"{settings.URL_VIEW_DECK}{ankihub_did}"
            mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"Please subscribe to the deck <br><b>{deck_config.name}</b><br>on the AnkiHub website to "
                    "be able to sync.<br><br>"
                    f'Link to the deck: <a href="{url_view_deck}">{url_view_deck}</a><br><br>'
                    f"Note that you also need an active AnkiHub subscription.",
                )
            )
            LOGGER.debug(
                "Unable to sync because of user not being subscribed to a deck."
            )
            return True
        elif exc.response.status_code == 404:
            mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"The deck <b>{deck_config.name}</b> does not exist on the AnkiHub website. "
                    f"Remove it from the subscribed decks to be able to sync.<br><br>"
                    f"deck id: <i>{ankihub_did}</i>",
                )
            )
            LOGGER.debug("Unable to sync because the deck doesn't exist on AnkiHub.")
            return True
        return False


def sync_with_progress(on_done: Optional[Callable[[], None]] = None) -> None:

    sync = AnkiHubSync()

    def sync_with_ankihub_after_delay():

        # sync_with_ankihub creates a backup before syncing and creating a backup requires to close
        # the collection in Anki versions lower than 2.1.50.
        # When other add-ons try to access the collection while it is closed they will get an error.
        # Many add-ons are added to the profile_did_open hook so we can wait until they will probably finish
        # and sync then.
        # Another way to deal with that is to tell users to set the sync_on_startup option to false and
        # to sync manually.
        if ANKI_MINOR < 50:
            sleep(3)

        sync.sync_all_decks()

    def on_syncing_done(future: Future):
        if exc := future.exception():
            LOGGER.debug("Unable to sync.")
            raise exc

        total = len(sync.importer.created_nids) + len(sync.importer.updated_nids)
        if total == 0:
            tooltip("AnkiHub: No new updates")
        else:
            tooltip(
                f"AnkiHub: Synced {total} note{'' if total == 1 else 's'}.",
                parent=mw,
            )
        mw.reset()

        if on_done is not None:
            on_done()

    if config.token():
        mw.taskman.with_progress(
            sync_with_ankihub_after_delay,
            label="Synchronizing with AnkiHub",
            on_done=on_syncing_done,
            parent=mw,
            immediate=True,
        )
    else:
        LOGGER.debug("Skipping sync due to no token.")
