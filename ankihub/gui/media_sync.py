import hashlib
import json
import os
import uuid
from datetime import datetime
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set

import aqt
from anki.errors import NotFoundError
from anki.models import NotetypeId
from anki.notes import NoteId
from aqt.gui_hooks import theme_did_change, top_toolbar_did_redraw
from aqt.qt import QAction

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient
from ..ankihub_client.models import DeckMedia
from ..common_utils import get_media_names_from_note_field, get_media_names_from_note_type
from ..db import ankihub_db
from ..settings import config, get_anki_profile_id
from .operations import AddonQueryOp
from .utils import media_sync_error_svg, media_sync_svg

SHOW_MEDIA_PROGRESS_PYCMD = "ankihub_show_media_progress"
TOOLBAR_BUTTON_ID = "ankihub_media_sync"


class MediaSyncStatus(Enum):
    DOWNLOAD = "Downloading..."
    UPLOAD = "Uploading..."
    ERROR = "Error"
    IDLE = "Idle"


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
        self._failed = False

    def setup_hooks(self) -> None:
        top_toolbar_did_redraw.append(lambda _: self.refresh_sync_status_text())
        theme_did_change.append(self.refresh_sync_status_text)
        self._toolbar_link = aqt.mw.toolbar.create_link(
            SHOW_MEDIA_PROGRESS_PYCMD, "", self._on_toolbar_button_clicked, tip="", id=TOOLBAR_BUTTON_ID
        )

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
        self._failed = False
        self._anki_profile_id_at_download_start = get_anki_profile_id()
        self.refresh_sync_status_text()

        def on_failure(exception: Exception) -> None:
            self._download_in_progress = False
            self._failed = True
            self.refresh_sync_status_text()
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
        self._failed = False
        self.refresh_sync_status_text()

        media_paths = self._media_paths_for_media_names(media_names)

        def on_failure(exception: Exception) -> None:
            self._amount_uploads_in_progress -= 1
            raise exception

        AddonQueryOp(
            parent=aqt.mw,
            op=lambda _: self._client.upload_media(media_paths, ankihub_did),
            success=lambda _: self._on_upload_finished(ankihub_deck_id=ankihub_did, on_success=on_success),
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
            if not self._client.download_media(missing_media_names, ah_did):
                self._failed = True

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
            latest_update = max(chunk.latest_update, latest_update) if latest_update else chunk.latest_update

            if self._stop_background_threads:
                LOGGER.info("Background threads stopped, aborting download of deck media objects...")
                return

            if self._anki_profile_id_at_download_start != get_anki_profile_id():
                LOGGER.info("Anki profile changed during media download, aborting download of deck media objects...")
                return

        if media_list:
            ankihub_db.upsert_deck_media_infos(ankihub_did=ankihub_did, media_list=media_list)
            config.save_latest_deck_media_update(ankihub_did, latest_media_update=latest_update)
        else:
            LOGGER.info("No new media updates for deck.", ah_did=ankihub_did)

    def _media_referenced_by_notes(self, ah_did: uuid.UUID) -> Set[str]:
        """Scan all notes in the AnkiHub deck and return the set of referenced media filenames."""
        anki_nids: List[NoteId] = ankihub_db.anki_nids_for_ankihub_deck(ah_did)

        media_names: Set[str] = set()
        note_type_ids: Set[int] = set()
        for nid in anki_nids:
            try:
                note = aqt.mw.col.get_note(nid)
            except NotFoundError:
                continue
            note_type_ids.add(note.mid)
            note_type = note.note_type()
            for field in note.values():
                media_names.update(get_media_names_from_note_field(field, note_type))
        for note_type_id in note_type_ids:
            note_type = ankihub_db.note_type_dict(NotetypeId(note_type_id))
            # Guard against notes converted to non-AnkiHub note types
            if note_type:
                media_names.update(get_media_names_from_note_type(note_type))
        return media_names

    def _missing_media_for_ah_deck(self, ah_did: uuid.UUID) -> List[str]:
        media_list = ankihub_db.downloadable_media_for_ankihub_deck(ah_did)
        if not media_list:
            return []

        referenced_media = self._media_referenced_by_notes(ah_did)
        # Filter to only media that is both downloadable AND referenced by notes
        media_list = [m for m in media_list if m.name in referenced_media]

        media_dir_path = Path(aqt.mw.col.media.dir())
        result = [
            media.name
            for media in media_list
            if not (media_dir_path / media.name).exists()
            or media.file_content_hash != hashlib.md5((media_dir_path / media.name).read_bytes()).hexdigest()
        ]
        return result

    def _on_download_finished(self, _: None) -> None:
        self._download_in_progress = False
        self.refresh_sync_status_text()

    def _refresh_media_download_status_inner(self):
        status: MediaSyncStatus
        if self._download_in_progress:
            status = MediaSyncStatus.DOWNLOAD
        elif self._amount_uploads_in_progress > 0:
            status = MediaSyncStatus.UPLOAD
        elif self._failed:
            status = MediaSyncStatus.ERROR
        else:
            status = MediaSyncStatus.IDLE

        self._set_status_text(status)
        self._set_toolbar_button_status(status)

    def _set_status_text(self, status: MediaSyncStatus):
        if self._status_action is None:
            return

        try:
            self._status_action.setText(f"🔃️ Media sync: {status.value}")
        except RuntimeError:
            LOGGER.warning("Could not set text of media sync status action because the object was deleted.")

    def _set_toolbar_button_status(self, status: MediaSyncStatus) -> None:
        elem_js = f"document.getElementById({json.dumps(TOOLBAR_BUTTON_ID)})"
        icon = media_sync_error_svg() if status == MediaSyncStatus.ERROR else media_sync_svg()
        if status == MediaSyncStatus.IDLE:
            js = """(() => {
                const toolbarButton = %(elem_js)s;
                if(toolbarButton) {
                    toolbarButton.remove()
                }
            })();""" % dict(elem_js=elem_js)
        else:
            js = """(() => {
                var toolbarButton = %(elem_js)s;
                if(toolbarButton) {
                    toolbarButton.remove();
                }
                document.querySelector(".toolbar").insertAdjacentHTML("beforeend", %(toolbar_link)s);
                toolbarButton = %(elem_js)s;
                toolbarButton.title = %(title)s;
                toolbarButton.innerHTML = %(icon)s;
                toolbarButton.style.verticalAlign = "middle";
            })();""" % dict(
                elem_js=elem_js,
                toolbar_link=json.dumps(self._toolbar_link),
                title=json.dumps(f"Media sync: {status.value}"),
                icon=json.dumps(icon),
            )
        aqt.mw.toolbar.web.eval(js)

    def _on_toolbar_button_clicked(self) -> None:
        print("todo: show media sync progress")


media_sync = _AnkiHubMediaSync()
