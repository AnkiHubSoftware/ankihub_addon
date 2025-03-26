import os
import uuid
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set

import aqt
from aqt.qt import QAction

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient
from ..ankihub_client.models import DeckMedia
from ..db import ankihub_db
from ..settings import config, get_anki_profile_id
from .operations import AddonQueryOp


class _AnkiHubMediaSync:
    """This class is responsible for synchronizing media between Anki and AnkiHub.
    The operations are performed in the background.
    This class keeps track of the status of the operations and shows it as text on
    the QAction that is passed to the setup method.
    """

    def __init__(self) -> None:
        self._download_in_progress = False
        self._amount_uploads_in_progress = 0
        self._status_action: Optional[QAction] = None
        self._stop_background_threads = False
        # Used to store the Anki profile ID when the media download is started.
        # If the Anki profile changes during the media download, the download is aborted.
        self._anki_profile_id_at_download_start: Optional[str] = None

    def set_status_action(self, status_action: QAction):
        """Set the QAction that should be used to show the status of the media sync."""
        self._status_action = status_action

    def start_media_download(self):
        """Download missing media for all subscribed decks from AnkiHub in the background.
        Does nothing if a download is already in progress.
        """
        if os.getenv("DISABLE_MEDIA_DOWNLOAD", None) == "1":
            LOGGER.info("Media download disabled, skipping...")
            return

        if self._download_in_progress:
            LOGGER.info("Media download already in progress, skipping...")
            return

        LOGGER.info("Starting media download...")

        self._download_in_progress = True
        self._anki_profile_id_at_download_start = get_anki_profile_id()
        self.refresh_sync_status_text()

        def on_failure(exception: Exception) -> None:
            self._download_in_progress = False
            raise exception

        AddonQueryOp(
            parent=aqt.mw,
            op=lambda _: self._update_deck_media_and_download_missing_media(),
            success=self._on_download_finished,
        ).failure(on_failure).without_collection().run_in_background()

    def start_media_upload(
        self,
        media_names: Iterable[str],
        ankihub_did: uuid.UUID,
        on_success: Optional[Callable[[], None]] = None,
    ):
        """Upload the referenced media files to AnkiHub in the background."""
        LOGGER.info("Starting media upload...")

        self._amount_uploads_in_progress += 1
        self.refresh_sync_status_text()

        media_paths = self._media_paths_for_media_names(media_names)

        def on_failure(exception: Exception) -> None:
            self._amount_uploads_in_progress -= 1
            raise exception

        AddonQueryOp(
            parent=aqt.mw,
            op=lambda _: self._client.upload_media(media_paths, ankihub_did),
            success=lambda _: self._on_upload_finished(
                ankihub_deck_id=ankihub_did, on_success=on_success
            ),
        ).failure(on_failure).without_collection().run_in_background()

    def stop_background_threads(self):
        """Stop all media sync operations."""
        self._client.stop_background_threads()
        self._stop_background_threads = True

    def allow_background_threads(self):
        """Allow background media sync operations to be started after they have been stopped."""
        self._client.allow_background_threads()
        self._stop_background_threads = False

    def refresh_sync_status_text(self):
        """Refresh the status text on the status action."""
        # GUI operations must be performed on the main thread.
        aqt.mw.taskman.run_on_main(self._refresh_media_download_status_inner)

    @cached_property
    def _client(self) -> AddonAnkiHubClient:
        # The client can't be initialized in __init__ because the add-on config is not set up yet at that point.
        return AddonAnkiHubClient()

    def _media_paths_for_media_names(self, media_names: Iterable[str]) -> Set[Path]:
        media_dir_path = Path(aqt.mw.col.media.dir())
        return {media_dir_path / media_name for media_name in media_names}

    def _on_upload_finished(
        self,
        ankihub_deck_id: uuid.UUID,
        on_success: Optional[Callable[[], None]] = None,
    ):
        self._amount_uploads_in_progress -= 1
        LOGGER.info("Uploaded media to AnkiHub.")
        self.refresh_sync_status_text()

        if on_success is not None:
            on_success()
        self._client.media_upload_finished(ankihub_deck_id)

    def _update_deck_media_and_download_missing_media(self) -> None:
        for ah_did in config.deck_ids():
            self._update_deck_media(ankihub_did=ah_did)
            missing_media_names = self._missing_media_for_ah_deck(ah_did)
            if not missing_media_names:
                LOGGER.info("No missing media for deck.", ah_did=ah_did)
                continue

            LOGGER.info(
                "Downloading media for deck...",
                ah_did=ah_did,
                missing_media_count=len(missing_media_names),
            )
            self._client.download_media(missing_media_names, ah_did)

    def _update_deck_media(self, ankihub_did: uuid.UUID) -> None:
        """Fetch deck media updates from AnkiHub and update the database and the config.

        If the deck configuration for the provided AnkiHub deck ID is not found (i.e., is None),
        the function logs a warning and returns early without making any updates.
        """
        deck_config = config.deck_config(ankihub_did)
        if deck_config is None:  # pragma: no cover
            # This can happen if the deck gets deleted or the user switches the Anki
            # profile during the media sync.
            LOGGER.warning("No deck config for deck.", ah_did=ankihub_did)
            return

        media_list: List[DeckMedia] = []
        latest_update: Optional[datetime] = None
        for chunk in self._client.get_deck_media_updates(
            ankihub_did,
            since=deck_config.latest_media_update,
        ):
            if not chunk.media:
                continue

            media_list += chunk.media
            latest_update = (
                max(chunk.latest_update, latest_update)
                if latest_update
                else chunk.latest_update
            )

            if self._stop_background_threads:
                LOGGER.info(
                    "Background threads stopped, aborting download of deck media objects..."
                )
                return

            if self._anki_profile_id_at_download_start != get_anki_profile_id():
                LOGGER.info(
                    "Anki profile changed during media download, aborting download of deck media objects..."
                )
                return

        if media_list:
            ankihub_db.upsert_deck_media_infos(
                ankihub_did=ankihub_did, media_list=media_list
            )
            config.save_latest_deck_media_update(
                ankihub_did, latest_media_update=latest_update
            )
        else:
            LOGGER.info("No new media updates for deck.", ah_did=ankihub_did)

    def _missing_media_for_ah_deck(self, ah_did: uuid.UUID) -> List[str]:
        media_names = ankihub_db.downloadable_media_names_for_ankihub_deck(ah_did)
        media_dir_path = Path(aqt.mw.col.media.dir())

        result = [
            media_name
            for media_name in media_names
            if not (media_dir_path / media_name).exists()
        ]
        return result

    def _on_download_finished(self, _: None) -> None:
        self._download_in_progress = False
        self.refresh_sync_status_text()

    def _refresh_media_download_status_inner(self):
        if self._download_in_progress:
            self._set_status_text("Downloading...")
        elif self._amount_uploads_in_progress > 0:
            self._set_status_text("Uploading...")
        else:
            self._set_status_text("Idle")

    def _set_status_text(self, text: str):
        if self._status_action is None:
            return

        try:
            self._status_action.setText(f"🔃️ Media Sync: {text}")
        except RuntimeError:
            LOGGER.warning(
                "Could not set text of media sync status action because the object was deleted."
            )


media_sync = _AnkiHubMediaSync()
