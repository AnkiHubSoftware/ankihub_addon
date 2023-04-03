import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import aqt
from aqt.qt import QAction

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient
from .db import ankihub_db
from .media_utils import get_img_names_from_notes


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

    def set_status_action(self, status_action: QAction):
        """Set the QAction that should be used to show the status of the media sync."""
        self._status_action = status_action

    def start_media_download(self):
        """Download missing media for all subscribed decks from AnkiHub in the background.
        Does nothing if a download is already in progress.
        """
        if self._download_in_progress:
            LOGGER.info("Media download already in progress, skipping...")
            return

        LOGGER.info("Starting media download...")

        self._download_in_progress = True
        self.refresh_sync_status_text()

        aqt.mw.taskman.run_in_background(
            self._download_missing_media, on_done=self._on_download_finished
        )

    def start_media_upload(
        self,
        media_names: Iterable[str],
        ankihub_did: uuid.UUID,
        on_success: Optional[Callable[[], None]] = None,
    ):
        """Upload the referenced media files to AnkiHub in the background."""
        self._amount_uploads_in_progress += 1
        self.refresh_sync_status_text()

        media_paths = list(self._media_paths_for_media_names(media_names))
        aqt.mw.taskman.run_in_background(
            lambda: AddonAnkiHubClient().upload_assets(media_paths, ankihub_did),
            on_done=lambda future: self._on_upload_finished(
                future, on_success=on_success
            ),
        )

    def _media_paths_for_media_names(
        self, media_names: Iterable[str]
    ) -> Iterable[Path]:
        media_dir_path = Path(aqt.mw.col.media.dir())
        return {media_dir_path / media_name for media_name in media_names}

    def _on_upload_finished(
        self, future: Future, on_success: Optional[Callable[[], None]] = None
    ):
        self._amount_uploads_in_progress -= 1
        future.result()
        LOGGER.info("Uploaded images to AnkiHub.")
        self.refresh_sync_status_text()

        if on_success is not None:
            on_success()

    def _download_missing_media(self):
        client = AddonAnkiHubClient()
        for ah_did in ankihub_db.ankihub_deck_ids():
            asset_disabled_fields = client.get_asset_disabled_fields(ah_did)
            missing_image_names = self._missing_images_for_ah_deck(
                ah_did, asset_disabled_fields
            )
            if not missing_image_names:
                continue
            AddonAnkiHubClient().download_images(missing_image_names, ah_did)

    def _missing_images_for_ah_deck(
        self, ah_did: uuid.UUID, asset_disabled_fields: Dict[int, List[str]]
    ) -> List[str]:
        nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
        notes_data = [ankihub_db.note_data(nid) for nid in nids]
        img_names = get_img_names_from_notes(
            notes_data, asset_disabled_fields=asset_disabled_fields
        )
        media_dir_path = Path(aqt.mw.col.media.dir())

        result = [
            img_name
            for img_name in img_names
            if not (media_dir_path / img_name).exists()
        ]
        return result

    def _on_download_finished(self, future: Future) -> None:
        self._download_in_progress = False
        future.result()
        LOGGER.info("Downloaded images from AnkiHub.")
        self.refresh_sync_status_text()

    def refresh_sync_status_text(self):
        """Refresh the status text on the status action."""
        # GUI operations must be performed on the main thread.
        aqt.mw.taskman.run_on_main(self._refresh_media_download_status_inner)

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

        self._status_action.setText(f"🔃️ Media Sync: {text}")


media_sync = _AnkiHubMediaSync()
