import uuid
from concurrent.futures import Future
from datetime import datetime
from time import sleep
from typing import Dict, Optional

from aqt import gui_hooks, mw
from aqt.utils import showInfo, tooltip

from . import LOGGER, settings
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubRequestError, SuggestionType
from .importing import AnkiHubImporter
from .settings import ANKI_MINOR, ANKIHUB_DATETIME_FORMAT_STR, config
from .utils import create_backup

ADDON_INTERNAL_TAG_PREFIX = "AnkiHub_"

TAG_FOR_PROTECTING_FIELDS = f"{ADDON_INTERNAL_TAG_PREFIX}Protect"
TAG_FOR_PROTECTING_ALL_FIELDS = f"{TAG_FOR_PROTECTING_FIELDS}::All"

TAG_FOR_UPDATES = f"{ADDON_INTERNAL_TAG_PREFIX}Update"
TAG_FOR_NEW_NOTE = f"{TAG_FOR_UPDATES}::New_Note"
TAG_FOR_SUGGESTION_TYPE = {
    SuggestionType.UPDATED_CONTENT: f"{TAG_FOR_UPDATES}::Content::Updated",
    SuggestionType.NEW_CONTENT: f"{TAG_FOR_UPDATES}::Content::New",
    SuggestionType.CONTENT_ERROR: f"{TAG_FOR_UPDATES}::Content::Error",
    SuggestionType.SPELLING_GRAMMATICAL: f"{TAG_FOR_UPDATES}::Spelling/Grammar",
    SuggestionType.NEW_TAGS: f"{TAG_FOR_UPDATES}::New_tags",
    SuggestionType.UPDATED_TAGS: f"{TAG_FOR_UPDATES}::Updated_tags",
    SuggestionType.NEW_CARD_TO_ADD: f"{TAG_FOR_UPDATES}::New_Card",
    SuggestionType.OTHER: f"{TAG_FOR_UPDATES}::Other",
}

# top-level tags that are only used by the add-on, but not by the web app
ADDON_INTERNAL_TAGS = [
    TAG_FOR_PROTECTING_FIELDS,
    TAG_FOR_UPDATES,
]

# tags that are used internally by Anki and should not be deleted or appear in suggestions
ANKI_INTERNAL_TAGS = ["leech", "marked"]


def is_internal_tag(tag: str) -> bool:
    return any(
        tag == internal_tag or tag.startswith(f"{internal_tag}::")
        for internal_tag in [*ADDON_INTERNAL_TAGS]
    ) or any(tag == internal_tag for internal_tag in ANKI_INTERNAL_TAGS)


class AnkiHubSync:
    def __init__(self):
        self.importer = AnkiHubImporter()

    def sync_all_decks(self) -> None:
        LOGGER.debug("Trying to sync with AnkiHub.")

        create_backup()

        for ankihub_did, deck_info in config.private_config.decks.items():
            try:
                should_continue = self._sync_deck(ankihub_did)
                if not should_continue:
                    return
            except AnkiHubRequestError as e:
                if self._handle_exception(e, ankihub_did, deck_info):
                    return
                else:
                    raise e

    def _sync_deck(self, ankihub_did: str) -> bool:
        deck = config.private_config.decks[ankihub_did]

        def download_progress_cb(notes_count: int):
            mw.taskman.run_on_main(
                lambda: mw.progress.update(
                    "Downloading updates\n"
                    f"for {deck['name']}\n"
                    f"Notes downloaded: {notes_count}"
                )
            )

        client = AnkiHubClient()
        notes_data = []
        latest_update: Optional[datetime] = None
        for chunk in client.get_deck_updates(
            uuid.UUID(ankihub_did),
            since=datetime.strptime(deck["latest_update"], ANKIHUB_DATETIME_FORMAT_STR)
            if deck["latest_update"]
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
                deck_name=deck["name"],
                local_did=deck["anki_id"],
                protected_fields=chunk.protected_fields,
                protected_tags=chunk.protected_tags,
            )
            config.save_latest_update(ankihub_did, latest_update)
        else:
            LOGGER.debug(f"No new updates to sync for {ankihub_did=}")

        return True

    def _handle_exception(
        self, exc: AnkiHubRequestError, ankihub_did: str, deck_info: Dict
    ) -> bool:
        # returns True if the exception was handled

        if "/updates" not in exc.response.url:
            return False

        if exc.response.status_code == 403:
            url_view_deck = f"{settings.URL_VIEW_DECK}{ankihub_did}"
            mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"Please subscribe to the deck <br><b>{deck_info['name']}</b><br>on the AnkiHub website to "
                    "be able to sync.<br><br>"
                    f'Link to the deck: <a href="{url_view_deck}">{url_view_deck}</a><br><br>'
                    f"Note that you also need an active AnkiHub membership.",
                )
            )
            LOGGER.debug(
                "Unable to sync because of user not being subscribed to a deck."
            )
            return True
        elif exc.response.status_code == 404:
            mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"The deck <b>{deck_info['name']}</b> does not exist on the AnkiHub website. "
                    f"Remove it from the subscribed decks to be able to sync.<br><br>"
                    f"deck id: <i>{ankihub_did}</i>",
                )
            )
            LOGGER.debug("Unable to sync because the deck doesn't exist on AnkiHub.")
            return True
        return False


def sync_with_progress() -> None:

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

    def on_done(future: Future):
        if exc := future.exception():
            LOGGER.debug("Unable to sync.")
            raise exc
        else:
            total = sync.importer.num_notes_created + sync.importer.num_notes_updated
            if total == 0:
                tooltip("AnkiHub: No new updates")
            else:
                tooltip(
                    f"AnkiHub: Synced {total} note{'' if total == 1 else 's'}.",
                    parent=mw,
                )
            mw.reset()

    if config.private_config.token:
        mw.taskman.with_progress(
            lambda: sync_with_ankihub_after_delay(),
            label="Synchronizing with AnkiHub",
            on_done=on_done,
            parent=mw,
            immediate=True,
        )
    else:
        LOGGER.debug("Skipping sync due to no token.")


def setup_sync_on_startup() -> None:
    def on_profile_open():
        # syncing with AnkiHub during sync with AnkiWeb causes an error,
        # this is why we have to wait until the AnkiWeb sync is done if there is one
        if not mw.can_auto_sync():
            sync_with_progress()
        else:

            def on_sync_did_finish():
                sync_with_progress()
                gui_hooks.sync_did_finish.remove(on_sync_did_finish)

            gui_hooks.sync_did_finish.append(on_sync_did_finish)

    gui_hooks.profile_did_open.append(on_profile_open)
