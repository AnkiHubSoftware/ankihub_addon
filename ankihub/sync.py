import dataclasses
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
            )
            config.save_latest_update(ankihub_did, latest_update)
        else:
            LOGGER.debug(f"No new updates to sync for {ankihub_did=}")

        return True

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

        total = sync.importer.num_notes_created + sync.importer.num_notes_updated
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
