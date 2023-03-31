import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Dict, List

import aqt

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient
from .db import ankihub_db
from .media_utils import get_img_names_from_notes


class AnkiHubMediaDownloader:
    def __init__(self) -> None:
        self._in_progress: bool = False

    def start_media_download(self):
        """Download missing media for all subscribed decks from AnkiHub in the background.
        Does nothing if a download is already in progress.
        The download status can be checked in the AnkiHub menu.
        """

        if self._in_progress:
            LOGGER.info("Media download already in progress, skipping...")
            return

        LOGGER.info("Starting media download...")
        self._in_progress = True

        # TODO Refactor this to not have to import from gui.menu here.
        from .gui.menu import media_download_status_action

        if media_download_status_action is not None:
            # The action can be None if the image support feature flag is disabled.
            media_download_status_action.setText("Media download: In progress...")

        aqt.mw.taskman.run_in_background(
            task=self._download_missing_media,
            on_done=self._on_finished,
        )

    def _download_missing_media(self):
        for ah_did in ankihub_db.ankihub_deck_ids():
            client = AddonAnkiHubClient()
            asset_disabled_fields = client.get_asset_disabled_fields(ah_did)
            missing_image_names = self._missing_images_for_ah_deck(
                ah_did, asset_disabled_fields
            )
            if not missing_image_names:
                continue
            client.download_images(missing_image_names, ah_did)

    def _missing_images_for_ah_deck(
        self, ah_did: uuid.UUID, asset_disabled_fields: Dict[int, List[str]] = {}
    ) -> List[str]:
        nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
        notes = [ankihub_db.note_data(nid) for nid in nids]
        img_names = get_img_names_from_notes(notes, asset_disabled_fields)
        media_dir_path = Path(aqt.mw.col.media.dir())

        result = [
            img_name
            for img_name in img_names
            if not (media_dir_path / img_name).exists()
        ]
        return result

    def _on_finished(self, future: Future) -> None:
        self._in_progress = False

        future.result()

        LOGGER.info("Media download finished.")

        # TODO Refactor this to not have to import from gui.menu here.
        from .gui.menu import media_download_status_action

        # TODO Refactor this so that the status is not hardcoded here.
        # Not sure yet if showing the status in the menu is a good idea.
        if media_download_status_action is not None:
            # The action can be None if the image support feature flag is disabled.
            media_download_status_action.setText("Media download: Idle.")


media_downloader = AnkiHubMediaDownloader()
