from concurrent.futures import Future
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Callable, List, Optional
from uuid import UUID

import aqt
import aqt.sync
from anki.collection import OpChangesWithCount
from anki.hooks import wrap
from anki.sync import SyncOutput, SyncStatus
from aqt.qt import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    Qt,
    QTextEdit,
    QVBoxLayout,
    qconnect,
)
from aqt.sync import get_sync_status

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import API_VERSION, Deck
from ...gui.operations import AddonQueryOp
from ...main.deck_unsubscribtion import uninstall_deck
from ...main.exceptions import ChangesRequireFullSyncError
from ...main.review_data import send_daily_review_summaries, send_review_data
from ...main.utils import collection_schema
from ...settings import config, get_end_cutoff_date_for_sending_review_summaries
from ..deck_updater import ah_deck_updater, show_tooltip_about_last_deck_updates_results
from ..exceptions import FullSyncCancelled
from ..utils import CollapsibleSection, logged_into_ankiweb, sync_with_ankiweb
from .db_check import maybe_check_databases
from .new_deck_subscriptions import check_and_install_new_deck_subscriptions
from .utils import future_with_exception, future_with_result, pass_exceptions_to_on_done


@dataclass
class _SyncState:
    schema_before_new_decks_installation: Optional[int] = None


sync_state = _SyncState()


class ChangesRequireFullSyncDialog(QDialog):
    def __init__(
        self,
        changes_require_full_sync_error: ChangesRequireFullSyncError,
        parent,
    ):
        super().__init__(parent)
        self.setWindowTitle(" ")
        self.setMinimumWidth(400)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(8)

        # Title inside the dialog
        title_label = QLabel("<h3>Some changes require a full sync</h3>")
        title_label.setWordWrap(True)
        main_layout.addWidget(title_label)
        main_layout.addSpacing(20)

        # Collapsible Note Type Updates Section, with a maximum expanded height.
        collapsible = CollapsibleSection("Note type updates", expanded_max_height=160)
        collapsible.toggle_button.setStyleSheet(
            collapsible.toggle_button.styleSheet() + "QToolButton { color: gray; }"
        )

        # Layout for the collapsible content
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(5, 5, 5, 5)
        content_layout.setSpacing(5)

        self.note_updates_text = QTextEdit()
        self.note_updates_text.setText(
            "\n".join(
                aqt.mw.col.models.get(mid)["name"]
                for mid in changes_require_full_sync_error.affected_note_type_ids
            )
        )
        self.note_updates_text.setReadOnly(True)
        self.note_updates_text.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        content_layout.addWidget(self.note_updates_text)

        collapsible.setContentLayout(content_layout)
        main_layout.addWidget(collapsible)
        main_layout.addSpacing(20)

        # Warning label
        warning_label = QLabel(
            "⚠️ <b>Prevent data loss:</b> make sure all your devices are synced with AnkiWeb before proceeding."
        )
        warning_label.setWordWrap(True)
        main_layout.addWidget(warning_label)
        main_layout.addSpacing(10)

        # Checkbox to enable the full sync button
        self.synced_checkbox = QCheckBox("I have synced my devices")
        main_layout.addWidget(self.synced_checkbox)
        main_layout.addSpacing(20)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(separator)
        main_layout.addSpacing(20)

        # Mobile instructions label
        mobile_instructions = QLabel(
            "👉 <b>On mobile</b>, after full sync, select the appropriate option when prompted:"
            "<ul>"
            "<li><b>iOS</b>: “Download from AnkiWeb”</li>"
            "<li><b>Android</b>: “AnkiWeb” or “Keep AnkiWeb collection”<br></li>"
            "</ul>"
        )
        mobile_instructions.setWordWrap(True)
        main_layout.addWidget(mobile_instructions)

        main_layout.addStretch()

        # Buttons layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        skip_button = QPushButton("Skip for now")
        run_full_sync_button = QPushButton("Run Full Sync")
        run_full_sync_button.setEnabled(False)

        skip_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        run_full_sync_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        button_layout.addWidget(skip_button)
        button_layout.addWidget(run_full_sync_button)
        main_layout.addLayout(button_layout)

        # Enable/disable the Run Full Sync button based on checkbox state.
        qconnect(
            self.synced_checkbox.checkStateChanged,
            lambda state: run_full_sync_button.setEnabled(
                state == Qt.CheckState.Checked
            ),
        )

        qconnect(skip_button.clicked, self.reject)
        qconnect(run_full_sync_button.clicked, self.accept)

        self.setLayout(main_layout)


