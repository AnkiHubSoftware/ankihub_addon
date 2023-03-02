import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import List

import aqt

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient
from .db import ankihub_db
from .media_utils import get_img_names_from_notes


class AnkiHubMediaDownloader:
    def __init__(self) -> None:
        self._syncing: bool = False

    def start_media_download(self):
        """Download missing media for all subscribed decks from AnkiHub in the background.
        Does nothing if a download is already in progress.
        The download status can be checked in the AnkiHub menu.
        """

        if self._syncing:
            LOGGER.info("Media download already in progress, skipping...")
            return

        LOGGER.info("Starting media download...")
        self._syncing = True

        # TODO Refactor this to not have to import from gui.menu here.
        from .gui.menu import media_download_status_action

        media_download_status_action.setText("Media download: In progress...")
        aqt.mw.taskman.run_in_background(
            task=self._download_misssing_media,
            on_done=self._on_finished,
        )

    def _download_misssing_media(self):
        for ah_did in ankihub_db.ankihub_deck_ids():
            missing_image_names = self.missing_images_for_ah_deck(ah_did)
            if not missing_image_names:
                continue
            AddonAnkiHubClient().download_images(missing_image_names, ah_did)

    def missing_images_for_ah_deck(self, ah_did: uuid.UUID) -> List[str]:
        nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
        img_names = get_img_names_from_notes(nids)

        media_dir_path = Path(aqt.mw.col.media.dir())

        result = [
            img_name
            for img_name in img_names
            if not (media_dir_path / img_name).exists()
        ]
        return result

    def _on_finished(self, future: Future) -> None:
        future.result()

        LOGGER.info("Media download finished.")

        self._syncing = False

        # TODO Refactor this to not have to import from gui.menu here.
        from .gui.menu import media_download_status_action

        # TODO Refactor this so that the status is not hardcoded here.
        # Not sure yet if showing the status in the menu is a good idea.
        media_download_status_action.setText("Media download: Idle.")


media_downloader = AnkiHubMediaDownloader()
