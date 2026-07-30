import hashlib
import json
import os
import uuid
from concurrent.futures import Future
from datetime import datetime
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set, Tuple

import aqt
from anki.errors import NotFoundError
from anki.models import NotetypeId
from anki.notes import NoteId
from aqt.gui_hooks import theme_did_change, top_toolbar_did_redraw
from aqt.qt import (
    QAction,
    QDialog,
    QHBoxLayout,
    QIcon,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSize,
    QSizePolicy,
    Qt,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    qconnect,
)

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient
from ..ankihub_client.models import DeckMedia
from ..common_utils import get_media_names_from_note_field, get_media_names_from_note_type
from ..db import ankihub_db
from ..settings import config, get_anki_profile_id
from .operations import AddonQueryOp
from .utils import error_icon, media_download_icon, media_sync_error_svg, media_sync_svg, media_upload_icon

SHOW_MEDIA_PROGRESS_PYCMD = "ankihub_show_media_progress"
TOOLBAR_BUTTON_ID = "ankihub_media_sync"


class MediaSyncStatus(Enum):
    DOWNLOAD = "Downloading..."
    UPLOAD = "Uploading..."
    ERROR = "Error"
    CANCELING = "Canceling..."
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
        self._canceling = False
        # Used to store the Anki profile ID when the media download is started.
        # If the Anki profile changes during the media download, the download is aborted.
        self._anki_profile_id_at_download_start: Optional[str] = None
        self._errors: list[Exception] = []
        # Used for retry
        self._last_op_callback: Optional[Callable] = None

    def setup_hooks(self) -> None:
        top_toolbar_did_redraw.append(lambda _: self.refresh_sync_status(False))
        theme_did_change.append(lambda: self.refresh_sync_status(False))
        self._toolbar_link = aqt.mw.toolbar.create_link(
            SHOW_MEDIA_PROGRESS_PYCMD, "", self._on_toolbar_button_clicked, tip="", id=TOOLBAR_BUTTON_ID
        )

    def set_status_action(self, status_action: QAction):
        """Set the QAction that should be used to show the status of the media sync."""
        self._status_action = status_action
        qconnect(status_action.triggered, self._on_status_action_triggered)

    def start_media_download(self, is_retry: bool = False):
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

        self.allow_background_threads()
        self._download_in_progress = True
        self._errors = []
        self._anki_profile_id_at_download_start = get_anki_profile_id()
        self.refresh_sync_status(is_retry)
        self._last_op_callback = lambda: self.start_media_download(is_retry=True)

        def on_failure(exception: Exception) -> None:
            self._download_in_progress = False
            self._errors = [exception]
            self._canceling = False
            self.refresh_sync_status(is_retry)
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
        is_retry: bool = False,
    ):
        """Upload the referenced media files to AnkiHub in the background."""
        LOGGER.info("Starting media upload...")

        media_names = list(media_names)
        self.allow_background_threads()
        self._amount_uploads_in_progress += 1
        self._errors = []
        self.refresh_sync_status(is_retry)
        self._last_op_callback = lambda: self.start_media_upload(media_names, ankihub_did, on_success, is_retry=True)

        media_paths = self._media_paths_for_media_names(media_names)
        self._dialog.reset_progress(len(media_paths))

        def on_failure(exception: Exception) -> None:
            self._amount_uploads_in_progress -= 1
            self._errors = [exception]
            self._canceling = False
            self.refresh_sync_status(is_retry)
            raise exception

        AddonQueryOp(
            parent=aqt.mw,
            op=lambda _: self._client.upload_media(media_paths, ankihub_did, self._on_media_chunk_uploaded),
            success=lambda _: self._on_upload_finished(ankihub_deck_id=ankihub_did, on_success=on_success),
        ).failure(on_failure).without_collection().run_in_background()

    def _on_media_chunk_uploaded(self, future: Future) -> None:
        try:
            count = future.result()
            aqt.mw.taskman.run_on_main(lambda: self._dialog.update_progress(count))
        except Exception as exc:
            self._errors.append(exc)

    def stop_background_threads(self):
        """Stop all media sync operations."""
        self._client.stop_background_threads()
        self._stop_background_threads = True
        self._canceling = True
        self.refresh_sync_status(False)

    def allow_background_threads(self):
        """Allow background media sync operations to be started after they have been stopped."""
        self._client.allow_background_threads()
        self._stop_background_threads = False
        self._download_in_progress = False
        self._amount_uploads_in_progress = 0
        self._errors = []
        self._canceling = False
        self.refresh_sync_status(False)

    def close_for_profile(self):
        self.stop_background_threads()
        self._dialog.reset_progress(0)

    def refresh_sync_status(self, is_retry: bool):
        """Refresh the status text on the status action and the toolbar button."""
        # GUI operations must be performed on the main thread.
        aqt.mw.taskman.run_on_main(lambda: self._refresh_media_download_status_inner(is_retry))

    @cached_property
    def _client(self) -> AddonAnkiHubClient:
        # The client can't be initialized in __init__ because the add-on config is not set up yet at that point.
        return AddonAnkiHubClient()

    @cached_property
    def _dialog(self) -> "MediaSyncProgressDialog":
        return MediaSyncProgressDialog()

    def _media_paths_for_media_names(self, media_names: Iterable[str]) -> Set[Path]:
        media_dir_path = Path(aqt.mw.col.media.dir())
        return {media_dir_path / media_name for media_name in media_names}

    def _on_upload_finished(
        self,
        ankihub_deck_id: uuid.UUID,
        on_success: Optional[Callable[[], None]] = None,
    ):
        self._amount_uploads_in_progress -= 1
        self._canceling = False
        LOGGER.info("Uploaded media to AnkiHub.")
        self.refresh_sync_status(False)

        if on_success is not None:
            on_success()
        self._client.media_upload_finished(ankihub_deck_id)

    def _update_deck_media_and_download_missing_media(self) -> None:
        all_missing: List[Tuple[uuid.UUID, List[str]]] = []
        for ah_did in config.deck_ids():
            self._update_deck_media(ankihub_did=ah_did)
            all_missing.append((ah_did, self._missing_media_for_ah_deck(ah_did)))

        aqt.mw.taskman.run_on_main(lambda: self._dialog.reset_progress(sum(len(m[1]) for m in all_missing)))

        for ah_did, missing_media_names in all_missing:
            if not missing_media_names:
                LOGGER.info("No missing media for deck.", ah_did=ah_did)
                continue

            LOGGER.info(
                "Downloading media for deck...",
                ah_did=ah_did,
                missing_media_count=len(missing_media_names),
            )
            self._client.download_media(missing_media_names, ah_did, self._on_downloaded_file)

    def _on_downloaded_file(self, future: Future) -> None:
        try:
            future.result()
        except Exception as exc:
            self._errors.append(exc)
        aqt.mw.taskman.run_on_main(lambda: self._dialog.update_progress(1))

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
        self._canceling = False
        self.refresh_sync_status(False)

    def _get_status(self) -> MediaSyncStatus:
        status: MediaSyncStatus
        if self._canceling:
            status = MediaSyncStatus.CANCELING
        elif self._download_in_progress:
            status = MediaSyncStatus.DOWNLOAD
        elif self._amount_uploads_in_progress > 0:
            status = MediaSyncStatus.UPLOAD
        elif self._errors:
            status = MediaSyncStatus.ERROR
        else:
            status = MediaSyncStatus.IDLE
        return status

    def _refresh_media_download_status_inner(self, is_retry: bool):
        status = self._get_status()
        print("_refresh_media_download_status_inner", status)
        self._set_status_text(status)
        self._set_toolbar_button_status(status)
        self._dialog.update_status(self._get_status(), is_retry=is_retry)

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
        self._dialog.show()

    def _on_status_action_triggered(self) -> None:
        if self._get_status() != MediaSyncStatus.IDLE:
            self._dialog.show()