@pass_exceptions_to_on_done
def sync_with_ankihub(on_done: Callable[[Future], None]) -> None:
    """Uninstall decks the user is not subscribed to anymore, check for (and maybe install) new deck subscriptions,
    then download updates to decks.

    If a full AnkiWeb sync is already required, sync with AnkiWeb first.
    """
    LOGGER.info("Syncing with AnkiHub...")
    config.log_private_config()

    @pass_exceptions_to_on_done
    def on_sync_status(out: SyncStatus, on_done: Callable[[Future], None]) -> None:
        if out.required == out.FULL_SYNC:
            LOGGER.info("Full sync required. Syncing with AnkiWeb first.")
            sync_with_ankiweb(partial(_after_ankiweb_sync, on_done=on_done))
        else:
            LOGGER.info("No full sync required. Syncing with AnkiHub directly.")
            _sync_with_ankihub_inner(on_done=on_done)

    get_sync_status(aqt.mw, partial(on_sync_status, on_done=on_done))


@pass_exceptions_to_on_done
def _after_ankiweb_sync(on_done: Callable[[Future], None]) -> None:
    @pass_exceptions_to_on_done
    def on_sync_status(out: SyncStatus, on_done: Callable[[Future], None]) -> None:
        if out.required == out.FULL_SYNC:
            # Stop here if user cancelled full sync
            LOGGER.info("AnkiWeb full sync cancelled.")
            raise FullSyncCancelled()

        _sync_with_ankihub_inner(on_done=on_done)

    get_sync_status(aqt.mw, partial(on_sync_status, on_done=on_done))


@pass_exceptions_to_on_done
def _sync_with_ankihub_inner(on_done: Callable[[Future], None]) -> None:
    client = AnkiHubClient()
    subscribed_decks = client.get_deck_subscriptions()

    _uninstall_decks_the_user_is_not_longer_subscribed_to(
        subscribed_decks=subscribed_decks
    )

    sync_state.schema_before_new_decks_installation = collection_schema()
    check_and_install_new_deck_subscriptions(
        subscribed_decks=subscribed_decks,
        on_done=partial(
            _on_new_deck_subscriptions_done,
            subscribed_decks=subscribed_decks,
            on_done=on_done,
        ),
    )


@pass_exceptions_to_on_done
def _on_new_deck_subscriptions_done(
    future: Future, on_done: Callable[[Future], None], subscribed_decks: List[Deck]
) -> None:
    future.result()

    if sync_state.schema_before_new_decks_installation != collection_schema():
        config.set_schema_to_do_full_upload_for_once(collection_schema())
        LOGGER.info("Full upload required after installation of decks.")
    else:
        config.set_schema_to_do_full_upload_for_once(None)
        LOGGER.info("No full upload required after installation of decks.")

    installed_ah_dids = config.deck_ids()
    subscribed_ah_dids = [deck.ah_did for deck in subscribed_decks]
    to_sync_ah_dids = set(installed_ah_dids).intersection(set(subscribed_ah_dids))

    update_decks_and_media(
        on_done=on_done, ah_dids=list(to_sync_ah_dids), start_media_sync=True
    )


@pass_exceptions_to_on_done
def update_decks_and_media(
    on_done: Callable[[Future], None], ah_dids: List[UUID], start_media_sync: bool
) -> None:
    def run_update(
        raise_if_full_sync_required: bool, do_full_upload: bool, start_media_sync: bool
    ) -> None:
        AddonQueryOp(
            op=lambda _: ah_deck_updater.update_decks_and_media(
                ah_dids,
                raise_if_full_sync_required=raise_if_full_sync_required,
                start_media_sync=start_media_sync,
            ),
            success=lambda _: on_success(do_full_upload=do_full_upload),
            parent=aqt.mw,
        ).failure(on_failure).with_progress().run_in_background()

    def on_failure(exception: Exception) -> None:
        if not isinstance(exception, ChangesRequireFullSyncError):
            on_done(future_with_exception(exception))
            return

        LOGGER.info(
            "Changes require full sync with AnkiWeb.",
            ah_dids=ah_dids,
            changes=exception.affected_note_type_ids,
        )

        dialog = ChangesRequireFullSyncDialog(
            changes_require_full_sync_error=exception, parent=aqt.mw
        )

        qconnect(dialog.rejected, lambda: _on_sync_done(on_done=on_done))
        qconnect(
            dialog.accepted,
            # Retry the update, this time without raising if a full sync will be required,
            # then do a full upload to AnkiWeb.
            lambda: run_update(
                raise_if_full_sync_required=False,
                do_full_upload=True,
                # The initial attempt already started media sync if needed.
                start_media_sync=False,
            ),
        )
        dialog.open()

    def on_success(do_full_upload: bool) -> None:
        if do_full_upload:
            config.set_schema_to_do_full_upload_for_once(collection_schema())

        _on_sync_done(on_done=on_done)

    if logged_into_ankiweb():
        run_update(
            raise_if_full_sync_required=True,
            do_full_upload=False,
            start_media_sync=start_media_sync,
        )
    else:
        run_update(
            raise_if_full_sync_required=False,
            do_full_upload=False,
            start_media_sync=start_media_sync,
        )