class FixedDialogLayout(QVBoxLayout):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def sizeHint(self) -> QSize:
        return QSize(500, super().sizeHint().height())


class MediaSyncProgressDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        self.setWindowTitle("AnkiHub - Media sync progress")

        vbox = FixedDialogLayout()
        self.setLayout(vbox)

        hbox1 = QHBoxLayout()

        self.icon_label = icon_label = QLabel()
        hbox1.addWidget(icon_label)
        self.status_label = status_label = QLabel()
        hbox1.addWidget(status_label)
        hbox1.addStretch(1)
        self.count_label = count_label = QLabel()
        hbox1.addWidget(count_label)
        vbox.addLayout(hbox1)

        self.progress_bar = progress_bar = QProgressBar()
        progress_bar.setTextVisible(False)
        vbox.addWidget(progress_bar)

        self.error_label = error_label = QLabel()
        vbox.addWidget(error_label)

        hbox3 = QHBoxLayout()
        hbox3.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.toggle_log_button = toggle_log_button = QPushButton("Show log")
        qconnect(toggle_log_button.clicked, self._on_toggle_log)
        self.cancel_button = cancel_button = QPushButton("Cancel")
        qconnect(cancel_button.clicked, self._on_cancel)
        self.minimize_button = minimize_button = QPushButton("Minimize")
        qconnect(minimize_button.clicked, self.hide)
        self.close_button = close_button = QPushButton("Close")
        qconnect(close_button.clicked, self.hide)
        self.retry_button = retry_button = QPushButton("Retry")
        qconnect(retry_button.clicked, self._on_retry)
        hbox3.addWidget(toggle_log_button)
        hbox3.addWidget(close_button)
        hbox3.addWidget(retry_button)
        hbox3.addWidget(cancel_button)
        hbox3.addWidget(minimize_button)
        vbox.addLayout(hbox3)

        self.error_log_browser = error_log_browser = QTextBrowser()
        self.error_log_area = error_log_area = QScrollArea()
        error_log_area.setWidgetResizable(True)
        error_log_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        error_log_area.setWidget(error_log_browser)
        self.error_log_spacer = error_log_spacer = QWidget()
        error_log_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vbox.addWidget(error_log_area)
        vbox.addWidget(error_log_spacer)
        self._on_toggle_log()

    def _on_cancel(self) -> None:
        media_sync.stop_background_threads()

    def _on_toggle_log(self) -> None:
        if self.error_log_area.isHidden():
            self.toggle_log_button.setText("Hide log")
            self.error_log_area.show()
            self.error_log_spacer.hide()
        else:
            self.toggle_log_button.setText("Show log")
            self.error_log_area.hide()
            self.error_log_spacer.show()

    def _on_retry(self) -> None:
        assert media_sync._last_op_callback is not None
        media_sync._last_op_callback()

    def update_status(self, status: MediaSyncStatus, is_retry: bool = False) -> None:
        label: str
        icon: Optional[QIcon] = None
        toggle_log, close, retry, cancel, minimize, progress, error = False, False, False, False, False, False, False
        retry_prefix = "Retrying: " if is_retry else ""
        if status == MediaSyncStatus.DOWNLOAD:
            label = f"{retry_prefix}Downloading from AnkiHub"
            icon = media_download_icon()
            cancel = minimize = progress = True
        elif status == MediaSyncStatus.UPLOAD:
            label = f"{retry_prefix}Uploading to AnkiHub"
            icon = media_upload_icon()
            cancel = minimize = progress = True
        elif status == MediaSyncStatus.ERROR:
            label = "Error! Attention needed."
            icon = error_icon()
            close = retry = error = True
            toggle_log = len(media_sync._errors) > 1
            if toggle_log:
                error_text = "Some errors found. See full log for details."
                self.error_log_browser.setPlainText("\n".join(str(error) for error in media_sync._errors))
            else:
                error_text = str(media_sync._errors[0])
            self.error_label.setText(error_text)
        elif status == MediaSyncStatus.CANCELING:
            label = status.value
            close = True
        elif status == MediaSyncStatus.IDLE:
            label = "Finished"
            close = True

        if icon is not None:
            self.icon_label.setPixmap(icon.pixmap(16, 16))

        self.toggle_log_button.setVisible(toggle_log)
        self.close_button.setVisible(close)
        self.retry_button.setVisible(retry)
        self.cancel_button.setVisible(cancel)
        self.minimize_button.setVisible(minimize)
        self.progress_bar.setVisible(progress)
        self.error_label.setVisible(error)
        self.status_label.setText(label)

        self.error_log_area.show()
        self._on_toggle_log()

    def update_progress(self, increment: int = 0) -> None:
        self.progress_bar.setValue(self.progress_bar.value() + increment)
        self.update_count_label()

    def reset_progress(self, maximum: int) -> None:
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(maximum)
        self.update_count_label()

    def update_count_label(self) -> None:
        if self.progress_bar.maximum():
            label = f"{self.progress_bar.value()}/{self.progress_bar.maximum()} files"
        else:
            label = ""
        self.count_label.setText(label)

    def set_maximum(self, maximum: int) -> None:
        if maximum:
            self.progress_bar.setMaximum(maximum)
        else:
            self.progress_bar.setMaximum(1)
            self.progress_bar.setValue(1)
        self.update_count_label()


media_sync = _AnkiHubMediaSync()