@pass_exceptions_to_on_done
def _on_sync_done(on_done: Callable[[Future], None]) -> None:
    config.set_api_version_on_last_sync(API_VERSION)

    show_tooltip_about_last_deck_updates_results()
    maybe_check_databases()

    aqt.mw.taskman.run_in_background(
        aqt.mw.col.tags.clear_unused_tags, on_done=_on_clear_unused_tags_done
    )

    aqt.mw.taskman.run_in_background(
        send_review_data, on_done=_on_send_review_data_done
    )

    _maybe_send_daily_review_summaries()

    if config.schema_to_do_full_upload_for_once():
        # Sync with AnkiWeb to resolve the pending full upload immediately.
        # Otherwise, Anki's Sync button will be red, and clicking it will trigger a full upload.
        sync_with_ankiweb(on_done=partial(on_done, future=future_with_result(None)))
    else:
        on_done(future_with_result(None))

    LOGGER.info("Sync with AnkiHub done.")
    config.log_private_config()


def _on_clear_unused_tags_done(future: Future) -> None:
    changes: OpChangesWithCount = future.result()
    LOGGER.info("Cleared unused tags.", deleted_tags_amount=changes.count)


def _on_send_review_data_done(future: Future) -> None:
    exception = future.exception()
    if not exception:
        LOGGER.info("Review data sent successfully")
        return

    # CollectionNotOpen is raised by Anki when trying to access the collection when it is closed.
    # This happens e.g. when the sync is triggered by the user closing Anki. Then the task for sending review data
    # starts and tries to access the collection, but it is already closed. We can ignore this error.
    if "CollectionNotOpen" in str(exception):  # pragma: no cover
        LOGGER.warning(
            "Failed to send review data because the collection is closed.",
            exc_info=exception,
        )
    else:
        LOGGER.error(  # pragma: no cover
            "Failed to send review data.", exc_info=exception
        )


def _maybe_send_daily_review_summaries() -> None:
    last_sent_summary_date = config.get_last_sent_summary_date()
    if not last_sent_summary_date:
        last_sent_summary_date = (
            get_end_cutoff_date_for_sending_review_summaries() - timedelta(days=1)
        )

    feature_flags = config.get_feature_flags()
    if feature_flags.get("daily_card_review_summary", False) and (
        last_sent_summary_date < get_end_cutoff_date_for_sending_review_summaries()
    ):
        aqt.mw.taskman.run_in_background(
            lambda: send_daily_review_summaries(last_sent_summary_date),
            on_done=_on_send_daily_review_summaries_done,
        )


def _on_send_daily_review_summaries_done(future: Future) -> None:
    exception = future.exception()
    if not exception:
        config.save_last_sent_summary_date(
            get_end_cutoff_date_for_sending_review_summaries()
        )

        LOGGER.info("Daily review summaries sent successfully")
        return

    # CollectionNotOpen is raised by Anki when trying to access the collection when it is closed.
    # This happens e.g. when the sync is triggered by the user closing Anki. Then the task for sending review data
    # starts and tries to access the collection, but it is already closed. We can ignore this error.
    if "CollectionNotOpen" in str(exception):  # pragma: no cover
        LOGGER.warning(
            "Failed to send review summaries because the collection is closed.",
            exc_info=exception,
        )
    else:
        LOGGER.error(  # pragma: no cover
            "Failed to send review summaries data.", exc_info=exception
        )


def _uninstall_decks_the_user_is_not_longer_subscribed_to(
    subscribed_decks: List[Deck],
) -> None:
    installed_ah_dids = config.deck_ids()
    subscribed_ah_dids = [deck.ah_did for deck in subscribed_decks]
    to_uninstall = set(installed_ah_dids).difference(subscribed_ah_dids)
    for ah_did in to_uninstall:
        uninstall_deck(ah_did)


def _upload_if_full_sync_triggered_by_ankihub(
    mw: aqt.main.AnkiQt,
    out: SyncOutput,
    on_done: Callable[[], None],
    _old: Callable[[aqt.main.AnkiQt, SyncOutput, Callable[[], None]], None],
) -> None:
    if config.schema_to_do_full_upload_for_once() == collection_schema():
        LOGGER.info(
            "Full sync triggered by AnkiHub. Uploading changes.",
            collection_schema=collection_schema(),
        )
        if hasattr(out, "server_media_usn"):
            server_usn = out.server_media_usn if mw.pm.media_syncing_enabled() else None
            aqt.sync.full_upload(mw, server_usn, on_done)
        else:
            aqt.sync.full_upload(mw, on_done)  # type: ignore
        config.set_schema_to_do_full_upload_for_once(None)
    else:
        _old(mw, out, on_done)


def setup_full_sync_patch() -> None:
    aqt.sync.full_sync = wrap(  # type: ignore
        aqt.sync.full_sync,
        _upload_if_full_sync_triggered_by_ankihub,
        "around",
    )
