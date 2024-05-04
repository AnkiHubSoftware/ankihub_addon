import copy
import importlib
import json
import os
import re
import shutil
import tempfile
import uuid
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Protocol,
    Set,
    Tuple,
    Union,
    cast,
)
from unittest.mock import Mock
from zipfile import ZipFile

import aqt
import pytest
from anki.cards import Card
from anki.consts import QUEUE_TYPE_NEW, QUEUE_TYPE_SUSPENDED
from anki.decks import DeckId, FilteredDeckConfig
from anki.errors import NotFoundError
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.utils import point_version
from aqt import AnkiQt, QMenu, dialogs, gui_hooks
from aqt.addcards import AddCards
from aqt.addons import InstallOk
from aqt.browser import Browser
from aqt.browser.sidebar.item import SidebarItem
from aqt.browser.sidebar.tree import SidebarTreeView
from aqt.gui_hooks import browser_will_show_context_menu
from aqt.importing import AnkiPackageImporter
from aqt.qt import QAction, Qt, QUrl
from aqt.theme import theme_manager
from aqt.webview import AnkiWebView
from pytest import fixture
from pytest_anki import AnkiSession
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot  # type: ignore
from requests import Response  # type: ignore
from requests_mock import Mocker

from ankihub.ankihub_client.models import (
    DeckMediaUpdateChunk,
    DeckUpdates,
    NotesAction,
    NotesActionChoices,
    UserDeckExtensionRelation,
)
from ankihub.gui import editor
from ankihub.gui.browser.browser import (
    ModifiedAfterSyncSearchNode,
    NewNoteSearchNode,
    SuggestionTypeSearchNode,
    UpdatedInTheLastXDaysSearchNode,
    _on_protect_fields_action,
    _on_reset_optional_tags_action,
)
from ankihub.main.deck_options import ANKIHUB_PRESET_NAME

from ..factories import (
    DeckExtensionFactory,
    DeckFactory,
    DeckMediaFactory,
    NoteInfoFactory,
)
from ..fixtures import (
    AddAnkiNote,
    ImportAHNote,
    ImportAHNoteType,
    InstallAHDeck,
    LatestInstanceTracker,
    MockDownloadAndInstallDeckDependencies,
    MockShowDialogWithCB,
    MockStudyDeckDialogWithCB,
    MockSuggestionDialog,
    SetFeatureFlagState,
    add_basic_anki_note_to_deck,
    create_or_get_ah_version_of_note_type,
    record_review,
    record_review_for_anki_nid,
)
from .conftest import TEST_PROFILE_ID

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub import entry_point, settings
from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ankihub.ankihub_client import (
    API_VERSION,
    AnkiHubHTTPError,
    ChangeNoteSuggestion,
    Deck,
    Field,
    NewNoteSuggestion,
    NoteCustomization,
    NoteInfo,
    OptionalTagSuggestion,
    SuggestionType,
    TagGroupValidationResponse,
    UserDeckRelation,
)
from ankihub.ankihub_client.ankihub_client import (
    ANKIHUB_DATETIME_FORMAT_STR,
    DEFAULT_API_URL,
    DeckExtensionUpdateChunk,
    _transform_notes_data,
)
from ankihub.common_utils import local_media_names_from_html
from ankihub.db import ankihub_db
from ankihub.db.models import AnkiHubNote
from ankihub.debug import (
    _log_stack,
    _setup_logging_for_db_begin,
    _setup_logging_for_sync_collection_and_media,
)
from ankihub.gui import utils
from ankihub.gui.auto_sync import (
    SYNC_RATE_LIMIT_SECONDS,
    _setup_ankihub_sync_on_ankiweb_sync,
)
from ankihub.gui.browser import custom_columns
from ankihub.gui.browser import setup as setup_browser
from ankihub.gui.browser.custom_search_nodes import (
    AnkiHubNoteSearchNode,
    UpdatedSinceLastReviewSearchNode,
)
from ankihub.gui.config_dialog import (
    get_config_dialog_manager,
    setup_config_dialog_manager,
)
from ankihub.gui.deck_updater import _AnkiHubDeckUpdater, ah_deck_updater
from ankihub.gui.deckbrowser import (
    FLASHCARD_SELECTOR_OPEN_BUTTON_ID,
    FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD,
    FlashCardSelectorDialog,
)
from ankihub.gui.decks_dialog import DeckManagementDialog
from ankihub.gui.editor import SUGGESTION_BTN_ID
from ankihub.gui.errors import upload_logs_and_data_in_background
from ankihub.gui.exceptions import DeckDownloadAndInstallError, RemoteDeckNotFoundError
from ankihub.gui.media_sync import media_sync
from ankihub.gui.menu import AnkiHubLogin, menu_state
from ankihub.gui.operations import ankihub_sync
from ankihub.gui.operations.db_check import ah_db_check
from ankihub.gui.operations.db_check.ah_db_check import check_ankihub_db
from ankihub.gui.operations.deck_installation import download_and_install_decks
from ankihub.gui.operations.new_deck_subscriptions import (
    check_and_install_new_deck_subscriptions,
)
from ankihub.gui.operations.utils import future_with_result
from ankihub.gui.optional_tag_suggestion_dialog import OptionalTagsSuggestionDialog
from ankihub.gui.suggestion_dialog import SuggestionDialog
from ankihub.main.deck_creation import create_ankihub_deck, modify_note_type
from ankihub.main.deck_unsubscribtion import uninstall_deck
from ankihub.main.exporting import to_note_data
from ankihub.main.importing import (
    AnkiHubImporter,
    AnkiHubImportResult,
    _adjust_note_types_in_anki_db,
    change_note_types_of_notes,
)
from ankihub.main.note_conversion import (
    ADDON_INTERNAL_TAGS,
    ANKI_INTERNAL_TAGS,
    TAG_FOR_OPTIONAL_TAGS,
    TAG_FOR_PROTECTING_ALL_FIELDS,
    TAG_FOR_PROTECTING_FIELDS,
)
from ankihub.main.note_deletion import TAG_FOR_DELETED_NOTES
from ankihub.main.reset_local_changes import reset_local_changes_to_notes
from ankihub.main.subdecks import (
    SUBDECK_TAG,
    build_subdecks_and_move_cards_to_them,
    flatten_deck,
)
from ankihub.main.suggestions import (
    ANKIHUB_NO_CHANGE_ERROR,
    ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR,
    BulkNoteSuggestionsResult,
    suggest_new_note,
    suggest_note_update,
    suggest_notes_in_bulk,
)
from ankihub.main.utils import (
    ANKIHUB_TEMPLATE_SNIPPET_RE,
    all_dids,
    get_note_types_in_deck,
    md5_file_hash,
    note_type_contains_field,
)
from ankihub.settings import (
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    ANKING_DECK_ID,
    AnkiHubCommands,
    BehaviorOnRemoteNoteDeleted,
    DeckConfig,
    DeckExtension,
    DeckExtensionConfig,
    SuspendNewCardsOfExistingNotes,
    ankihub_base_path,
    config,
    profile_files_path,
    url_flashcard_selector,
)

SAMPLE_MODEL_ID = NotetypeId(1656968697414)
TEST_DATA_PATH = Path(__file__).parent.parent / "test_data"
SAMPLE_DECK_APKG = TEST_DATA_PATH / "small.apkg"
ANKIHUB_SAMPLE_DECK_APKG = TEST_DATA_PATH / "small_ankihub.apkg"
SAMPLE_NOTES_DATA = eval((TEST_DATA_PATH / "small_ankihub.txt").read_text())
SAMPLE_NOTE_TYPES: Dict[NotetypeId, NotetypeDict] = json.loads(
    (TEST_DATA_PATH / "small_ankihub_note_types.json").read_text()
)

# the package name in the manifest is "ankihub"
# the package name is used during the add-on installation process
# to determine the path to the add-on files which also determines if an existing add-on is updated
# or if a new add-on is installed
ANKIHUB_ANKIADDON_FILE = TEST_DATA_PATH / "ankihub.ankiaddon"


class InstallSampleAHDeck(Protocol):
    def __call__(self) -> Tuple[DeckId, uuid.UUID]:
        ...


@fixture
def install_sample_ah_deck(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
) -> InstallSampleAHDeck:

    ah_did = next_deterministic_uuid()

    def _install_sample_ah_deck():
        # Can only be used in an anki_session_with_addon.profile_loaded() context
        anki_did = import_sample_ankihub_deck(ankihub_did=ah_did)
        config.add_deck(
            name="Testdeck",
            ankihub_did=ah_did,
            anki_did=anki_did,
            user_relation=UserDeckRelation.SUBSCRIBER,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
        )
        return anki_did, ah_did

    return _install_sample_ah_deck


def import_sample_ankihub_deck(
    ankihub_did: uuid.UUID, assert_created_deck=True
) -> DeckId:
    # import the deck from the notes data
    dids_before_import = all_dids()
    importer = AnkiHubImporter()
    local_did = importer.import_ankihub_deck(
        ankihub_did=ankihub_did,
        notes=ankihub_sample_deck_notes_data(),
        deck_name="Testdeck",
        is_first_import_of_deck=True,
        behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
        protected_fields={},
        protected_tags=[],
        note_types=SAMPLE_NOTE_TYPES,
        suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
            ankihub_did
        ),
        suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
    ).anki_did
    new_dids = all_dids() - dids_before_import

    if assert_created_deck:
        assert len(new_dids) == 1
        assert local_did == list(new_dids)[0]

    return local_did


class CreateAnkiAHNote(Protocol):
    def __call__(
        self,
        ankihub_nid: uuid.UUID = None,
        note_type_id: Optional[NotetypeId] = None,
    ) -> Note:
        ...


@fixture
def create_anki_ah_note(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    next_deterministic_id: Callable[[], int],
) -> CreateAnkiAHNote:
    """This fixture returns a new Anki Note that has a AnkiHub note type by default and
    the fields of the note are pre-filled with deterministic values.
    If the note type has an ankihub_id field, it will be set to the given ankihub_nid.
    The note is not saved in any database.
    Can only be used in an anki_session_with_addon.profile_loaded() context.
    """

    def _make_ah_note(
        ankihub_nid: uuid.UUID = None,
        note_type_id: Optional[NotetypeId] = None,
    ) -> Note:
        mw = anki_session_with_addon_data.mw

        if ankihub_nid is None:
            ankihub_nid = next_deterministic_uuid()

        if note_type_id is None:
            note_type = create_or_get_ah_version_of_note_type(
                mw=mw, note_type=mw.col.models.by_name("Basic")
            )
        else:
            note_type = mw.col.models.get(note_type_id)
            assert note_type is not None

        note = mw.col.new_note(note_type)
        note.id = NoteId(next_deterministic_id())

        # fields of the note will be set to "old <field_name>"
        # except for the ankihub note_type field (if it exists) which will be set to the ankihub nid
        for field_cfg in note_type["flds"]:
            field_name: str = field_cfg["name"]
            note[field_name] = f"old {field_name.lower()}"

        if ANKIHUB_NOTE_TYPE_FIELD_NAME in note:
            note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_nid)

        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_nid)
        note.tags = []
        note.guid = "old guid"
        return note

    return _make_ah_note


def ankihub_sample_deck_notes_data() -> List[NoteInfo]:
    notes_data_raw = _transform_notes_data(SAMPLE_NOTES_DATA)
    result = [NoteInfo.from_dict(x) for x in notes_data_raw]
    return result


@fixture
def mock_ankihub_sync_dependencies(
    mock_client_methods_called_during_ankihub_sync: None,
    mock_fetch_note_types_to_return_empty_dict: None,
) -> None:
    # Set a fake token so that the deck update is not aborted
    config.save_token("test_token")


@fixture
def mock_fetch_note_types_to_return_empty_dict(
    mocker: MockerFixture,
) -> None:
    # This prevents the add-on from fetching the note types from the server
    mocker.patch("ankihub.main.note_types._fetch_note_types")


@pytest.fixture
def mock_client_methods_called_during_ankihub_sync(mocker: MockerFixture) -> None:
    mocker.patch.object(AnkiHubClient, "get_deck_subscriptions")
    mocker.patch.object(AnkiHubClient, "get_deck_extensions_by_deck_id")
    mocker.patch.object(AnkiHubClient, "is_media_upload_finished")
    mocker.patch.object(AnkiHubClient, "get_deck_media_updates")
    mocker.patch.object(AnkiHubClient, "send_card_review_data")
    mocker.patch.object(AnkiHubClient, "get_deck_by_id")

    deck_updates_mock = Mock()
    deck_updates_mock.notes = []
    mocker.patch.object(
        AnkiHubClient, "get_deck_updates", return_value=deck_updates_mock
    )


class MockClientGetNoteType(Protocol):
    def __call__(self, note_types: List[NotetypeDict]) -> None:
        ...


@fixture
def mock_client_get_note_type(mocker: MockerFixture) -> MockClientGetNoteType:
    """Mock the get_note_type method of the AnkiHubClient to return the matching note type
    based on the id of the note type."""

    def _mock_client_note_types(note_types: List[NotetypeDict]) -> None:
        def note_type_by_id(self, note_type_id: int) -> NotetypeDict:
            result = next(
                (
                    note_type
                    for note_type in note_types
                    if note_type["id"] == note_type_id
                ),
                None,
            )
            assert result is not None
            return result

        mocker.patch.object(AnkiHubClient, "get_note_type", side_effect=note_type_by_id)

    return _mock_client_note_types


class SyncWithAnkiHub(Protocol):
    def __call__(self) -> None:
        ...


@pytest.fixture
def sync_with_ankihub(qtbot: QtBot) -> SyncWithAnkiHub:
    """Sync with AnkiHub and wait until the sync is done."""

    def _sync_with_ankihub() -> None:
        with qtbot.wait_callback() as callback:
            ankihub_sync.sync_with_ankihub(on_done=callback)

        future: Future = callback.args[0]
        future.result()  # raises exception if there is one

    return _sync_with_ankihub


class CreateChangeSuggestion(Protocol):
    def __call__(self, note: Note, wait_for_media_upload: bool) -> Mock:
        ...


@pytest.fixture
def create_change_suggestion(
    qtbot: QtBot, mocker: MockerFixture, mock_client_media_upload: Mock
):
    """Create a change suggestion for a note and wait for the background thread that uploads media to finish.
    Returns the mock for the create_change_note_suggestion method. It can be used to get information
    about the suggestion that was passed to the client."""

    create_change_suggestion_mock = mocker.patch.object(
        AnkiHubClient,
        "create_change_note_suggestion",
    )

    def create_change_suggestion_inner(note: Note, wait_for_media_upload: bool):
        suggest_note_update(
            note=note,
            change_type=SuggestionType.NEW_CONTENT,
            comment="test",
            media_upload_cb=media_sync.start_media_upload,
        )

        if wait_for_media_upload:
            # Wait for the background thread that uploads the media to finish.
            qtbot.wait_until(lambda: mock_client_media_upload.call_count == 1)

        return create_change_suggestion_mock

    return create_change_suggestion_inner


class CreateNewNoteSuggestion(Protocol):
    def __call__(
        self, note: Note, ah_did: uuid.UUID, wait_for_media_upload: bool
    ) -> Mock:
        ...


@pytest.fixture
def create_new_note_suggestion(
    qtbot: QtBot, mocker: MockerFixture, mock_client_media_upload: Mock
):
    """Create a new note suggestion for a note and wait for the background thread that uploads media to finish.
    Returns the mock for the create_new_note_suggestion_mock method. It can be used to get information
    about the suggestion that was passed to the client."""

    create_new_note_suggestion_mock = mocker.patch.object(
        AnkiHubClient,
        "create_new_note_suggestion",
    )

    def create_new_note_suggestion_inner(
        note: Note, ah_did: uuid.UUID, wait_for_media_upload: bool
    ):
        suggest_new_note(
            note=note,
            comment="test",
            ankihub_did=ah_did,
            media_upload_cb=media_sync.start_media_upload,
        )

        if wait_for_media_upload:
            # Wait for the background thread that uploads the media to finish.
            qtbot.wait_until(lambda: mock_client_media_upload.call_count == 1)

        return create_new_note_suggestion_mock

    return create_new_note_suggestion_inner


def test_entry_point(anki_session_with_addon_data: AnkiSession, qtbot: QtBot):
    entry_point.run()
    with anki_session_with_addon_data.profile_loaded():
        qtbot.wait(1000)

    # this test is just to make sure the entry point doesn't crash
    # and that the add-on doesn't crash on Anki startup


# The JS in the webviews is flaky if not run in sequential mode
@pytest.mark.sequential
class TestEditor:
    @pytest.mark.parametrize(
        "note_fields_changed, suggest_deletion, logged_in",
        [
            (True, False, True),
            (False, False, True),
            (False, True, True),
            (True, False, False),
        ],
    )
    def test_create_change_note_suggestion(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        mock_suggestion_dialog: MockSuggestionDialog,
        qtbot: QtBot,
        note_fields_changed: bool,
        logged_in: bool,
        suggest_deletion: bool,
    ):
        editor.setup()
        with anki_session_with_addon_data.profile_loaded():
            mocker.patch.object(config, "is_logged_in", return_value=logged_in)

            mock_suggestion_dialog(
                user_cancels=False,
                suggestion_type=(
                    SuggestionType.DELETE
                    if suggest_deletion
                    else SuggestionType.UPDATED_CONTENT
                ),
            )

            create_change_note_suggestion_mock = mocker.patch.object(
                AnkiHubClient, "create_change_note_suggestion"
            )

            show_tooltip_mock = mocker.patch(
                "ankihub.gui.suggestion_dialog.show_tooltip"
            )

            ah_did = install_ah_deck()
            ah_note = import_ah_note(ah_did=ah_did)
            anki_note = aqt.mw.col.get_note(NoteId(ah_note.anki_nid))

            if note_fields_changed:
                new_field_value = "new field value"
                anki_note["Front"] = new_field_value

            add_cards_dialog: AddCards = dialogs.open("AddCards", aqt.mw)
            add_cards_dialog.editor.set_note(anki_note)

            self.wait_suggestion_button_ready(qtbot=qtbot, mocker=mocker)

            self.assert_suggestion_button_text(
                qtbot=qtbot,
                addcards=add_cards_dialog,
                expected_text=AnkiHubCommands.CHANGE.value,
            )

            self.click_suggestion_button(add_cards_dialog)

            if not logged_in:
                # Assert that the login dialog was shown
                window: AnkiHubLogin = AnkiHubLogin._window
                qtbot.wait_until(lambda: window and window.isVisible())
            elif note_fields_changed or suggest_deletion:
                # Assert that the suggestion was sent to the server with the correct data
                qtbot.wait_until(lambda: create_change_note_suggestion_mock.called)
                change_note_suggestion: ChangeNoteSuggestion = (
                    create_change_note_suggestion_mock.call_args.kwargs[
                        "change_note_suggestion"
                    ]
                )
                if suggest_deletion:
                    assert not change_note_suggestion.fields
                else:
                    assert change_note_suggestion.fields[0].value == new_field_value
            else:
                # Assert that no suggestion was sent to the server and that a tooltip was shown
                qtbot.wait_until(lambda: show_tooltip_mock.called)
                assert not create_change_note_suggestion_mock.called

            # Clear editor to prevent dialog that asks for confirmation to discard changes when closing the editor
            add_cards_dialog.editor.cleanup()

    @pytest.mark.parametrize("logged_in", [True, False])
    def test_create_new_note_suggestion(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        mock_suggestion_dialog: MockSuggestionDialog,
        install_ah_deck: InstallAHDeck,
        import_ah_note_type: ImportAHNoteType,
        qtbot: QtBot,
        logged_in: bool,
    ):
        editor.setup()
        with anki_session_with_addon_data.profile_loaded():
            mocker.patch.object(config, "is_logged_in", return_value=logged_in)
            mock_suggestion_dialog(user_cancels=False)

            create_new_note_suggestion_mock = mocker.patch.object(
                AnkiHubClient, "create_new_note_suggestion"
            )

            ah_did = install_ah_deck()
            ah_note_type = import_ah_note_type(ah_did=ah_did)

            anki_note = aqt.mw.col.new_note(ah_note_type)
            field_value = "field value"
            anki_note["Front"] = field_value

            add_cards_dialog: AddCards = dialogs.open("AddCards", aqt.mw)
            add_cards_dialog.editor.set_note(anki_note)

            self.wait_suggestion_button_ready(qtbot=qtbot, mocker=mocker)

            self.assert_suggestion_button_text(
                qtbot=qtbot,
                addcards=add_cards_dialog,
                expected_text=AnkiHubCommands.NEW.value,
            )

            self.click_suggestion_button(add_cards_dialog)

            if not logged_in:
                # Assert that the login dialog was shown
                window: AnkiHubLogin = AnkiHubLogin._window
                qtbot.wait_until(lambda: window and window.isVisible())
            else:
                # Assert that the suggestion was created with the correct data
                qtbot.wait_until(lambda: create_new_note_suggestion_mock.called)
                new_note_suggestion: NewNoteSuggestion = (
                    create_new_note_suggestion_mock.call_args.kwargs[
                        "new_note_suggestion"
                    ]
                )
                assert new_note_suggestion.fields[0].value == field_value

            # Clear editor to prevent dialog that asks for confirmation to discard changes when closing the editor
            add_cards_dialog.editor.cleanup()

    def test_suggestion_button_is_disabled_for_notes_without_ankihub_note_type(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        qtbot: QtBot,
        add_anki_note: AddAnkiNote,
    ):
        editor.setup()
        with anki_session_with_addon_data.profile_loaded():
            anki_note = add_anki_note()
            add_cards_dialog: AddCards = dialogs.open("AddCards", aqt.mw)
            add_cards_dialog.editor.set_note(anki_note)

            self.wait_suggestion_button_ready(qtbot=qtbot, mocker=mocker)

            self.assert_suggestion_button_text(
                qtbot=qtbot,
                addcards=add_cards_dialog,
                expected_text="",
            )
            self.assert_suggestion_button_enabled_status(
                qtbot=qtbot, addcards=add_cards_dialog, expected_enabled=False
            )

            # Clear editor to prevent dialog that asks for confirmation to discard changes when closing the editor
            add_cards_dialog.editor.cleanup()

    @pytest.mark.parametrize("is_deleted", [True, False])
    def test_suggestion_button_is_disabled_for_notes_deleted_from_ankihub(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        qtbot: QtBot,
        is_deleted: bool,
    ):
        editor.setup()
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            ah_note = import_ah_note(ah_did=ah_did)
            anki_note = aqt.mw.col.get_note(NoteId(ah_note.anki_nid))

            if is_deleted:
                AnkiHubNote.update(
                    last_update_type=SuggestionType.DELETE.value[0]
                ).where(AnkiHubNote.ankihub_note_id == ah_note.ah_nid).execute()

            add_cards_dialog: AddCards = dialogs.open("AddCards", aqt.mw)
            add_cards_dialog.editor.set_note(anki_note)

            self.wait_suggestion_button_ready(qtbot=qtbot, mocker=mocker)

            self.assert_suggestion_button_text(
                qtbot=qtbot,
                addcards=add_cards_dialog,
                expected_text=AnkiHubCommands.CHANGE.value,
            )
            self.assert_suggestion_button_enabled_status(
                qtbot=qtbot, addcards=add_cards_dialog, expected_enabled=not is_deleted
            )

            # Clear editor to prevent dialog that asks for confirmation to discard changes when closing the editor
            add_cards_dialog.editor.cleanup()

    def test_with_note_deleted_on_ankihub(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        qtbot: QtBot,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        mock_suggestion_dialog: MockSuggestionDialog,
    ):
        editor.setup()
        with anki_session_with_addon_data.profile_loaded():
            mocker.patch.object(config, "is_logged_in", return_value=True)

            mock_suggestion_dialog(user_cancels=False)

            # Mock the client method to raise an exception with a 404 response
            response = Response()
            response.status_code = 404
            create_change_note_suggestion_mock = mocker.patch.object(
                AnkiHubClient,
                "create_change_note_suggestion",
                side_effect=AnkiHubHTTPError(response=response),
            )

            # Setup a note with some changes to create a change suggestion
            ah_did = install_ah_deck()
            ah_note = import_ah_note(ah_did=ah_did)
            anki_note = aqt.mw.col.get_note(NoteId(ah_note.anki_nid))

            new_field_value = "new field value"
            anki_note["Front"] = new_field_value

            show_error_dialog_mock = mocker.patch(
                "ankihub.gui.suggestion_dialog.show_error_dialog"
            )

            add_cards_dialog: AddCards = dialogs.open("AddCards", aqt.mw)
            add_cards_dialog.editor.set_note(anki_note)
            self.wait_suggestion_button_ready(qtbot=qtbot, mocker=mocker)
            self.click_suggestion_button(add_cards_dialog)

            # Assert that the error dialog was shown with the correct message
            qtbot.wait_until(lambda: show_error_dialog_mock.called)
            assert (
                show_error_dialog_mock.call_args.args[0]
                == "This note has been deleted from AnkiHub. No new suggestions can be made."
            )
            create_change_note_suggestion_mock.assert_called_once()  # sanity check

            # Clear editor to prevent dialog that asks for confirmation to discard changes when closing the editor
            add_cards_dialog.editor.cleanup()

    def wait_suggestion_button_ready(self, qtbot: QtBot, mocker: MockerFixture) -> None:
        refresh_buttons_spy = mocker.spy(editor, "_refresh_buttons")
        qtbot.wait_until(lambda: refresh_buttons_spy.called)

    def assert_suggestion_button_text(
        self, qtbot: QtBot, addcards: AddCards, expected_text: str
    ) -> None:
        with qtbot.wait_callback() as callback:
            addcards.editor.web.evalWithCallback(
                f"document.getElementById('{SUGGESTION_BTN_ID}-label').textContent",
                callback,
            )
        callback.assert_called_with(expected_text)

    def assert_suggestion_button_enabled_status(
        self, qtbot: QtBot, addcards: AddCards, expected_enabled: bool
    ) -> None:
        with qtbot.wait_callback() as callback:
            addcards.editor.web.evalWithCallback(
                f"document.getElementById('{SUGGESTION_BTN_ID}').disabled",
                callback,
            )
        callback.assert_called_with(not expected_enabled)

    def click_suggestion_button(self, addcards: AddCards) -> None:
        addcards.editor.web.eval(
            f"document.getElementById('{SUGGESTION_BTN_ID}').click()"
        )


def test_get_note_types_in_deck(anki_session_with_addon_data: AnkiSession):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        with anki_session.deck_installed(SAMPLE_DECK_APKG) as deck_id:
            # test get note types in deck
            note_model_ids = get_note_types_in_deck(DeckId(deck_id))
            # TODO test on a deck that has more than one note type.
            assert len(note_model_ids) == 2
            assert note_model_ids == [1656968697414, 1656968697418]


def test_note_type_contains_field(anki_session_with_addon_data: AnkiSession):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        with anki_session.deck_installed(SAMPLE_DECK_APKG):
            note_type = anki_session.mw.col.models.get(SAMPLE_MODEL_ID)
            assert note_type_contains_field(note_type, SAMPLE_MODEL_ID) is False
            new_field = {"name": ANKIHUB_NOTE_TYPE_FIELD_NAME}
            note_type["flds"].append(new_field)
            assert note_type_contains_field(note_type, ANKIHUB_NOTE_TYPE_FIELD_NAME)
            note_type["flds"].remove(new_field)


def test_modify_note_type(anki_session_with_addon_data: AnkiSession):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        with anki_session.deck_installed(SAMPLE_DECK_APKG):
            note_type = anki_session.mw.col.models.by_name("Basic")
            original_note_type = copy.deepcopy(note_type)
            original_note_template = original_note_type["tmpls"][0]["afmt"]
            modify_note_type(note_type)
            modified_template = note_type["tmpls"][0]["afmt"]
            # # TODO Make more precise assertions.
            assert ANKIHUB_NOTE_TYPE_FIELD_NAME in modified_template
            assert original_note_template != modified_template


def test_create_collaborative_deck_and_upload(
    anki_session_with_addon_data: AnkiSession,
    mocker: MockerFixture,
    next_deterministic_uuid: Callable[[], uuid.UUID],
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        # create a new deck with one note
        deck_name = "New Deck"
        mw.col.decks.add_normal_deck_with_name(deck_name)
        anki_did = mw.col.decks.id_for_name(deck_name)

        note = mw.col.new_note(mw.col.models.by_name("Basic"))
        note["Front"] = "front"
        note["Back"] = "back"
        mw.col.add_note(note, anki_did)

        # upload deck
        ah_did = next_deterministic_uuid()
        ah_nid = next_deterministic_uuid()
        upload_deck_mock = mocker.patch.object(
            AnkiHubClient,
            "upload_deck",
            return_value=ah_did,
        )
        mocker.patch("uuid.uuid4", return_value=ah_nid)
        create_ankihub_deck(deck_name, private=False)

        # re-load note to get updated note.mid
        note.load()

        # check that the client method was called with the correct data
        expected_note_types_data = [mw.col.models.get(note.mid)]
        expected_note_data = NoteInfo(
            ah_nid=ah_nid,
            anki_nid=note.id,
            fields=[
                Field(name="Front", value="front", order=0),
                Field(name="Back", value="back", order=1),
            ],
            tags=[],
            mid=note.mid,
            guid=note.guid,
            last_update_type=None,
        )

        upload_deck_mock.assert_called_once_with(
            deck_name=deck_name,
            notes_data=[expected_note_data],
            note_types_data=expected_note_types_data,
            anki_deck_id=anki_did,
            private=False,
        )

        # check that note data is in db
        assert ankihub_db.note_data(note.id) == expected_note_data

        # check that note mod value is in database
        assert AnkiHubNote.get(AnkiHubNote.anki_note_id == note.id).mod == note.mod


class TestDownloadAndInstallDecks:
    @pytest.mark.qt_no_exception_capture
    @pytest.mark.parametrize(
        "has_subdeck_tags",
        [True, False],
    )
    def test_download_and_install_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mock_download_and_install_deck_dependencies: MockDownloadAndInstallDeckDependencies,
        ankihub_basic_note_type: NotetypeDict,
        has_subdeck_tags: bool,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            deck = DeckFactory.create()
            notes_data = [
                NoteInfoFactory.create(
                    mid=ankihub_basic_note_type["id"],
                    tags=[f"{SUBDECK_TAG}::Deck::Subdeck"] if has_subdeck_tags else [],
                )
            ]
            mocks = mock_download_and_install_deck_dependencies(
                deck, notes_data, ankihub_basic_note_type
            )

            # Download and install the deck
            with qtbot.wait_callback() as callback:
                download_and_install_decks(
                    [deck.ah_did],
                    on_done=callback,
                    behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                )

            # Assert that the deck was installed
            # ... in the Anki database
            assert deck.anki_did in [x.id for x in aqt.mw.col.decks.all_names_and_ids()]
            assert aqt.mw.col.get_note(NoteId(notes_data[0].anki_nid)) is not None

            # ... in the AnkiHub database
            ankihub_db.ankihub_deck_ids() == [deck.ah_did]
            assert ankihub_db.note_data(NoteId(notes_data[0].anki_nid)) == notes_data[0]

            # ... in the config
            assert config.deck_ids() == [deck.ah_did]

            # Assert that the mocked functions were called
            for name, mock in mocks.items():
                assert (
                    mock.call_count == 1
                ), f"Mock {name} was not called once, but {mock.call_count} times"

    def test_exception_is_not_backpropagated_to_caller(
        self, anki_session_with_addon_data: AnkiSession, mocker: MockerFixture
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Mock a function which is called in download_install_decks to raise an exception.
            exception_message = "test exception"

            mocker.patch.object(
                aqt.mw.taskman,
                "with_progress",
                side_effect=Exception(exception_message),
            )

            # Set up the on_done callback
            future: Optional[Future] = None

            def on_done(future_: Future) -> None:
                nonlocal future
                future = future_

            # Call download_and_install_decks. This shouldn't raise an exception.
            download_and_install_decks(ankihub_dids=[], on_done=on_done)

            # Assert that the future contains the exception and that it contains the expected message.
            assert future.exception().args[0] == exception_message

    @pytest.mark.parametrize(
        "response_status_code",
        [404, 500],
    )
    def test_fetching_deck_infos_raises_ankihub_http_error(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        qtbot: QtBot,
        mock_download_and_install_deck_dependencies: MockDownloadAndInstallDeckDependencies,
        ankihub_basic_note_type: NotetypeDict,
        response_status_code: int,
    ):
        with anki_session_with_addon_data.profile_loaded():

            deck = DeckFactory.create()
            notes_data = [NoteInfoFactory.create(mid=ankihub_basic_note_type["id"])]
            mock_download_and_install_deck_dependencies(
                deck, notes_data, ankihub_basic_note_type
            )

            response = Response()
            response.status_code = response_status_code
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_by_id",
                side_effect=AnkiHubHTTPError(response=response),
            )

            with qtbot.wait_callback() as callback:
                download_and_install_decks(
                    ankihub_dids=[deck.ah_did],
                    on_done=callback,
                    behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                )

            future: Future = callback.args[0]
            exception = future.exception()
            assert isinstance(exception, DeckDownloadAndInstallError)

            if response_status_code == 404:
                assert isinstance(exception.original_exception, RemoteDeckNotFoundError)
            else:
                assert isinstance(exception.original_exception, AnkiHubHTTPError)

    def test_download_and_install_single_deck_raises_exception(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        qtbot: QtBot,
        mock_download_and_install_deck_dependencies: MockDownloadAndInstallDeckDependencies,
        ankihub_basic_note_type: NotetypeDict,
    ):
        with anki_session_with_addon_data.profile_loaded():

            deck = DeckFactory.create()
            notes_data = [NoteInfoFactory.create(mid=ankihub_basic_note_type["id"])]
            mock_download_and_install_deck_dependencies(
                deck, notes_data, ankihub_basic_note_type
            )

            exception_message = "test exception"
            mocker.patch(
                "ankihub.gui.operations.deck_installation._download_and_install_single_deck",
                side_effect=Exception(exception_message),
            )

            with qtbot.wait_callback() as callback:
                download_and_install_decks(
                    ankihub_dids=[deck.ah_did],
                    on_done=callback,
                    behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                )

            future: Future = callback.args[0]
            exception = future.exception()
            assert isinstance(exception, DeckDownloadAndInstallError)
            assert exception.original_exception.args[0] == exception_message


class TestCheckAndInstallNewDeckSubscriptions:
    def test_one_new_subscription(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
        mock_show_dialog_with_cb: MockShowDialogWithCB,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            # Mock confirmation dialog
            mock_show_dialog_with_cb(
                "ankihub.gui.operations.new_deck_subscriptions.show_dialog",
                button_index=1,
            )

            # Mock download and install operation to only call the on_done callback
            download_and_install_decks_mock = mocker.patch(
                "ankihub.gui.operations.new_deck_subscriptions.download_and_install_decks",
                side_effect=lambda *args, on_done, **kwargs: on_done(
                    future_with_result(None)
                ),
            )

            # Call the function with a deck
            deck = DeckFactory.create()
            with qtbot.wait_callback() as callback:
                check_and_install_new_deck_subscriptions(
                    subscribed_decks=[deck], on_done=callback
                )

            # Assert that the on_done callback was called with a future with a result of None
            assert callback.args[0].result() is None

            # Assert that the mocked functions were called
            assert download_and_install_decks_mock.call_count == 1
            assert download_and_install_decks_mock.call_args[0][0] == [deck.ah_did]

    def test_user_declines(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mock_show_dialog_with_cb: MockShowDialogWithCB,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            # Mock confirmation dialog
            mock_show_dialog_with_cb(
                "ankihub.gui.operations.new_deck_subscriptions.show_dialog",
                button_index=0,
            )

            # Call the function with a deck
            deck = DeckFactory.create()
            with qtbot.wait_callback() as callback:
                check_and_install_new_deck_subscriptions(
                    subscribed_decks=[deck], on_done=callback
                )

            # Assert that the on_done callback was called with a future with a result of None
            assert callback.args[0].result() is None

    def test_no_new_subscriptions(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            # Call the function with an empty list
            with qtbot.wait_callback() as callback:
                check_and_install_new_deck_subscriptions(
                    subscribed_decks=[], on_done=callback
                )

            # Assert that the on_done callback was called with a future with a result of None
            assert callback.args[0].result() is None

    def test_confirmation_dialog_raises_exception(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            # Mock confirmation dialog to raise an exception

            message_box_mock = mocker.patch(
                "ankihub.gui.operations.new_deck_subscriptions.show_dialog",
                side_effect=Exception("Something went wrong"),
            )

            # Call the function with a deck
            deck = DeckFactory.create()

            with qtbot.wait_callback() as callback:
                check_and_install_new_deck_subscriptions(
                    subscribed_decks=[deck], on_done=callback
                )

            # Assert that the on_done callback was called with a future with an exception
            assert callback.args[0].exception() is not None

            # Assert that the mocked functions were called
            assert message_box_mock.call_count == 1

    def test_install_operation_raises_exception(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
        mock_show_dialog_with_cb: MockShowDialogWithCB,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            # Mock confirmation dialog
            mock_show_dialog_with_cb(
                "ankihub.gui.operations.new_deck_subscriptions.show_dialog",
                button_index=1,
            )

            # Mock download and install operation to raise an exception
            download_and_install_decks_mock = mocker.patch(
                "ankihub.gui.operations.new_deck_subscriptions.download_and_install_decks",
                side_effect=Exception("Something went wrong"),
            )

            # Call the function with a deck
            deck = DeckFactory.create()
            with qtbot.wait_callback() as callback:
                check_and_install_new_deck_subscriptions(
                    subscribed_decks=[deck], on_done=callback
                )

            # Assert that the on_done callback was called with a future with an exception
            assert callback.args[0].exception() is not None

            # Assert that the mocked functions were called
            assert download_and_install_decks_mock.call_count == 1


def test_get_deck_by_id(
    requests_mock: Mocker, next_deterministic_uuid: Callable[[], uuid.UUID]
):
    client = AnkiHubClient()
    client.local_media_dir_path_cb = lambda: Path("/tmp/ankihub_media")

    # test get deck by id
    ah_did = next_deterministic_uuid()
    date_time = datetime.now(tz=timezone.utc)
    expected_data = {
        "id": str(ah_did),
        "name": "test",
        "anki_id": 1,
        "csv_last_upload": date_time.strftime(ANKIHUB_DATETIME_FORMAT_STR),
        "csv_notes_filename": "test.csv",
        "media_upload_finished": False,
        "user_relation": "subscriber",
    }

    requests_mock.get(f"{config.api_url}/decks/{ah_did}/", json=expected_data)
    deck_info = client.get_deck_by_id(ah_did=ah_did)  # type: ignore
    assert deck_info == Deck(
        ah_did=ah_did,
        anki_did=1,
        name="test",
        csv_last_upload=date_time,
        csv_notes_filename="test.csv",
        media_upload_finished=False,
        user_relation=UserDeckRelation.SUBSCRIBER,
    )

    # test get deck by id unauthenticated
    requests_mock.get(f"{config.api_url}/decks/{ah_did}/", status_code=403)

    try:
        client.get_deck_by_id(ah_did=ah_did)  # type: ignore
    except AnkiHubHTTPError as e:
        exc = e
    assert exc is not None and exc.response.status_code == 403


@pytest.mark.parametrize("change_tags_to_upper_case", [True, False])
def test_suggest_note_update(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
    mocker: MockerFixture,
    change_tags_to_upper_case: bool,
):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw

        _, ah_did = install_sample_ah_deck()

        nid = mw.col.find_notes("")[0]
        note = mw.col.get_note(nid)

        # Set up tags on the note
        tags_that_shouldnt_be_sent = [
            # internal and optional tags should be ignored
            *ADDON_INTERNAL_TAGS,
            *ANKI_INTERNAL_TAGS,
            f"{TAG_FOR_OPTIONAL_TAGS}::TAG_GROUP::OptionalTag",
        ]

        note.tags = [
            "stays",
            "removed",
            *tags_that_shouldnt_be_sent,
        ]

        # Update the note in the database to match the note in the collection
        # so that the changes are detected relative to this state of the note.
        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did, notes_data=[to_note_data(note)]
        )

        # Make some changes to the note
        note["Front"] = "updated"
        note.tags.append("added")
        note.tags.remove("removed")

        if change_tags_to_upper_case:
            # Change the tags to upper case to see if the tag comparison is case-insensitive
            note.tags = [tag.upper() for tag in note.tags]

        # Suggest the changes
        create_change_note_suggestion_mock = mocker.patch.object(
            AnkiHubClient,
            "create_change_note_suggestion",
        )

        suggest_note_update(
            note=note,
            change_type=SuggestionType.NEW_CONTENT,
            comment="test",
            media_upload_cb=mocker.stub(),
        )

        # Check that the correct suggestion was created
        create_change_note_suggestion_mock.assert_called_once_with(
            change_note_suggestion=ChangeNoteSuggestion(
                anki_nid=note.id,
                ah_nid=ankihub_db.ankihub_nid_for_anki_nid(note.id),
                change_type=SuggestionType.NEW_CONTENT,
                fields=[Field(name="Front", value="updated", order=0)],
                # Even though the tag comparison is case-insensitive (e.g. the "stays" -> "STAYS" change is not sent),
                # the tags should be sent in the same case as they are in the note when a new tag is added.
                added_tags=["added" if not change_tags_to_upper_case else "ADDED"],
                removed_tags=["removed"],
                comment="test",
            ),
            auto_accept=False,
        )


def test_suggest_new_note(
    anki_session_with_addon_data: AnkiSession,
    mocker: MockerFixture,
    requests_mock: Mocker,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw

        _, ah_did = install_sample_ah_deck()
        note = mw.col.new_note(mw.col.models.by_name("Basic (Testdeck / user1)"))

        adapter = requests_mock.post(
            f"{config.api_url}/decks/{ah_did}/note-suggestion/",
            status_code=201,
        )

        note.tags = [
            "a",
            *ADDON_INTERNAL_TAGS,
            *ANKI_INTERNAL_TAGS,
            f"{TAG_FOR_OPTIONAL_TAGS}::TAG_GROUP::OptionalTag",
        ]
        suggest_new_note(
            note=note,
            ankihub_did=ah_did,
            comment="test",
            media_upload_cb=mocker.stub(),
        )

        # ... assert that add-on internal and optional tags were filtered out
        suggestion_data = adapter.last_request.json()  # type: ignore
        assert set(suggestion_data["tags"]) == set(
            [
                "a",
            ]
        )

        # test create change note suggestion unauthenticated
        url = f"{config.api_url}/decks/{ah_did}/note-suggestion/"
        requests_mock.post(
            url,
            status_code=403,
        )

        exc = None
        try:
            suggest_new_note(
                note=note,
                ankihub_did=ah_did,
                comment="test",
                media_upload_cb=mocker.stub(),
            )
        except AnkiHubHTTPError as e:
            exc = e
        assert exc is not None and exc.response.status_code == 403


class TestSuggestNotesInBulk:
    def test_new_note_suggestion(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        install_ah_deck: InstallAHDeck,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
    ):
        bulk_suggestions_method_mock = mocker.patch.object(
            AnkiHubClient, "create_suggestions_in_bulk"
        )
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()

            # Add a new note
            note_type = import_ah_note_type(ah_did=ah_did)
            new_note = add_anki_note(note_type=note_type)

            ah_nid = next_deterministic_uuid()
            mocker.patch("uuid.uuid4", return_value=ah_nid)

            result = suggest_notes_in_bulk(
                ankihub_did=ah_did,
                notes=[new_note],
                auto_accept=False,
                change_type=SuggestionType.NEW_CONTENT,
                comment="test",
                media_upload_cb=mocker.stub(),
            )

            assert bulk_suggestions_method_mock.call_count == 1
            assert bulk_suggestions_method_mock.call_args.kwargs == {
                "new_note_suggestions": [
                    NewNoteSuggestion(
                        ah_nid=ah_nid,
                        anki_nid=new_note.id,
                        fields=[
                            Field(name="Front", order=0, value=""),
                            Field(name="Back", order=1, value=""),
                        ],
                        tags=[],
                        guid=new_note.guid,
                        comment="test",
                        ah_did=ah_did,
                        note_type_name=note_type["name"],
                        anki_note_type_id=note_type["id"],
                    ),
                ],
                "change_note_suggestions": [],
                "auto_accept": False,
            }
            assert result.new_note_suggestions_count == 1
            assert result.change_note_suggestions_count == 0
            assert len(result.errors_by_nid) == 0

    @pytest.mark.parametrize(
        "note_has_changes, note_is_marked_as_deleted",
        [
            (True, True),
            (True, False),
            (False, True),
            (False, False),
        ],
    )
    def test_change_note_suggestion(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        note_has_changes: bool,
        note_is_marked_as_deleted: bool,
    ):
        bulk_suggestions_method_mock = mocker.patch.object(
            AnkiHubClient, "create_suggestions_in_bulk"
        )
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()

            ah_note = import_ah_note(ah_did=ah_did)
            changed_note = aqt.mw.col.get_note(NoteId(ah_note.anki_nid))

            if note_has_changes:
                changed_note["Front"] = "new front"
                changed_note.tags += ["test"]
                changed_note.flush()

            if note_is_marked_as_deleted:
                AnkiHubNote.update(
                    last_update_type=SuggestionType.DELETE.value[0]
                ).where(AnkiHubNote.anki_note_id == changed_note.id).execute()

            result = suggest_notes_in_bulk(
                ankihub_did=ah_did,
                notes=[changed_note],
                auto_accept=False,
                change_type=SuggestionType.NEW_CONTENT,
                comment="test",
                media_upload_cb=mocker.stub(),
            )

            if note_has_changes and not note_is_marked_as_deleted:
                assert bulk_suggestions_method_mock.call_count == 1
                assert bulk_suggestions_method_mock.call_args.kwargs == {
                    "change_note_suggestions": [
                        ChangeNoteSuggestion(
                            ah_nid=ah_note.ah_nid,
                            anki_nid=changed_note.id,
                            fields=[
                                Field(
                                    name="Front",
                                    order=0,
                                    value="new front",
                                ),
                            ],
                            added_tags=["test"],
                            removed_tags=[],
                            comment="test",
                            change_type=SuggestionType.NEW_CONTENT,
                        ),
                    ],
                    "new_note_suggestions": [],
                    "auto_accept": False,
                }
                assert result == BulkNoteSuggestionsResult(
                    new_note_suggestions_count=0,
                    change_note_suggestions_count=1,
                    errors_by_nid={},
                )
            else:
                assert bulk_suggestions_method_mock.call_count == 0
                assert result.change_note_suggestions_count == 0
                assert result.new_note_suggestions_count == 0
                assert len(result.errors_by_nid) == 1
                if note_is_marked_as_deleted:
                    assert ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR in str(
                        result.errors_by_nid[changed_note.id]
                    )
                else:
                    assert ANKIHUB_NO_CHANGE_ERROR in str(
                        result.errors_by_nid[changed_note.id]
                    )

    def test_suggestion_for_multiple_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        install_ah_deck: InstallAHDeck,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
        import_ah_note: ImportAHNote,
    ):
        anki_session = anki_session_with_addon_data
        bulk_suggestions_method_mock = mocker.patch.object(
            AnkiHubClient, "create_suggestions_in_bulk"
        )
        with anki_session.profile_loaded():
            ah_did = install_ah_deck()

            # Add multiple notes for new note suggestions
            note_type = import_ah_note_type(ah_did=ah_did)
            new_notes = [add_anki_note(note_type=note_type) for _ in range(3)]

            ah_nids = [next_deterministic_uuid() for _ in range(3)]
            mocker.patch("uuid.uuid4", side_effect=ah_nids)

            # Add multiple notes for change note suggestions
            ah_notes = [import_ah_note(ah_did=ah_did) for _ in range(3)]
            changed_notes = [
                aqt.mw.col.get_note(NoteId(ah_note.anki_nid)) for ah_note in ah_notes
            ]
            for note in changed_notes:
                note["Front"] = "new front"
                note.flush()

            result = suggest_notes_in_bulk(
                ankihub_did=ah_did,
                notes=new_notes + changed_notes,
                auto_accept=False,
                change_type=SuggestionType.NEW_CONTENT,
                comment="test",
                media_upload_cb=mocker.stub(),
            )

            assert bulk_suggestions_method_mock.call_count == 1

            def get_ah_nids_from_suggestions(
                suggestions: List[Union[ChangeNoteSuggestion, NewNoteSuggestion]]
            ) -> List[uuid.UUID]:
                return [suggestion.ah_nid for suggestion in suggestions]

            kwargs = bulk_suggestions_method_mock.call_args.kwargs

            assert len(kwargs["new_note_suggestions"]) == 3
            assert (
                get_ah_nids_from_suggestions(kwargs["new_note_suggestions"]) == ah_nids
            )

            assert len(kwargs["change_note_suggestions"]) == 3
            assert get_ah_nids_from_suggestions(kwargs["change_note_suggestions"]) == [
                ah_note.ah_nid for ah_note in ah_notes
            ]

            assert not kwargs["auto_accept"]
            assert result == BulkNoteSuggestionsResult(
                new_note_suggestions_count=3,
                change_note_suggestions_count=3,
                errors_by_nid={},
            )


def test_adjust_note_types(anki_session_with_addon_data: AnkiSession):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw

        # for testing creating missing note type
        ankihub_basic_1 = copy.deepcopy(mw.col.models.by_name("Basic"))
        ankihub_basic_1["id"] = 1
        ankihub_basic_1["name"] = "AnkiHub Basic 1"
        modify_note_type(ankihub_basic_1)

        # for testing updating existing note type
        ankihub_basic_2 = copy.deepcopy(mw.col.models.by_name("Basic"))
        ankihub_basic_2["name"] = "AnkiHub Basic 2"
        modify_note_type(ankihub_basic_2)
        # ... save the note type
        ankihub_basic_2["id"] = 0
        changes = mw.col.models.add_dict(ankihub_basic_2)
        ankihub_basic_2["id"] = changes.id
        # ... then add a field
        new_field = mw.col.models.new_field("foo")
        new_field["ord"] = 2
        mw.col.models.add_field(ankihub_basic_2, new_field)
        # ... and change the name
        ankihub_basic_2["name"] = "AnkiHub Basic 2 (new)"

        remote_note_types = {
            ankihub_basic_1["id"]: ankihub_basic_1,
            ankihub_basic_2["id"]: ankihub_basic_2,
        }
        _adjust_note_types_in_anki_db(remote_note_types)

        assert mw.col.models.by_name("AnkiHub Basic 1") is not None
        assert mw.col.models.get(ankihub_basic_2["id"])["flds"][3]["name"] == "foo"
        assert (
            mw.col.models.get(ankihub_basic_2["id"])["name"] == "AnkiHub Basic 2 (new)"
        )


def test_reset_note_types_of_notes(anki_session_with_addon_data: AnkiSession):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw

        # create a note and save it
        basic = mw.col.models.by_name("Basic")
        note = mw.col.new_note(basic)
        note["Front"] = "abc"
        note["Back"] = "abc"
        mw.col.add_note(note, mw.col.decks.active()[0])

        cloze = mw.col.models.by_name("Cloze")

        # change the note type of the note using reset_note_types_of_notes
        nid_mid_pairs = [
            (NoteId(note.id), NotetypeId(cloze["id"])),
        ]
        change_note_types_of_notes(nid_mid_pairs)

        assert mw.col.get_note(note.id).mid == cloze["id"]


class TestAnkiHubImporter:
    def test_import_new_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            # import the apkg to get the note types, then delete the deck
            file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
            importer = AnkiPackageImporter(mw.col, file)
            importer.run()
            mw.col.decks.remove([mw.col.decks.id_for_name("Testdeck")])

            ah_did = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=ankihub_sample_deck_notes_data(),
                deck_name="test",
                is_first_import_of_deck=True,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                note_types=SAMPLE_NOTE_TYPES,
                protected_fields={},
                protected_tags=[],
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )
            anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            assert (
                len(new_dids) == 1
            )  # we have no mechanism for importing subdecks from a csv yet, so ti will be just onen deck
            assert anki_did == list(new_dids)[0]

            assert len(import_result.created_nids) == 3
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(ah_did=ah_did)

    def test_import_existing_deck_1(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            # import the apkg
            file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
            importer = AnkiPackageImporter(mw.col, file)
            importer.run()
            existing_did = mw.col.decks.id_for_name("Testdeck")

            ah_did = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=ankihub_sample_deck_notes_data(),
                deck_name="test",
                is_first_import_of_deck=True,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                note_types=SAMPLE_NOTE_TYPES,
                protected_fields={},
                protected_tags=[],
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )
            anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            assert not new_dids
            assert anki_did == existing_did

            # no notes should be changed because they already exist
            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(ah_did=ah_did)

    def test_import_existing_deck_2(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            # import the apkg
            file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
            importer = AnkiPackageImporter(mw.col, file)
            importer.run()

            # move one card to another deck
            other_deck_id = mw.col.decks.add_normal_deck_with_name("other deck").id
            cids = mw.col.find_cards("deck:Testdeck")
            assert len(cids) == 3
            mw.col.set_deck([cids[0]], other_deck_id)

            ah_did = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=ankihub_sample_deck_notes_data(),
                deck_name="test",
                is_first_import_of_deck=False,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                note_types=SAMPLE_NOTE_TYPES,
                protected_fields={},
                protected_tags=[],
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )
            anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            # when the existing cards are in multiple seperate decks a new deck is created
            assert len(new_dids) == 1
            assert anki_did == list(new_dids)[0]

            # no notes should be changed because they already exist
            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(ah_did=ah_did)

    def test_import_existing_deck_3(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            # import the apkg
            file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
            importer = AnkiPackageImporter(mw.col, file)
            importer.run()
            existing_did = mw.col.decks.id_for_name("Testdeck")

            # modify two notes
            note_1 = mw.col.get_note(NoteId(1608240057545))
            note_1["Front"] = "new front"

            note_2 = mw.col.get_note(NoteId(1656968819662))
            note_2.tags.append("foo")

            mw.col.update_notes([note_1, note_2])

            # delete one note
            mw.col.remove_notes([NoteId(1608240029527)])

            ah_did = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=ankihub_sample_deck_notes_data(),
                deck_name="test",
                is_first_import_of_deck=True,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                note_types=SAMPLE_NOTE_TYPES,
                protected_fields={},
                protected_tags=[],
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )
            anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            assert not new_dids
            assert anki_did == existing_did

            assert len(import_result.created_nids) == 1
            assert len(import_result.updated_nids) == 2

            assert_that_only_ankihub_sample_deck_info_in_database(ah_did=ah_did)

    def test_update_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            anki_did, ah_did = install_sample_ah_deck()
            first_local_did = anki_did

            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=ankihub_sample_deck_notes_data(),
                note_types=SAMPLE_NOTE_TYPES,
                deck_name="test",
                is_first_import_of_deck=False,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                protected_fields={},
                protected_tags=[],
                anki_did=first_local_did,
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )
            second_anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            assert len(new_dids) == 0
            assert first_local_did == second_anki_did

            # no notes should be changed because they already exist
            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(ah_did=ah_did)

    def test_update_deck_when_it_was_deleted(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            anki_did, ah_did = install_sample_ah_deck()
            first_local_did = anki_did

            # move cards to another deck and remove the original one
            other_deck = mw.col.decks.add_normal_deck_with_name("other deck").id
            cids = mw.col.find_cards(f"deck:{mw.col.decks.name(first_local_did)}")
            assert len(cids) == 3
            mw.col.set_deck(cids, other_deck)
            mw.col.decks.remove([first_local_did])

            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=ankihub_sample_deck_notes_data(),
                note_types=SAMPLE_NOTE_TYPES,
                deck_name="test",
                is_first_import_of_deck=False,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                protected_fields={},
                protected_tags=[],
                anki_did=first_local_did,
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )
            second_anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            # deck with first_local_did should be recreated
            assert len(new_dids) == 1
            assert list(new_dids)[0] == first_local_did
            assert second_anki_did == first_local_did

            # no notes should be changed because they already exist
            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(ah_did=ah_did)

    def test_update_deck_with_subdecks(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            anki_did, ah_did = install_sample_ah_deck()

            # add a subdeck tag to a note
            notes_data = ankihub_sample_deck_notes_data()
            note_data = notes_data[0]
            note_data.tags = [f"{SUBDECK_TAG}::Testdeck::A::B"]
            note = mw.col.get_note(NoteId(note_data.anki_nid))

            # import the deck again, now with the changed note data
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=notes_data,
                deck_name="test",
                is_first_import_of_deck=False,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                note_types=SAMPLE_NOTE_TYPES,
                protected_fields={},
                protected_tags=[],
                anki_did=anki_did,
                subdecks=True,
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )
            second_anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            # assert that two new decks were created
            assert len(new_dids) == 2
            assert anki_did == second_anki_did
            assert mw.col.decks.by_name("Testdeck::A::B") is not None

            # one note should be updated
            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 1

            assert_that_only_ankihub_sample_deck_info_in_database(ah_did=ah_did)

            # check that cards of the note were moved to the subdeck
            assert note.cards()
            for card in note.cards():
                assert card.did == mw.col.decks.id_for_name("Testdeck::A::B")

    def test_import_deck_and_check_that_values_are_saved_to_databases(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # import the deck to setup note types
            _, ah_did = install_sample_ah_deck()

            note_data = ankihub_sample_deck_notes_data()[0]

            # set fields and tags of note_data
            # so that we can check if protected fields and tags are handled correctly
            protected_field_name = note_data.fields[0].name
            note_data.fields[0].value = "new field content"
            note_type_id = note_data.mid

            note_data.tags = ["tag1", "tag2"]

            nid = NoteId(note_data.anki_nid)
            note = mw.col.get_note(nid)
            note.tags = ["protected_tag"]

            protected_field_content = "protected field content"
            note[protected_field_name] = protected_field_content

            note.flush()

            importer = AnkiHubImporter()
            importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=[note_data],
                deck_name="test",
                is_first_import_of_deck=False,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                protected_fields={note_type_id: [protected_field_name]},
                protected_tags=["protected_tag"],
                note_types=SAMPLE_NOTE_TYPES,
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )

            # assert that the fields are saved correctly in the Anki DB (protected)
            assert note[protected_field_name] == protected_field_content

            # assert that the tags are saved correctly in the Anki DB (protected)
            note = mw.col.get_note(nid)
            assert set(note.tags) == set(["tag1", "tag2", "protected_tag"])

            # assert that the note_data was saved correctly in the AnkiHub DB (without modifications)
            note_data_from_db = ankihub_db.note_data(nid)
            assert note_data_from_db == note_data

    @pytest.mark.parametrize(
        # Deleted notes are not considered as conflicting notes and can be overwritten.
        "first_note_is_soft_deleted",
        [True, False],
    )
    def test_conflicting_notes_dont_get_imported(
        self,
        anki_session_with_addon_data: AnkiSession,
        ankihub_basic_note_type: NotetypeDict,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        first_note_is_soft_deleted: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            anki_nid = NoteId(1)
            mid_1 = ankihub_basic_note_type["id"]
            note_type_2 = create_copy_of_note_type(mw, ankihub_basic_note_type)
            mid_2 = note_type_2["id"]

            # Import the first note
            ah_did_1 = next_deterministic_uuid()
            note_info_1 = NoteInfoFactory.create(
                anki_nid=anki_nid,
                tags=["tag1"],
                mid=mid_1,
                last_update_type=(
                    SuggestionType.DELETE if first_note_is_soft_deleted else None
                ),
            )
            import_result = self._import_notes(
                [note_info_1],
                is_first_import_of_deck=True,
                ah_did=ah_did_1,
                note_types={mid_1: ankihub_basic_note_type},
            )

            mod_before = AnkiHubNote.get(AnkiHubNote.anki_note_id == anki_nid).mod
            sleep(1)  # Sleep to test for mod value changes

            # Import a second note with the same anki_nid
            ah_did_2 = next_deterministic_uuid()
            note_info_2 = NoteInfoFactory.create(
                anki_nid=anki_nid,
                tags=["tag2"],
                mid=mid_2,
            )
            import_result = self._import_notes(
                [note_info_2],
                is_first_import_of_deck=True,
                ah_did=ah_did_2,
                note_types={mid_2: note_type_2},
            )

            if first_note_is_soft_deleted:
                assert import_result.created_nids == [anki_nid]
                assert import_result.updated_nids == []
                assert import_result.skipped_nids == []
                assert import_result.deleted_nids == []
            else:
                assert import_result.created_nids == []
                assert import_result.updated_nids == []
                assert import_result.skipped_nids == [anki_nid]
                assert import_result.deleted_nids == []

            if first_note_is_soft_deleted:
                expected_ah_note = note_info_2
            else:
                expected_ah_note = note_info_1

            # Check the note data in the AnkiHub DB
            assert ankihub_db.note_data(anki_nid) == expected_ah_note

            # Check the mod value in the AnkiHub DB
            mod_after = AnkiHubNote.get(AnkiHubNote.anki_note_id == anki_nid).mod
            if first_note_is_soft_deleted:
                assert mod_after > mod_before
            else:
                assert mod_after == mod_before

            # Check the note data in the Anki DB
            assert to_note_data(mw.col.get_note(anki_nid)) == expected_ah_note

    @pytest.mark.parametrize(
        "behavior_on_remote_note_deleted, note_has_review",
        [
            (BehaviorOnRemoteNoteDeleted.NEVER_DELETE, False),
            (BehaviorOnRemoteNoteDeleted.NEVER_DELETE, True),
            (BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS, False),
            (BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS, True),
        ],
    )
    def test_import_note_deletion(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        next_deterministic_id: Callable[[], int],
        behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted,
        note_has_review: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            anki_did = DeckId(next_deterministic_id())
            ah_note = import_ah_note(ah_did=ah_did, anki_did=anki_did)

            if note_has_review:
                record_review_for_anki_nid(NoteId(ah_note.anki_nid))

            ah_note.last_update_type = SuggestionType.DELETE

            dids_before_import = all_dids()

            import_result = self._import_notes(
                [ah_note],
                behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
                is_first_import_of_deck=False,
                ah_did=ah_did,
                anki_did=anki_did,
            )

            new_dids = all_dids() - dids_before_import

            assert not new_dids

            if (
                behavior_on_remote_note_deleted
                == BehaviorOnRemoteNoteDeleted.NEVER_DELETE
                or (
                    behavior_on_remote_note_deleted
                    == BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS
                    and note_has_review
                )
            ):
                anki_note = aqt.mw.col.get_note(NoteId(ah_note.anki_nid))
                assert TAG_FOR_DELETED_NOTES in anki_note.tags
                assert anki_note[ANKIHUB_NOTE_TYPE_FIELD_NAME] == ""

                assert len(import_result.created_nids) == 0
                assert len(import_result.updated_nids) == 0
                assert len(import_result.marked_as_deleted_nids) == 1
                assert len(import_result.deleted_nids) == 0
            elif (
                behavior_on_remote_note_deleted
                == BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS
            ):
                with pytest.raises(NotFoundError):
                    aqt.mw.col.get_note(NoteId(ah_note.anki_nid))

                assert len(import_result.created_nids) == 0
                assert len(import_result.updated_nids) == 0
                assert len(import_result.marked_as_deleted_nids) == 0
                assert len(import_result.deleted_nids) == 1

    def test_import_note_deletion_with_one_note_deleted_and_one_marked_as_deleted(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        next_deterministic_id: Callable[[], int],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            anki_did = DeckId(next_deterministic_id())

            ah_note_1 = import_ah_note(ah_did=ah_did, anki_did=anki_did)
            ah_note_1.last_update_type = SuggestionType.DELETE
            record_review_for_anki_nid(NoteId(ah_note_1.anki_nid))

            ah_note_2 = import_ah_note(ah_did=ah_did, anki_did=anki_did)
            ah_note_2.last_update_type = SuggestionType.DELETE

            import_result = self._import_notes(
                [ah_note_1, ah_note_2],
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS,
                is_first_import_of_deck=False,
                ah_did=ah_did,
                anki_did=anki_did,
            )

            anki_note_1 = aqt.mw.col.get_note(NoteId(ah_note_1.anki_nid))
            assert TAG_FOR_DELETED_NOTES in anki_note_1.tags
            assert anki_note_1[ANKIHUB_NOTE_TYPE_FIELD_NAME] == ""

            with pytest.raises(NotFoundError):
                aqt.mw.col.get_note(NoteId(ah_note_2.anki_nid))

            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0
            assert len(import_result.marked_as_deleted_nids) == 1
            assert len(import_result.deleted_nids) == 1

    @pytest.mark.parametrize(
        "behavior_on_remote_note_deleted",
        [
            BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
            BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS,
        ],
    )
    def test_import_note_deletion_for_note_that_doesnt_exist_in_anki(
        self,
        anki_session_with_addon_data: AnkiSession,
        ankihub_basic_note_type: NotetypeDict,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        next_deterministic_id: Callable[[], int],
        behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            anki_did = DeckId(next_deterministic_id())

            ah_note = NoteInfoFactory.create(
                last_update_type=SuggestionType.DELETE,
                mid=ankihub_basic_note_type["id"],
            )
            import_result = self._import_notes(
                [ah_note],
                note_types={ankihub_basic_note_type["id"]: ankihub_basic_note_type},
                behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
                is_first_import_of_deck=False,
                ah_did=ah_did,
                anki_did=anki_did,
            )

            with pytest.raises(NotFoundError):
                aqt.mw.col.get_note(NoteId(ah_note.anki_nid))

            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0
            assert len(import_result.marked_as_deleted_nids) == 0
            assert len(import_result.deleted_nids) == 0

    @pytest.mark.parametrize("recommended_deck_settings", [True, False])
    def test_import_new_deck_uses_ankihub_deck_config(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        recommended_deck_settings: bool,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw
            file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
            importer = AnkiPackageImporter(mw.col, file)
            importer.run()
            mw.col.decks.remove([mw.col.decks.id_for_name("Testdeck")])

            ah_did = next_deterministic_uuid()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer.import_ankihub_deck(
                ankihub_did=ah_did,
                notes=ankihub_sample_deck_notes_data(),
                deck_name="test",
                is_first_import_of_deck=True,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                note_types=SAMPLE_NOTE_TYPES,
                protected_fields={},
                protected_tags=[],
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
                recommended_deck_settings=recommended_deck_settings,
            )
            anki_did = import_result.anki_did
            deck_config = mw.col.decks.config_dict_for_deck_id(anki_did)
            if recommended_deck_settings:
                assert deck_config["name"] == ANKIHUB_PRESET_NAME
            else:
                assert deck_config["name"] != ANKIHUB_PRESET_NAME

    def _import_notes(
        self,
        ah_notes: List[NoteInfo],
        ah_did: uuid.UUID,
        is_first_import_of_deck: bool,
        behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted = BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
        anki_did: Optional[DeckId] = None,
        note_types: Dict[NotetypeId, NotetypeDict] = {},
    ) -> AnkiHubImportResult:
        """Helper function to use the AnkiHubImporter to import notes with default arguments."""
        ankihub_importer = AnkiHubImporter()
        import_result = ankihub_importer.import_ankihub_deck(
            ankihub_did=ah_did,
            notes=ah_notes,
            deck_name="test",
            anki_did=anki_did,
            is_first_import_of_deck=is_first_import_of_deck,
            behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
            note_types=note_types,
            protected_fields={},
            protected_tags=[],
            suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                ah_did
            ),
            suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
        )
        return import_result


def assert_that_only_ankihub_sample_deck_info_in_database(ah_did: uuid.UUID):
    assert ankihub_db.ankihub_deck_ids() == [ah_did]
    assert len(ankihub_db.anki_nids_for_ankihub_deck(ah_did)) == 3


def create_copy_of_note_type(mw: AnkiQt, note_type: NotetypeDict) -> NotetypeDict:
    new_model = copy.deepcopy(note_type)
    new_model["id"] = 0
    changes = mw.col.models.add_dict(new_model)
    mid = NotetypeId(changes.id)
    result = mw.col.models.get(mid)
    return result


class TestAnkiHubImporterSuspendNewCardsOfExistingNotesOption:
    @pytest.mark.parametrize(
        "option_value, existing_card_suspended, expected_new_card_suspended",
        [
            # Always suspend new cards
            (SuspendNewCardsOfExistingNotes.ALWAYS, False, True),
            (SuspendNewCardsOfExistingNotes.ALWAYS, True, True),
            # Never suspend new cards
            (SuspendNewCardsOfExistingNotes.NEVER, True, False),
            (SuspendNewCardsOfExistingNotes.NEVER, False, False),
            # Suspend new cards if existing sibling cards are suspended
            (SuspendNewCardsOfExistingNotes.IF_SIBLINGS_SUSPENDED, True, True),
            (SuspendNewCardsOfExistingNotes.IF_SIBLINGS_SUSPENDED, False, False),
        ],
    )
    def test_suspend_new_cards_of_existing_notes_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        option_value: SuspendNewCardsOfExistingNotes,
        existing_card_suspended: bool,
        expected_new_card_suspended: bool,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            ah_did = install_ah_deck()
            config.set_suspend_new_cards_of_existing_notes(ah_did, option_value)

            ah_nid = next_deterministic_uuid()
            old_card, new_card = self._create_and_update_note_with_new_card(
                ah_did=ah_did,
                ah_nid=ah_nid,
                existing_card_suspended=existing_card_suspended,
                import_ah_note=import_ah_note,
            )

            # Assert the old card has the same suspension state as before
            assert old_card.queue == (
                QUEUE_TYPE_SUSPENDED if existing_card_suspended else QUEUE_TYPE_NEW
            )

            # Assert the new card is suspended or not suspended depending on the option value
            assert new_card.queue == (
                QUEUE_TYPE_SUSPENDED if expected_new_card_suspended else QUEUE_TYPE_NEW
            )

    def _create_and_update_note_with_new_card(
        self,
        ah_did: uuid.UUID,
        ah_nid: uuid.UUID,
        existing_card_suspended: bool,
        import_ah_note: ImportAHNote,
    ) -> Tuple[Card, Card]:
        # Create a cloze note with one card, optionally suspend the existing card,
        # then update the note, adding a new cloze, which results in a new card
        # getting created for the added cloze.
        # Return the old and new card.

        ankihub_cloze = create_or_get_ah_version_of_note_type(
            aqt.mw, aqt.mw.col.models.by_name("Cloze")
        )

        note = aqt.mw.col.new_note(ankihub_cloze)
        note["Text"] = "{{c1::foo}}"
        aqt.mw.col.add_note(note, DeckId(0))

        if existing_card_suspended:
            # Suspend the only card of the note
            card = note.cards()[0]
            card.queue = QUEUE_TYPE_SUSPENDED
            card.flush()

        # Update the note using the AnkiHub importer
        note_data = NoteInfoFactory.create(
            anki_nid=note.id,
            ah_nid=ah_nid,
            fields=[Field(name="Text", value="{{c1::foo}} {{c2::bar}}", order=0)],
            mid=ankihub_cloze["id"],
        )
        import_ah_note(note_data=note_data, mid=ankihub_cloze["id"], ah_did=ah_did)

        updated_note = aqt.mw.col.get_note(note.id)

        assert len(updated_note.cards()) == 2  # one existing and one new card

        # The id is a timestamp, so the old card has a lower id than the new card
        old_card = min(updated_note.cards(), key=lambda c: c.id)
        new_card = max(updated_note.cards(), key=lambda c: c.id)

        return old_card, new_card


class TestAnkiHubImporterSuspendNewCardsOfNewNotesOption:
    @pytest.mark.parametrize(
        "option_value, expected_new_card_suspended",
        [
            (True, True),
            (False, False),
        ],
    )
    def test_suspend_new_cards_of_new_notes_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        option_value: bool,
        expected_new_card_suspended: bool,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            ah_did = install_ah_deck()
            config.set_suspend_new_cards_of_new_notes(ah_did, option_value)

            note_info = import_ah_note(ah_did=ah_did)
            note = aqt.mw.col.get_note(NoteId(note_info.anki_nid))
            assert len(note.cards()) == 1

            new_card = note.cards()[0]

            # Assert the new card is suspended or not suspended depending on the option value
            assert new_card.queue == (
                QUEUE_TYPE_SUSPENDED if expected_new_card_suspended else QUEUE_TYPE_NEW
            )


def test_keep_notes_with_instructions_tag_unsuspended(
    anki_session_with_addon_data: AnkiSession,
    install_ah_deck: InstallAHDeck,
    import_ah_note: ImportAHNote,
):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        ah_did = install_ah_deck()
        config.set_suspend_new_cards_of_new_notes(ah_did, True)
        note_info = NoteInfoFactory.create(tags=[settings.TAG_FOR_INSTRUCTION_NOTES])
        import_ah_note(ah_did=ah_did, note_data=note_info)
        note = aqt.mw.col.get_note(NoteId(note_info.anki_nid))
        new_card = note.cards()[0]

        assert new_card.queue == QUEUE_TYPE_NEW


def test_unsubscribe_from_deck(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
    qtbot: QtBot,
    mocker: MockerFixture,
    requests_mock: Mocker,
):
    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw

        anki_deck_id, ah_did = install_sample_ah_deck()

        mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
        assert len(mids) == 2

        mocker.patch.object(config, "is_logged_in", return_value=True)
        deck = mw.col.decks.get(anki_deck_id)
        requests_mock.get(
            f"{DEFAULT_API_URL}/decks/subscriptions/",
            status_code=200,
            json=[
                {
                    "deck": {
                        "id": str(ah_did),
                        "name": deck["name"],
                        "owner": 1,
                        "anki_id": anki_deck_id,
                        "csv_last_upload": None,
                        "csv_notes_filename": "",
                        "media_upload_finished": True,
                        "user_relation": "subscriber",
                    }
                }
            ],
        )
        dialog = DeckManagementDialog()
        decks_list = dialog.decks_list
        deck_item_index = 0
        deck_item = decks_list.item(deck_item_index)
        deck_item.setSelected(True)
        mocker.patch("ankihub.gui.decks_dialog.ask_user", return_value=True)

        requests_mock.get(
            f"{DEFAULT_API_URL}/decks/subscriptions/", status_code=200, json=[]
        )
        unsubscribe_from_deck_mock = mocker.patch.object(
            AnkiHubClient,
            "unsubscribe_from_deck",
        )
        qtbot.mouseClick(dialog.unsubscribe_btn, Qt.MouseButton.LeftButton)
        unsubscribe_from_deck_mock.assert_called_once()

        assert dialog.decks_list.count() == 0

        # check if note type modifications were removed
        assert all(not note_type_contains_field(mw.col.models.get(mid)) for mid in mids)
        assert all(
            not re.search(
                ANKIHUB_TEMPLATE_SNIPPET_RE, mw.col.models.get(mid)["tmpls"][0]["afmt"]
            )
            for mid in mids
        )

        # check if the deck was removed from the db
        mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
        assert len(mids) == 0

        nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
        assert len(nids) == 0


def import_note_types_for_sample_deck(mw: AnkiQt):
    # import the apkg to get the note types, then delete created decks
    dids_before_import = all_dids()

    file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
    importer = AnkiPackageImporter(mw.col, file)
    importer.run()

    dids_after_import = all_dids()
    new_dids = list(dids_after_import - dids_before_import)

    mw.col.decks.remove(new_dids)


class TestPrepareNote:
    def test_prepare_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        create_anki_ah_note: CreateAnkiAHNote,
        ankihub_basic_note_type: NotetypeDict,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ankihub_nid = next_deterministic_uuid()

            new_fields = [
                Field(name="Front", value="new front", order=0),
                Field(name="Back", value="new back", order=1),
            ]
            new_tags = ["c", "d"]

            note = create_anki_ah_note(ankihub_nid=ankihub_nid)
            note.tags = ["a", "b"]
            note_was_changed_1 = prepare_note(
                note,
                fields=new_fields,
                tags=new_tags,
                protected_fields={ankihub_basic_note_type["id"]: ["Back"]},
                protected_tags=["a"],
            )
            # assert that the note was modified but the protected fields and tags were not
            assert note_was_changed_1
            assert note["Front"] == "new front"
            assert note["Back"] == "old back"
            assert set(note.tags) == set(["a", "c", "d"])

            # assert that the note was not modified because the same arguments were used on the same note
            note_was_changed_2 = prepare_note(
                note,
                fields=new_fields,
                tags=new_tags,
                protected_fields={ankihub_basic_note_type["id"]: ["Back"]},
                protected_tags=["a"],
            )
            assert not note_was_changed_2
            assert note["Front"] == "new front"
            assert note["Back"] == "old back"
            assert set(note.tags) == set(["a", "c", "d"])

            # assert that addon-internal tags don't get removed
            note = create_anki_ah_note(ankihub_nid=ankihub_nid)
            note.tags = list(ADDON_INTERNAL_TAGS)
            note_was_changed_5 = prepare_note(note, tags=[])
            assert not note_was_changed_5
            assert set(note.tags) == set(ADDON_INTERNAL_TAGS)

            # assert that fields protected by tags are in fact protected
            note = create_anki_ah_note(ankihub_nid=ankihub_nid)
            note.tags = [f"{TAG_FOR_PROTECTING_FIELDS}::Front"]
            note["Front"] = "old front"
            note_was_changed_6 = prepare_note(
                note,
                fields=[Field(name="Front", value="new front", order=0)],
            )
            assert not note_was_changed_6
            assert note["Front"] == "old front"

            # assert that fields protected by tags are in fact protected
            note = create_anki_ah_note(ankihub_nid=ankihub_nid)
            note.tags = [f"{TAG_FOR_PROTECTING_FIELDS}::All"]
            note_was_changed_7 = prepare_note(
                note,
                fields=[
                    Field(name="Front", value="new front", order=0),
                    Field(name="Back", value="new back", order=1),
                ],
            )
            assert not note_was_changed_7
            assert note["Front"] == "old front"
            assert note["Back"] == "old back"

            # assert that the tag for protecting all fields works
            note = create_anki_ah_note(ankihub_nid=ankihub_nid)
            note.tags = [f"{TAG_FOR_PROTECTING_FIELDS}::All"]
            note_was_changed_7 = prepare_note(
                note,
                fields=[
                    Field(name="Front", value="new front", order=0),
                    Field(name="Back", value="new back", order=1),
                ],
            )
            assert not note_was_changed_7
            assert note["Front"] == "old front"
            assert note["Back"] == "old back"

            # assert that the note guid is changed
            note = create_anki_ah_note(ankihub_nid=ankihub_nid)
            note_was_changed_8 = prepare_note(
                note,
                guid="new guid",
            )
            assert note_was_changed_8
            assert note.guid == "new guid"

    def test_prepare_note_protect_field_with_spaces(
        self,
        anki_session_with_addon_data: AnkiSession,
        create_anki_ah_note: CreateAnkiAHNote,
        ankihub_basic_note_type: Dict[str, Any],
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        anki_session = anki_session_with_addon_data
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session.mw

            ankihub_nid = next_deterministic_uuid()

            field_name_with_spaces = "Field name with spaces"
            ah_basic_variation = ankihub_basic_note_type.copy()
            ah_basic_variation["id"] = 0
            ah_basic_variation["name"] = "AnkiHub Basic Variation"
            ah_basic_variation["flds"][0]["name"] = field_name_with_spaces
            ah_basic_variation["tmpls"][0]["qfmt"] = ankihub_basic_note_type["tmpls"][
                0
            ]["qfmt"].replace("Front", field_name_with_spaces)
            mw.col.models.add_dict(ah_basic_variation)
            ah_basic_variation = mw.col.models.by_name(ah_basic_variation["name"])
            ah_basic_variation_id = ah_basic_variation["id"]

            # assert that fields with spaces are protected by tags that have spaces replaced by underscores
            note = create_anki_ah_note(
                ankihub_nid=ankihub_nid,
                note_type_id=ah_basic_variation_id,
            )
            note.tags = [
                f"{TAG_FOR_PROTECTING_FIELDS}::{field_name_with_spaces.replace(' ', '_')}"
            ]
            note_changed = prepare_note(
                note=note,
                ankihub_nid=ankihub_nid,
                fields=[Field(name=field_name_with_spaces, value="new front", order=0)],
            )
            assert not note_changed
            assert note[field_name_with_spaces] == "old field name with spaces"

            # assert that field is not protected without this tag (to make sure the test is correct)
            note = create_anki_ah_note(
                ankihub_nid=ankihub_nid,
                note_type_id=ah_basic_variation_id,
            )
            note_changed = prepare_note(
                note=note,
                ankihub_nid=ankihub_nid,
                fields=[Field(name=field_name_with_spaces, value="new front", order=0)],
            )
            assert note_changed
            assert note[field_name_with_spaces] == "new front"


def prepare_note(
    note,
    ankihub_nid: Optional[uuid.UUID] = None,
    tags: List[str] = [],
    fields: Optional[List[Field]] = [],
    protected_fields: Optional[Dict] = {},
    protected_tags: List[str] = [],
    guid: Optional[str] = None,
    last_update_type: SuggestionType = SuggestionType.NEW_CONTENT,
):
    if ankihub_nid is None:
        ankihub_nid = note[ANKIHUB_NOTE_TYPE_FIELD_NAME]

    if guid is None:
        guid = note.guid

    note_data = NoteInfo(
        ah_nid=ankihub_nid,
        anki_nid=note.id,
        fields=fields,
        tags=tags,
        mid=note.mid,
        guid=guid,
        last_update_type=last_update_type,
    )

    ankihub_importer = AnkiHubImporter()
    result = ankihub_importer._prepare_note_inner(
        note,
        note_data=note_data,
        protected_fields=protected_fields,
        protected_tags=protected_tags,
    )
    return result


class TestCustomSearchNodes:
    def test_use_custom_search_node_in_browser_search(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        qtbot: QtBot,
    ):
        setup_browser()
        with anki_session_with_addon_data.profile_loaded():
            # Add an AnkiHub note to the collection
            note_info = NoteInfoFactory.create()
            import_ah_note(note_info)

            # Add a non-AnkiHub note to the collection
            add_basic_anki_note_to_deck(DeckId(1))

            # Search for new AnkiHub notes in the browser using our NewNoteSearchNode
            browser: Browser = dialogs.open("Browser", aqt.mw)
            search_string = f"{NewNoteSearchNode.parameter_name}:"
            browser.search_for(search=search_string)

            # Assert that only the AnkiHub note is in the search results
            browser.table.select_all()
            assert browser.table.get_selected_note_ids() == [note_info.anki_nid]

            # Close the browser to prevent RuntimeErrors getting raised during teardown
            with qtbot.wait_callback() as callback:
                dialogs.closeAll(onsuccess=callback)

    def test_use_custom_search_node_in_browser_search_with_invalid_parameter(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        qtbot: QtBot,
    ):
        setup_browser()
        with anki_session_with_addon_data.profile_loaded():
            showWarning_mock = mocker.patch("ankihub.gui.browser.browser.showWarning")

            browser: Browser = dialogs.open("Browser", aqt.mw)
            search_string = f"{NewNoteSearchNode.parameter_name}:invalid-parameter"
            browser.search_for(search=search_string)

            assert showWarning_mock.called

            # Close the browser to prevent RuntimeErrors getting raised during teardown
            with qtbot.wait_callback() as callback:
                dialogs.closeAll(onsuccess=callback)

    def test_ModifiedAfterSyncSearchNode_with_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()
            all_nids = mw.col.find_notes("")

            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            assert (
                ModifiedAfterSyncSearchNode(browser, "yes").filter_ids(all_nids) == []
            )
            assert set(
                ModifiedAfterSyncSearchNode(browser, "no").filter_ids(all_nids)
            ) == set(all_nids)

            # we can't use freeze_time here because note.mod is set by the Rust backend
            sleep(1.1)

            # modify a note - this changes its mod value in the Anki DB
            nid = all_nids[0]
            note = mw.col.get_note(nid)
            note["Front"] = "new front"
            note.flush()

            nids = ModifiedAfterSyncSearchNode(browser, "yes").filter_ids(all_nids)
            assert nids == [nid]

    def test_ModifiedAfterSyncSearchNode_with_cards(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()
            all_cids = mw.col.find_cards("")

            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = False

            assert (
                ModifiedAfterSyncSearchNode(browser, "yes").filter_ids(all_cids) == []
            )
            assert set(
                ModifiedAfterSyncSearchNode(browser, "no").filter_ids(all_cids)
            ) == set(all_cids)

            # we can't use freeze_time here because note.mod is set by the Rust backend
            sleep(1.1)

            # modify a note - this changes its mod value in the Anki DB
            cid = all_cids[0]
            note = mw.col.get_note(mw.col.get_card(cid).nid)
            note["Front"] = "new front"
            note.flush()

            cids = ModifiedAfterSyncSearchNode(browser, "yes").filter_ids(all_cids)
            assert cids == [cid]

    def test_ModifiedAfterSyncSearchNode_excludes_deleted_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            import_ah_note()
            AnkiHubNote.update(
                last_update_type=SuggestionType.DELETE.value[0]
            ).execute()

            # The note is soft deleted, so it should not be included in the search results
            all_nids = aqt.mw.col.find_notes("")
            assert (
                ModifiedAfterSyncSearchNode(browser, "yes").filter_ids(all_nids) == []
            )
            assert ModifiedAfterSyncSearchNode(browser, "no").filter_ids(all_nids) == []

    def test_UpdatedInTheLastXDaysSearchNode(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()

            all_nids = mw.col.find_notes("")

            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            assert (
                UpdatedInTheLastXDaysSearchNode(browser, "1").filter_ids(all_nids)
                == all_nids
            )
            assert (
                UpdatedInTheLastXDaysSearchNode(browser, "2").filter_ids(all_nids)
                == all_nids
            )

            yesterday_timestamp = int((datetime.now() - timedelta(days=1)).timestamp())
            AnkiHubNote.update(mod=yesterday_timestamp).execute()

            assert (
                UpdatedInTheLastXDaysSearchNode(browser, "1").filter_ids(all_nids) == []
            )
            assert (
                UpdatedInTheLastXDaysSearchNode(browser, "2").filter_ids(all_nids)
                == all_nids
            )

    def test_NewNoteSearchNode(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            import_note_types_for_sample_deck(mw)
            notes_data = ankihub_sample_deck_notes_data()
            notes_data[0].last_update_type = None
            notes_data[1].last_update_type = None
            notes_data[2].last_update_type = SuggestionType.OTHER

            ankihub_models = {
                m["id"]: m for m in mw.col.models.all() if "/" in m["name"]
            }
            ah_did = next_deterministic_uuid()
            AnkiHubImporter().import_ankihub_deck(
                ankihub_did=ah_did,
                notes=notes_data,
                note_types=ankihub_models,
                protected_fields={},
                protected_tags=[],
                deck_name="Test-Deck",
                is_first_import_of_deck=True,
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )

            all_nids = mw.col.find_notes("")

            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            # notes without a last_update_type are new
            assert NewNoteSearchNode(browser, "").filter_ids(all_nids) == [
                notes_data[0].anki_nid,
                notes_data[1].anki_nid,
            ]

    def test_SuggestionTypeSearchNode(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            import_note_types_for_sample_deck(mw)
            notes_data = ankihub_sample_deck_notes_data()
            notes_data[0].last_update_type = SuggestionType.NEW_CONTENT
            notes_data[1].last_update_type = SuggestionType.NEW_CONTENT
            notes_data[2].last_update_type = SuggestionType.SPELLING_GRAMMATICAL

            ankihub_models = {
                m["id"]: m for m in mw.col.models.all() if "/" in m["name"]
            }
            ah_did = next_deterministic_uuid()
            AnkiHubImporter().import_ankihub_deck(
                ankihub_did=ah_did,
                notes=notes_data,
                note_types=ankihub_models,
                protected_fields={},
                protected_tags=[],
                deck_name="Test-Deck",
                behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
                suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                    ah_did
                ),
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
                is_first_import_of_deck=True,
            )

            all_nids = mw.col.find_notes("")

            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            assert SuggestionTypeSearchNode(
                browser, SuggestionType.NEW_CONTENT.value[0]
            ).filter_ids(all_nids) == [
                notes_data[0].anki_nid,
                notes_data[1].anki_nid,
            ]
            assert SuggestionTypeSearchNode(
                browser, SuggestionType.SPELLING_GRAMMATICAL.value[0]
            ).filter_ids(all_nids) == [notes_data[2].anki_nid]

    def test_UpdatedSinceLastReviewSearchNode(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()

            all_nids = mw.col.find_notes("")

            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            assert (
                UpdatedSinceLastReviewSearchNode(browser, "").filter_ids(all_nids) == []
            )

            # Add a review entry for a card to the database.
            nid = all_nids[0]
            note = mw.col.get_note(nid)
            cid = note.card_ids()[0]

            record_review(cid, review_time_ms=1 * 1000)

            # Update the mod time in the ankihub database to simulate a note update.
            AnkiHubNote.update(mod=2).where(AnkiHubNote.anki_note_id == nid).execute()

            # Check that the note of the card is now included in the search results.
            assert UpdatedSinceLastReviewSearchNode(browser, "").filter_ids(
                all_nids
            ) == [nid]

            # Add another review entry for the card to the database.
            record_review(cid, review_time_ms=3 * 1000)

            # Check that the note of the card is not included in the search results anymore.
            assert (
                UpdatedSinceLastReviewSearchNode(browser, "").filter_ids(all_nids) == []
            )

    def test_AnkiHubNoteSearchNode_yes(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        add_anki_note: AddAnkiNote,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_note_1 = import_ah_note()
            add_anki_note()

            # Add a deleted note the ankihub database. It should be included in the search results.
            ah_note_2 = import_ah_note()
            AnkiHubNote.update(last_update_type=SuggestionType.DELETE.value[0]).where(
                AnkiHubNote.ankihub_note_id == ah_note_2.ah_nid
            ).execute()

            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            all_nids = aqt.mw.col.find_notes("")
            assert AnkiHubNoteSearchNode(browser, "yes").filter_ids(all_nids) == [
                ah_note_1.anki_nid,
                ah_note_2.anki_nid,
            ]

    def test_AnkiHubNoteSearchNode_no(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        add_anki_note: AddAnkiNote,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            import_ah_note()
            note = add_anki_note()

            all_nids = aqt.mw.col.find_notes("")
            assert AnkiHubNoteSearchNode(browser, "no").filter_ids(all_nids) == [
                note.id
            ]

    def test_AnkiHubNoteSearchNode_invalid_value(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            browser = mocker.Mock()
            browser.table.is_notes_mode.return_value = True

            all_nids = aqt.mw.col.find_notes("")
            with pytest.raises(
                ValueError,
                match=rf"Invalid value for {AnkiHubNoteSearchNode.parameter_name}.+",
            ):
                AnkiHubNoteSearchNode(browser, "invalid").filter_ids(all_nids)


class TestBrowserTreeView:
    # without this mark the test sometime fails on clean-up
    @pytest.mark.qt_no_exception_capture
    def test_ankihub_items_exist_and_work(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        config.public_config["sync_on_startup"] = False
        entry_point.run()

        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()

            browser: Browser = dialogs.open("Browser", mw)

            qtbot.wait(500)
            sidebar: SidebarTreeView = browser.sidebar
            ankihub_item: SidebarItem = sidebar.model().root.children[0]
            assert "AnkiHub" in ankihub_item.name

            # assert that all children of the ankihub_item exist
            ankihub_child_item_names = [item.name for item in ankihub_item.children]
            assert ankihub_child_item_names == [
                "With AnkiHub ID",
                "ID Pending",
                "Modified After Sync",
                "Not Modified After Sync",
                "Updated Today",
                "Updated Since Last Review",
                "Deleted Notes",
            ]

            updated_today_item = ankihub_item.children[4]
            assert updated_today_item.name == "Updated Today"
            updated_today_child_item_names = [
                item.name for item in updated_today_item.children
            ]
            assert updated_today_child_item_names == [
                "New Note",
                *[x.value[1] for x in SuggestionType],
            ]

            # click on the first item
            with_ankihub_id_item = ankihub_item.children[0]
            sidebar._on_search(sidebar.model().index_for_item(with_ankihub_id_item))
            qtbot.wait(500)

            # assert that expected number of notes shows up
            browser.table.select_all()
            nids = browser.table.get_selected_note_ids()
            assert len(nids) == 3

    # without this mark the test sometime fails on clean-up
    @pytest.mark.qt_no_exception_capture
    def test_contains_ankihub_tag_items(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        config.public_config["sync_on_startup"] = False
        entry_point.run()

        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()

            notes = mw.col.find_notes("")
            note = mw.col.get_note(notes[0])

            # add ankihub tags to a note
            # when no notes have the tag, the related ankihub tag tree item will not exist
            note.tags = [TAG_FOR_PROTECTING_FIELDS, SUBDECK_TAG, TAG_FOR_OPTIONAL_TAGS]
            note.flush()

            browser: Browser = dialogs.open("Browser", mw)

            qtbot.wait(500)
            sidebar: SidebarTreeView = browser.sidebar
            ankihub_item: SidebarItem = sidebar.model().root.children[0]
            assert "AnkiHub" in ankihub_item.name

            # assert that all children of the ankihub_item exist
            item_names = [item.name for item in ankihub_item.children]
            assert item_names == [
                "With AnkiHub ID",
                "ID Pending",
                "Modified After Sync",
                "Not Modified After Sync",
                "Updated Today",
                "Updated Since Last Review",
                "Deleted Notes",
                TAG_FOR_OPTIONAL_TAGS,
                TAG_FOR_PROTECTING_FIELDS,
                SUBDECK_TAG,
            ]


# without this mark the test sometime fails on clean-up
@pytest.mark.qt_no_exception_capture
def test_browser_custom_columns(
    anki_session_with_addon_data: AnkiSession,
    qtbot: QtBot,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    config.public_config["sync_on_startup"] = False
    entry_point.run()

    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        install_sample_ah_deck()

        notes_data = ankihub_sample_deck_notes_data()

        browser: Browser = dialogs.open("Browser", mw)
        browser.search_for("")
        qtbot.wait(500)

        browser.table.select_all()
        nids = browser.table.get_selected_note_ids()
        assert len(nids) == len(notes_data) == 3

        # enable all custom columns
        for custom_column in custom_columns:
            browser.table._on_column_toggled(True, custom_column.builtin_column.key)

        qtbot.wait(500)

        # compare the custom column values with the expected values for the first row
        current_row = browser.table._model.get_row(browser.table._current())
        custom_column_cells = current_row.cells[4:]
        custom_column_cells_texts = [cell.text for cell in custom_column_cells]
        assert custom_column_cells_texts == [
            str(notes_data[0].ah_nid),
            "No",
            "No",
        ]


class TestBrowserContextMenu:
    def test_ankihub_actions_are_added_to_the_browser_context_menu(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            note_info = NoteInfoFactory.create()
            import_ah_note(note_info)

            menu = self.open_browser_context_menu_with_all_notes_selected()

            # Get the texts of the actions in the menu
            actions = [action for action in menu.actions() if not action.isSeparator()]
            texts = [action.text() for action in actions]

            expected_texts = [
                "AnkiHub: Bulk suggest notes",
                "AnkiHub: Suggest to delete note",
                "AnkiHub: Protect fields",
                "AnkiHub: Reset local changes",
                "AnkiHub: Suggest Optional Tags",
                "AnkiHub: Copy Anki Note ID to clipboard",
                "AnkiHub: Copy AnkiHub Note ID to clipboard",
            ]

            assert texts == expected_texts

            # Close the browser to prevent RuntimeErrors getting raised during teardown
            with qtbot.wait_callback() as callback:
                dialogs.closeAll(onsuccess=callback)

    def open_browser_context_menu_with_all_notes_selected(self) -> QMenu:
        """Returns a menu with just our context menu actions. The actions behave as if
        all notes in the Anki collection were selected."""
        # Set up our browser modifications, open the browser and select all notes
        setup_browser()
        browser: Browser = dialogs.open("Browser", aqt.mw)
        browser.search_for(search="")  # The empty search matches all notes
        browser.table.select_all()

        # Call Anki's hoks for the context menu and let it add the actions to a menu
        menu = QMenu()
        browser_will_show_context_menu(browser=browser, menu=menu)

        return menu

    @pytest.mark.parametrize(
        "action_text, expected_change_type_text",
        [
            # The suggestion type which is selected by default is UPDATED_CONTENT
            ("AnkiHub: Bulk suggest notes", SuggestionType.UPDATED_CONTENT.value[1]),
            # When the user uses the "Suggest to delete note" action, the suggestion type is DELETE
            ("AnkiHub: Suggest to delete note", SuggestionType.DELETE.value[1]),
        ],
    )
    def test_note_suggestion_actions(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        latest_instance_tracker: LatestInstanceTracker,
        qtbot: QtBot,
        action_text: str,
        expected_change_type_text: str,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info = NoteInfoFactory.create()
            import_ah_note(note_info, ah_did=ah_did)

            menu = self.open_browser_context_menu_with_all_notes_selected()

            latest_instance_tracker.track(SuggestionDialog)

            # Trigger the action
            action: QAction = next(
                action for action in menu.actions() if action.text() == action_text
            )
            action.trigger()

            # Assert the suggestion dialog was opened with the right suggestion type
            dialog: SuggestionDialog = latest_instance_tracker.get_latest_instance(
                SuggestionDialog
            )
            assert dialog.isVisible()
            assert dialog.change_type_select.currentText() == expected_change_type_text

            # Close the browser to prevent RuntimeErrors getting raised during teardown
            with qtbot.wait_callback() as callback:
                dialogs.closeAll(onsuccess=callback)

    @pytest.mark.parametrize(
        "action_text, expected_note_attribute_in_clipboard",
        [
            ("AnkiHub: Copy Anki Note ID to clipboard", "anki_nid"),
            ("AnkiHub: Copy AnkiHub Note ID to clipboard", "ah_nid"),
        ],
    )
    def test_copy_nid_actions(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        mocker: MockerFixture,
        qtbot: QtBot,
        action_text: str,
        expected_note_attribute_in_clipboard: str,
    ):
        with anki_session_with_addon_data.profile_loaded():
            note_info = NoteInfoFactory.create()
            import_ah_note(note_info)

            menu = self.open_browser_context_menu_with_all_notes_selected()

            # Trigger the action
            action: QAction = next(
                action for action in menu.actions() if action.text() == action_text
            )
            clipboard = aqt.mw.app.clipboard()
            clipboard_setText_mock = mocker.patch.object(clipboard, "setText")
            action.trigger()

            # Assert that the clipboard contains the expected nid
            qtbot.wait_until(lambda: clipboard_setText_mock.called)

            expected_clipboard_text = str(
                getattr(note_info, expected_note_attribute_in_clipboard)
            )
            clipboard_setText_mock.assert_called_once_with(expected_clipboard_text)

            # Close the browser to prevent RuntimeErrors getting raised during teardown
            with qtbot.wait_callback() as callback:
                dialogs.closeAll(onsuccess=callback)


@pytest.mark.qt_no_exception_capture
@pytest.mark.parametrize(
    "field_names_to_protect, expected_tag",
    [
        ({"Front"}, f"{TAG_FOR_PROTECTING_FIELDS}::Front"),
        ({"Front", "Back"}, f"{TAG_FOR_PROTECTING_ALL_FIELDS}"),
    ],
)
def test_protect_fields_action(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
    mocker: MockerFixture,
    qtbot: QtBot,
    field_names_to_protect: Set[str],
    expected_tag: str,
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        install_sample_ah_deck()

        # Open the browser
        browser: Browser = dialogs.open("Browser", mw)

        # Patch gui function choose_subset to return the fields to protect
        mocker.patch(
            "ankihub.gui.browser.browser.choose_subset",
            return_value=field_names_to_protect,
        )

        # Call the action for a note
        nids = mw.col.find_notes("Front:*")
        nid = nids[0]
        _on_protect_fields_action(browser, [nid])

        # Assert that the note has the expected tag
        def assert_note_has_expected_tag():
            note = mw.col.get_note(nid)
            assert expected_tag in note.tags

        qtbot.wait_until(assert_note_has_expected_tag)


class TestDeckManagementDialog:
    @pytest.mark.parametrize(
        "nightmode",
        [True, False],
    )
    def test_basic(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        qtbot: QtBot,
        mocker: MockerFixture,
        nightmode: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            self._mock_dependencies(mocker)

            deck_name = "Test Deck"
            ah_did = install_ah_deck(ah_deck_name=deck_name)
            anki_did = config.deck_config(ah_did).anki_id

            mocker.patch.object(
                AnkiHubClient,
                "get_deck_subscriptions",
                return_value=[
                    DeckFactory.create(ah_did=ah_did, anki_did=anki_did, name=deck_name)
                ],
            )

            theme_manager.night_mode = nightmode

            dialog = DeckManagementDialog()
            dialog.display_subscribe_window()

            assert dialog.decks_list.count() == 1

            # Select a deck from the list
            dialog.decks_list.setCurrentRow(0)
            qtbot.wait(200)

            deck_name = config.deck_config(ah_did).name
            assert deck_name in dialog.deck_name_label.text()

    def test_toggle_subdecks(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            self._mock_dependencies(mocker)

            # Install a deck with subdeck tags
            subdeck_name, anki_did, ah_did = self._install_deck_with_subdeck_tag(
                install_ah_deck, import_ah_note
            )
            # ... The subdeck should not exist yet
            assert aqt.mw.col.decks.by_name(subdeck_name) is None

            # Mock get_deck_subscriptions to return the deck
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_subscriptions",
                return_value=[DeckFactory.create(ah_did=ah_did, anki_did=anki_did)],
            )

            # Open the dialog
            dialog = DeckManagementDialog()
            dialog.display_subscribe_window()
            qtbot.wait(200)

            # Select the deck and click the toggle subdeck button
            assert dialog.decks_list.count() == 1
            dialog.decks_list.setCurrentRow(0)
            qtbot.wait(200)

            assert dialog.subdecks_cb.isEnabled()
            dialog.subdecks_cb.click()
            qtbot.wait(200)

            # The subdeck should now exist
            assert aqt.mw.col.decks.by_name(subdeck_name) is not None

            # Click the toggle subdeck button again
            dialog.subdecks_cb.click()
            qtbot.wait(200)

            # The subdeck should not exist anymore
            assert aqt.mw.col.decks.by_name(subdeck_name) is None

    def _install_deck_with_subdeck_tag(
        self, install_ah_deck: InstallAHDeck, import_ah_note: ImportAHNote
    ) -> Tuple[str, int, uuid.UUID]:
        """Install a deck with a subdeck tag and return the full subdeck name."""
        ah_did = install_ah_deck()
        subdeck_name = "Subdeck"
        deck_name = config.deck_config(ah_did).name
        deck_name_as_tag = deck_name.replace(" ", "_")
        note_info = NoteInfoFactory.create(
            tags=[f"{SUBDECK_TAG}::{deck_name_as_tag}::{subdeck_name}"]
        )
        import_ah_note(ah_did=ah_did, note_data=note_info)
        anki_did = config.deck_config(ah_did).anki_id
        subdeck_full_name = f"{deck_name}::{subdeck_name}"
        return subdeck_full_name, anki_did, ah_did

    def test_change_destination_for_new_cards(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        install_ah_deck: InstallAHDeck,
        mocker: MockerFixture,
        mock_study_deck_dialog_with_cb: MockStudyDeckDialogWithCB,
    ):
        with anki_session_with_addon_data.profile_loaded():
            self._mock_dependencies(mocker)

            ah_did = install_ah_deck()

            # Mock get_deck_subscriptions to return the deck
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_subscriptions",
                return_value=[
                    DeckFactory.create(
                        ah_did=ah_did, anki_did=config.deck_config(ah_did).anki_id
                    )
                ],
            )

            # Mock the dialog that asks the user for the destination deck to choose
            # a new deck.
            new_destination_deck_name = "New Deck"
            install_ah_deck(anki_deck_name=new_destination_deck_name)
            new_home_deck_anki_id = aqt.mw.col.decks.id_for_name(
                new_destination_deck_name
            )
            mock_study_deck_dialog_with_cb(
                "ankihub.gui.decks_dialog.StudyDeckWithoutHelpButton",
                deck_name=new_destination_deck_name,
            )

            # Open the dialog
            dialog = DeckManagementDialog()
            dialog.display_subscribe_window()
            qtbot.wait(200)

            # Select the deck and click the Set Updates Destination button
            dialog.decks_list.setCurrentRow(0)
            qtbot.wait(200)

            dialog.set_new_cards_destination_btn.click()
            qtbot.wait(200)

            # Assert that the destination deck was updated
            assert config.deck_config(ah_did).anki_id == new_home_deck_anki_id

    def test_with_deck_not_installed(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        next_deterministic_id: Callable[[], int],
    ):
        with anki_session_with_addon_data.profile_loaded():
            self._mock_dependencies(mocker)

            ah_did = next_deterministic_uuid()
            anki_did = next_deterministic_id()
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_subscriptions",
                return_value=[DeckFactory.create(ah_did=ah_did, anki_did=anki_did)],
            )

            dialog = DeckManagementDialog()
            dialog.display_subscribe_window()

            assert dialog.decks_list.count() == 1

            # Select the deck from the list
            dialog.decks_list.setCurrentRow(0)
            qtbot.wait(200)

            assert hasattr(dialog, "deck_not_installed_label")

    def _mock_dependencies(self, mocker: MockerFixture) -> None:
        # Mock the config to return that the user is logged in
        mocker.patch.object(config, "is_logged_in", return_value=True)

        # Mock the ask_user function to always return True
        mocker.patch("ankihub.gui.operations.subdecks.ask_user", return_value=True)


class TestBuildSubdecksAndMoveCardsToThem:
    @pytest.mark.parametrize(
        # The tag comparison is case-insensitive
        "use_lower_case_subdeck_tags",
        [True, False],
    )
    def test_basic(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        use_lower_case_subdeck_tags: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            _, ah_did = install_sample_ah_deck()

            # add subdeck tags to notes
            nids = mw.col.find_notes("deck:Testdeck")
            note1 = mw.col.get_note(nids[0])
            subdeck_tag_prefix = (
                SUBDECK_TAG.lower() if use_lower_case_subdeck_tags else SUBDECK_TAG
            )
            note1.tags = [f"{subdeck_tag_prefix}::Testdeck"]
            note1.flush()

            note2 = mw.col.get_note(nids[1])
            note2.tags = [f"{subdeck_tag_prefix}::Testdeck::B::C"]
            note2.flush()

            # call the function that moves all cards in the deck to their subdecks
            build_subdecks_and_move_cards_to_them(ah_did)

            # assert that the decks were created and the cards of the notes were moved to them
            assert note1.cards()
            for card in note1.cards():
                assert mw.col.decks.name(card.did) == "Testdeck"

            assert note2.cards()
            for card in note2.cards():
                assert mw.col.decks.name(card.did) == "Testdeck::B::C"

    def test_empty_decks_get_deleted(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            _, ah_did = install_sample_ah_deck()

            # create empty decks
            mw.col.decks.add_normal_deck_with_name("Testdeck::empty::A")
            # assert that the empty decks were created to be sure
            assert mw.col.decks.id("Testdeck::empty", create=False)
            assert mw.col.decks.id("Testdeck::empty::A", create=False)

            # call the function that moves all cards in the deck to their subdecks
            build_subdecks_and_move_cards_to_them(ah_did)

            # assert that the empty decks were deleted
            assert mw.col.decks.id("Testdeck::empty", create=False) is None
            assert mw.col.decks.id("Testdeck::empty::A", create=False) is None

    def test_notes_not_moved_out_filtered_decks(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # Set the config to not suspend new cards of new notes.
            # This is needed because the filtered deck will be empty otherwise.
            config.public_config["suspend_new_cards_of_new_notes"] = "never"

            _, ah_did = install_sample_ah_deck()

            nids = mw.col.find_notes("deck:Testdeck")

            # create a filtered deck that will contain the cards of the imported deck
            filtered_deck = mw.col.sched.get_or_create_filtered_deck(DeckId(0))
            filtered_deck.name = "filtered deck"
            filtered_deck.config.search_terms.pop(0)
            filtered_deck.config.search_terms.append(
                FilteredDeckConfig.SearchTerm(
                    search="deck:Testdeck",
                    limit=100,
                    order=0,  # type: ignore
                )
            )
            mw.col.sched.add_or_update_filtered_deck(filtered_deck)
            filtered_deck_id = mw.col.decks.id("filtered deck", create=False)
            filtered_deck = mw.col.sched.get_or_create_filtered_deck(filtered_deck_id)

            # assign a subdeck tag to a note
            nids = mw.col.find_notes("deck:Testdeck")
            note = mw.col.get_note(nids[0])
            note.tags = [f"{SUBDECK_TAG}::Testdeck::B::C"]
            note.flush()

            # assert that the note is in the filtered deck to be safe
            assert note.cards()
            for card in note.cards():
                assert card.did == filtered_deck.id

            # call the function that moves all cards in the deck to their subdecks
            build_subdecks_and_move_cards_to_them(ah_did)

            # assert that only the odid of the cards of the note was changed
            assert note.cards()
            for card in note.cards():
                assert mw.col.decks.name(card.did) == "filtered deck"
                assert mw.col.decks.name(card.odid) == "Testdeck::B::C"

    def test_note_without_subdeck_tag_not_moved(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            _, ah_did = install_sample_ah_deck()

            # move cards of a note to the default deck
            nids = mw.col.find_notes("deck:Testdeck")
            note = mw.col.get_note(nids[0])
            mw.col.set_deck(note.card_ids(), 1)

            # call the function that moves all cards in the deck to their subdecks
            build_subdecks_and_move_cards_to_them(ah_did)

            # assert that the cards of the note were not moved because the note has no subdeck tag
            assert note.cards()
            for card in note.cards():
                assert card.did == 1


def test_create_copy_browser_action_does_not_copy_ah_nid(
    anki_session_with_addon_data: AnkiSession,
    ankihub_basic_note_type: Dict[str, Any],
    next_deterministic_uuid: Callable[[], uuid.UUID],
    qtbot: QtBot,
):
    # Run the entry point so that the changes to the create copy action are applied.
    entry_point.run()
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        # Create a note.
        note = mw.col.new_note(ankihub_basic_note_type)
        note["Front"] = "front"
        note["Back"] = "back"
        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(next_deterministic_uuid())
        mw.col.add_note(note, DeckId(1))

        # Use the browser context menu action to create a copy of the note.
        browser = Browser(mw)
        qtbot.addWidget(browser)
        browser.show()
        # ... Select the note.
        browser.form.tableView.selectRow(0)
        # ... And call the action.
        browser.on_create_copy()

        # Check that the ANKIHUB_NOTE_TYPE_FIELD_NAME field is empty.
        add_cards_dialog: AddCards = aqt.dialogs._dialogs["AddCards"][1]
        note = add_cards_dialog.editor.note
        assert note.fields == ["front", "back", ""]


def test_flatten_deck(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        _, ah_did = install_sample_ah_deck()

        subdeck_name = "Testdeck::A::B"
        mw.col.decks.add_normal_deck_with_name(subdeck_name)
        subdeck_id = mw.col.decks.id_for_name(subdeck_name)

        # move cards of a note to the default deck
        nids = mw.col.find_notes("deck:Testdeck")
        note = mw.col.get_note(nids[0])
        mw.col.set_deck(note.card_ids(), subdeck_id)

        # call the function that flattens the deck and removes all subdecks
        flatten_deck(ah_did)

        # assert that the cards of the note were moved back to the root deck
        # because the note has no subdeck tag
        assert note.cards()
        for card in note.cards():
            assert mw.col.decks.name(card.did) == "Testdeck"

        # assert that the subdecks were deleted
        assert mw.col.decks.by_name(subdeck_name) is None


def test_reset_local_changes_to_notes(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
    mock_client_get_note_type: MockClientGetNoteType,
    mocker: MockerFixture,
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        _, ah_did = install_sample_ah_deck()

        # ids of notes are from small_ankihub.txt
        basic_note_1 = mw.col.get_note(NoteId(1608240029527))
        basic_note_2 = mw.col.get_note(NoteId(1608240057545))

        # change the content of a note and move it to a different deck
        basic_note_1["Front"] = "changed"
        basic_note_1.flush()
        mw.col.set_deck(basic_note_1.card_ids(), 1)

        # delete a note
        mw.col.remove_notes([basic_note_2.id])

        # mock the client functions that are called to get the data needed for resetting local changes
        mocker.patch.object(AnkiHubClient, "get_protected_fields")
        mocker.patch.object(AnkiHubClient, "get_protected_tags")
        mock_client_get_note_type([note_type for note_type in mw.col.models.all()])

        # reset local changes
        nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
        reset_local_changes_to_notes(nids=nids, ah_did=ah_did)

        # assert that basic_note_1 was changed back is still in the deck it was moved to
        # (resetting local changes to notes should not move existing notes between decks as the
        # user might not want that)
        basic_note_1.load()
        assert basic_note_1["Front"] == "This is the front 1"
        assert basic_note_1.cards()
        for card in basic_note_1.cards():
            assert card.did == 1

        # assert that basic_note_2 was added back and is in the ankihub deck
        basic_note_2.load()
        assert basic_note_2["Front"] == "<p>This is the front 2 without review</p>"
        assert basic_note_2.cards()
        for card in basic_note_2.cards():
            assert mw.col.decks.name(card.did) == "Testdeck"


def test_migrate_profile_data_from_old_location(
    anki_session_with_addon_before_profile_support: AnkiSession,
    mocker: MockerFixture,
):
    anki_session = anki_session_with_addon_before_profile_support

    # mock update_decks_and_media so that the add-on doesn't try to download updates from AnkiHub
    mocker.patch("ankihub.gui.deck_updater.ah_deck_updater.update_decks_and_media")

    # run the entrypoint and load the profile to trigger the migration
    entry_point.run()
    with anki_session.profile_loaded():
        pass

    # Assert that the profile data was migrated
    assert {
        x.name
        for x in profile_files_path().glob("*")
        if not x.name.startswith("ankihub.db")
    } == {".private_config.json"}
    assert config.user() == "user1"
    assert len(config.deck_ids()) == 1

    # Assert the expected contents of the ankihub base folder
    assert set([x.name for x in ankihub_base_path().glob("*")]) == {
        str(TEST_PROFILE_ID),
        "ankihub.log",
    }


def test_profile_swap(
    anki_session_with_addon_data: AnkiSession,
    mocker: MockerFixture,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    anki_session = anki_session_with_addon_data

    # already exists
    PROFILE_1_NAME = "User 1"
    PROFILE_1_ID = TEST_PROFILE_ID
    # will be created in the test
    PROFILE_2_NAME = "User 2"
    PROFILE_2_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

    general_setup_mock = mocker.patch("ankihub.entry_point._general_setup")

    entry_point.run()

    # load the first profile and import a deck
    with anki_session.profile_loaded():
        mw = anki_session.mw

        assert profile_files_path() == ankihub_base_path() / str(PROFILE_1_ID)

        install_sample_ah_deck()

        # the database should contain the imported deck
        assert len(ankihub_db.ankihub_deck_ids()) == 1
        # the config should contain the deck subscription
        assert len(config.deck_ids()) == 1

    # create the second profile
    mw.pm.create(PROFILE_2_NAME)

    # load the second profile
    mw.pm.load(PROFILE_2_NAME)
    # monkey patch uuid4 so that the id of the second profile is known
    mocker.patch("uuid.uuid4", return_value=PROFILE_2_ID)
    with anki_session.profile_loaded():
        assert profile_files_path() == ankihub_base_path() / str(PROFILE_2_ID)
        # the database should be empty
        assert len(ankihub_db.ankihub_deck_ids()) == 0
        # the config should not conatin any deck subscriptions
        assert len(config.deck_ids()) == 0

    # load the first profile again
    mw.pm.load(PROFILE_1_NAME)
    with anki_session.profile_loaded():
        assert profile_files_path() == ankihub_base_path() / str(PROFILE_1_ID)
        # the database should contain the imported deck
        assert len(ankihub_db.ankihub_deck_ids()) == 1
        # the config should contain the deck subscription
        assert len(config.deck_ids()) == 1

    # assert that the general_setup function was only called once
    assert general_setup_mock.call_count == 1


def test_migrate_addon_data_from_old_location(
    anki_session_with_addon_data: AnkiSession,
):
    # Move the profile data to the old location and add a file to the folder
    old_profile_files_path = settings.user_files_path() / settings.get_anki_profile_id()
    shutil.move(settings.profile_files_path(), old_profile_files_path)
    (old_profile_files_path / "test").touch()

    assert not settings.profile_files_path().exists()  # sanity check

    # Start the add-on and load the profile to trigger the migration
    entry_point.run()
    with anki_session_with_addon_data.profile_loaded():
        pass

    # Assert that the profile data was migrated to the new location and the file was also moved
    assert not old_profile_files_path.exists()
    assert settings.profile_files_path().exists()
    assert (settings.profile_files_path() / "test").exists()


class TestDeckUpdater:
    def test_update_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        mocker: MockerFixture,
        mock_ankihub_sync_dependencies: None,
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Install a deck to be updated
            ah_did = install_ah_deck()

            # Mock client.get_deck_updates to return a note update
            note_info = import_ah_note(ah_did=ah_did)
            note_info.fields[0].value = "changed"

            latest_update = datetime.now()
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_updates",
                return_value=DeckUpdates(
                    latest_update=latest_update,
                    protected_fields={},
                    protected_tags=[],
                    notes=[note_info],
                ),
            )

            mocker.patch.object(
                AnkiHubClient,
                "get_deck_by_id",
                return_value=DeckFactory.create(ah_did=ah_did),
            )

            # Use the deck updater to update the deck
            ah_deck_updater.update_decks_and_media(
                ah_dids=[ah_did], start_media_sync=False
            )

            # Assert last_update_results are accurate
            deck_updates_results = ah_deck_updater.last_deck_updates_results()
            assert len(deck_updates_results) == 1
            deck_update_result = deck_updates_results[0]
            assert deck_update_result.ankihub_did == ah_did
            assert deck_update_result.updated_nids == [note_info.anki_nid]
            assert deck_update_result.created_nids == []
            assert deck_update_result.skipped_nids == []

            # Assert that the note was updated in Anki
            note = aqt.mw.col.get_note(NoteId(note_info.anki_nid))
            assert note["Front"] == "changed"

            # Assert that the note was updated in the add-on database
            note_info = ankihub_db.note_data(note.id)
            assert note_info.fields[0].value == "changed"

            # Assert that the last update time was updated in the config
            assert config.deck_config(ah_did).latest_update == latest_update

    @pytest.mark.parametrize(
        "initial_tags, incoming_optional_tags, expected_tags",
        [
            # An optional tag gets added
            (
                ["foo::bar"],
                ["AnkiHub_Optional::tag_group::test1"],
                ["foo::bar", "AnkiHub_Optional::tag_group::test1"],
            ),
            # Optional tag of current deck gets removed
            (
                ["AnkiHub_Optional::tag_group::test1"],
                [],
                [],
            ),
            # Optional tag of other deck extension is not removed
            (
                ["AnkiHub_Optional::other_tag_group::test1"],
                [],
                ["AnkiHub_Optional::other_tag_group::test1"],
            ),
            # Optional tag gets replaced
            (
                ["foo::bar", "AnkiHub_Optional::tag_group::test1"],
                ["AnkiHub_Optional::tag_group::test2"],
                ["foo::bar", "AnkiHub_Optional::tag_group::test2"],
            ),
        ],
    )
    def test_update_optional_tags(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        initial_tags: List[str],
        incoming_optional_tags: List[str],
        expected_tags: List[str],
        mocker: MockerFixture,
        mock_ankihub_sync_dependencies: None,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()

            # Create note with initial tags
            note_info = import_ah_note(ah_did=ah_did)
            note = aqt.mw.col.get_note(NoteId(note_info.anki_nid))
            note.tags = initial_tags
            aqt.mw.col.update_note(note)

            # Mock client to return a deck extension update with incoming_optional_tags
            latest_update = datetime.now()

            mocker.patch.object(
                AnkiHubClient,
                "get_deck_by_id",
                return_value=DeckFactory.create(ah_did=ah_did),
            )

            deck_extension = DeckExtensionFactory.create(
                ah_did=ah_did, tag_group_name="tag_group"
            )
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_extensions_by_deck_id",
                return_value=[deck_extension],
            )

            mocker.patch.object(
                AnkiHubClient,
                "get_deck_extension_updates",
                return_value=[
                    DeckExtensionUpdateChunk(
                        note_customizations=[
                            NoteCustomization(
                                ankihub_nid=note_info.ah_nid,
                                tags=incoming_optional_tags,
                            ),
                        ],
                        latest_update=latest_update,
                    ),
                ],
            )

            # Update the deck
            deck_updater = _AnkiHubDeckUpdater()
            deck_updater.update_decks_and_media(
                ah_dids=[ah_did], start_media_sync=False
            )

            # Assert that the note now has the expected tags
            note.load()
            assert set(note.tags) == set(expected_tags)

            # Assert that the deck extension info was saved in the config
            assert config.deck_extension_config(
                extension_id=deck_extension.id
            ) == DeckExtensionConfig(
                ah_did=ah_did,
                owner_id=deck_extension.owner_id,
                name=deck_extension.name,
                tag_group_name=deck_extension.tag_group_name,
                description=deck_extension.description,
                latest_update=latest_update,
            )

    @pytest.mark.parametrize(
        "current_relation, incoming_relation",
        [
            (UserDeckRelation.SUBSCRIBER, UserDeckRelation.MAINTAINER),
            (UserDeckRelation.MAINTAINER, UserDeckRelation.SUBSCRIBER),
            (UserDeckRelation.SUBSCRIBER, UserDeckRelation.SUBSCRIBER),
        ],
    )
    def test_user_relation_gets_updated_in_deck_config(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        mocker: MockerFixture,
        current_relation: UserDeckRelation,
        incoming_relation: UserDeckRelation,
        mock_ankihub_sync_dependencies: None,
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Install deck and set the current relation in the config
            ah_did = install_ah_deck()
            deck = DeckFactory.create(
                ah_did=ah_did,
                user_relation=current_relation,
            )
            config.update_deck(deck)

            # Mock client.get_deck_by_id to return the deck with the incoming relation
            deck = copy.deepcopy(deck)
            deck.user_relation = incoming_relation
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_by_id",
                return_value=deck,
            )

            # Update the deck
            deck_updater = _AnkiHubDeckUpdater()
            deck_updater.update_decks_and_media(
                ah_dids=[ah_did], start_media_sync=False
            )

            # Assert that the deck config was updated with the incoming relation
            assert config.deck_config(ah_did).user_relation == incoming_relation


class TestSyncWithAnkiHub:
    """Tests for the sync_with_ankihub operation."""

    @pytest.mark.parametrize(
        "subscribed_to_deck",
        [True, False],
    )
    @pytest.mark.qt_no_exception_capture
    def test_sync_uninstalls_unsubscribed_decks(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        mocker: MockerFixture,
        mock_client_methods_called_during_ankihub_sync: None,
        sync_with_ankihub: SyncWithAnkiHub,
        subscribed_to_deck: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # Install a deck
            anki_did, ah_did = install_sample_ah_deck()

            # Mock client methods
            deck = DeckFactory.create(ah_did=ah_did)
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_subscriptions",
                return_value=[deck] if subscribed_to_deck else [],
            )
            mocker.patch.object(AnkiHubClient, "get_deck_by_id", return_value=deck)

            # Set a fake token so that the sync is not skipped
            config.save_token("test_token")

            # Sync
            sync_with_ankihub()

            # Assert that the deck was uninstalled if the user is not subscribed to it,
            # else assert that it was not uninstalled
            assert config.deck_ids() == ([ah_did] if subscribed_to_deck else [])
            assert ankihub_db.ankihub_deck_ids() == (
                [ah_did] if subscribed_to_deck else []
            )

            mids = [
                mw.col.get_note(nid).mid for nid in mw.col.find_notes(f"did:{anki_did}")
            ]
            is_ankihub_note_type = [
                note_type_contains_field(
                    mw.col.models.get(mid), ANKIHUB_NOTE_TYPE_FIELD_NAME
                )
                for mid in mids
            ]
            assert (
                all(is_ankihub_note_type)
                if subscribed_to_deck
                else not any(is_ankihub_note_type)
            )

    def test_sync_updates_api_version_on_last_sync(
        self,
        anki_session_with_addon_data: AnkiSession,
        sync_with_ankihub: SyncWithAnkiHub,
        mock_ankihub_sync_dependencies: None,
    ):
        assert config._private_config.api_version_on_last_sync is None  # sanity check

        with anki_session_with_addon_data.profile_loaded():
            sync_with_ankihub()

        assert config._private_config.api_version_on_last_sync == API_VERSION

    @pytest.mark.parametrize(
        "is_for_anking_deck",
        [True, False],
    )
    def test_sync_applies_pending_notes_actions(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        mocker: MockerFixture,
        mock_ankihub_sync_dependencies: None,
        sync_with_ankihub: SyncWithAnkiHub,
        qtbot: QtBot,
        is_for_anking_deck: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Setup deck with a note that has a suspended card
            ah_did = install_ah_deck(
                ah_did=ANKING_DECK_ID if is_for_anking_deck else None
            )
            note_info = import_ah_note(
                ah_did=ah_did,
            )
            note = aqt.mw.col.get_note(NoteId(note_info.anki_nid))
            card = note.cards()[0]
            card.queue = QUEUE_TYPE_SUSPENDED
            aqt.mw.col.update_card(card)

            # Mock client methods
            deck = DeckFactory.create(ah_did=ah_did)
            mocker.patch.object(
                AnkiHubClient, "get_deck_subscriptions", return_value=[deck]
            )
            mocker.patch.object(AnkiHubClient, "get_deck_by_id", return_value=deck)

            # ... Mock get_pending_notes_actions_for_deck to return an unsuspend action for the note
            notes_action = NotesAction(
                action=NotesActionChoices.UNSUSPEND, note_ids=[note_info.ah_nid]
            )
            mocker.patch.object(
                AnkiHubClient,
                "get_pending_notes_actions_for_deck",
                return_value=[notes_action],
            )

            # Sync
            sync_with_ankihub()

            # Assert that the cards of the note get unsuspended if its the AnKing deck which was synced.
            # (We don't support notes actions for other decks yet.)
            def cards_of_note_are_unsuspended() -> bool:
                cards = note.cards()
                return all(not card.queue == QUEUE_TYPE_SUSPENDED for card in cards)

            if is_for_anking_deck:
                qtbot.wait_until(cards_of_note_are_unsuspended)
            else:
                qtbot.wait(500)
                assert not cards_of_note_are_unsuspended()

    def test_exception_is_not_backpropagated_to_caller(
        self, anki_session_with_addon_data: AnkiSession, mocker: MockerFixture
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Mock a client function which is called in sync_with_ankihub to raise an exception.
            exception_message = "test exception"

            mocker.patch.object(
                AnkiHubClient,
                "get_deck_subscriptions",
                side_effect=Exception(exception_message),
            )

            # Set up the on_done callback
            future: Optional[Future] = None

            def on_done(future_: Future) -> None:
                nonlocal future
                future = future_

            # Call sync_with_ankihub. This shouldn't raise an exception.
            ankihub_sync.sync_with_ankihub(on_done=on_done)

            # Assert that the future contains the exception and that it contains the expected message.
            assert future.exception().args[0] == exception_message

    @pytest.mark.qt_no_exception_capture
    def test_col_schema_on_full_sync_updated(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        mocker: MockerFixture,
        mock_client_methods_called_during_ankihub_sync: None,
        sync_with_ankihub: SyncWithAnkiHub,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            anki_did, ah_did = install_sample_ah_deck()

            deck = DeckFactory.create(ah_did=ah_did)
            mocker.patch.object(
                AnkiHubClient,
                "get_deck_subscriptions",
                return_value=[deck],
            )
            mocker.patch.object(AnkiHubClient, "get_deck_by_id", return_value=deck)

            config.save_token("test_token")

            sync_with_ankihub()
            assert not config.col_schema_on_full_sync()

            mocker.patch.object(
                AnkiHubClient,
                "get_deck_subscriptions",
                return_value=[],
            )

            sync_with_ankihub()
            assert config.col_schema_on_full_sync() == mw.col.db.scalar(
                "select scm from col"
            )


def test_uninstalling_deck_removes_related_deck_extension_from_config(
    anki_session_with_addon_data: AnkiSession, install_ah_deck: InstallAHDeck
):
    with anki_session_with_addon_data.profile_loaded():
        ah_did = install_ah_deck()
        deck_extension = DeckExtensionFactory.create(
            ah_did=ah_did,
        )
        config.create_or_update_deck_extension_config(deck_extension)

        # sanity check
        assert config.deck_extensions_ids_for_ah_did(ah_did) == [deck_extension.id]

        uninstall_deck(ah_did)
        assert config.deck_extensions_ids_for_ah_did(ah_did) == []


class TestAutoSync:
    def test_with_on_ankiweb_sync_config_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        mock_client_methods_called_during_ankihub_sync: None,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # Mock the syncs.
            self._mock_syncs_and_check_new_subscriptions(mocker)

            # Setup the auto sync.
            _setup_ankihub_sync_on_ankiweb_sync()

            # Set the auto sync config option.
            config.public_config["auto_sync"] = "on_ankiweb_sync"

            # Trigger the AnkiWeb sync.
            with qtbot.wait_callback() as callback:
                mw._sync_collection_and_media(after_sync=callback)

            # Assert that both syncs were called.
            assert self.check_and_install_new_deck_subscriptions_mock.call_count == 1
            assert self.udpate_decks_and_media_mock.call_count == 1
            assert self.ankiweb_sync_mock.call_count == 1

    def test_with_never_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # Mock the syncs.
            self._mock_syncs_and_check_new_subscriptions(mocker)

            # Setup the auto sync.
            _setup_ankihub_sync_on_ankiweb_sync()

            # Set the auto sync config option.
            config.public_config["auto_sync"] = "never"

            # Trigger the AnkiWeb sync.
            with qtbot.wait_callback() as callback:
                mw._sync_collection_and_media(after_sync=callback)

            # Assert that only the AnkiWeb sync was called.
            assert self.udpate_decks_and_media_mock.call_count == 0
            assert self.check_and_install_new_deck_subscriptions_mock.call_count == 0
            assert self.ankiweb_sync_mock.call_count == 1

    def test_with_on_startup_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        mock_client_methods_called_during_ankihub_sync: None,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # Mock the syncs.
            self._mock_syncs_and_check_new_subscriptions(mocker)

            # Setup the auto sync.
            _setup_ankihub_sync_on_ankiweb_sync()

            # Set the auto sync config option.
            config.public_config["auto_sync"] = "on_startup"

            # Trigger the AnkiWeb sync.
            with qtbot.wait_callback() as callback:
                mw._sync_collection_and_media(after_sync=callback)

            # Assert that both syncs were called.
            assert self.udpate_decks_and_media_mock.call_count == 1
            assert self.ankiweb_sync_mock.call_count == 1

            # Assert that the new deck subscriptions operation was called.
            self.check_and_install_new_deck_subscriptions_mock.call_count == 1

            # Trigger the AnkiWeb sync again.
            with qtbot.wait_callback() as callback:
                mw._sync_collection_and_media(after_sync=callback)

            # Assert that only the AnkiWeb sync was called the second time.
            assert self.udpate_decks_and_media_mock.call_count == 1
            assert self.ankiweb_sync_mock.call_count == 2

            assert self.check_and_install_new_deck_subscriptions_mock.call_count == 1

    def test_with_user_not_being_logged_in(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        mock_client_methods_called_during_ankihub_sync: None,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # Mock the syncs.
            self._mock_syncs_and_check_new_subscriptions(mocker)

            # Set the token to None
            mocker.patch.object(config, "token", return_value=None)

            display_login_mock = mocker.patch.object(AnkiHubLogin, "display_login")

            # Setup the auto sync.
            _setup_ankihub_sync_on_ankiweb_sync()

            # Set the auto sync config option.
            config.public_config["auto_sync"] = "on_ankiweb_sync"

            # Trigger the AnkiWeb sync.
            mw._sync_collection_and_media(after_sync=mocker.stub())
            qtbot.wait(500)

            # Assert that the login dialog was displayed.
            assert display_login_mock.call_count == 1

            # Assert that the he AnkiWeb sync was run
            assert self.ankiweb_sync_mock.call_count == 1

            # Assert that the AnkiHub sync was not run.
            assert self.check_and_install_new_deck_subscriptions_mock.call_count == 0
            assert self.udpate_decks_and_media_mock.call_count == 0

    def _mock_syncs_and_check_new_subscriptions(self, mocker: MockerFixture):
        # Mock the token so that the AnkiHub sync is not skipped.
        mocker.patch.object(config, "token", return_value="test_token")

        # Mock update_decks_and_media so it does nothing.
        self.udpate_decks_and_media_mock = mocker.patch.object(
            ah_deck_updater, "update_decks_and_media"
        )

        # Mock the AnkiWeb sync so it only calls its callback on the main thread.
        def run_callback_on_main(*args, **kwargs) -> None:
            on_done = kwargs["on_done"]
            aqt.mw.taskman.run_on_main(on_done)

        self.ankiweb_sync_mock = mocker.patch.object(
            aqt.sync, "sync_collection", side_effect=run_callback_on_main
        )
        # ... and reload aqt.main so the mock is used.
        importlib.reload(aqt.main)

        # Mock the aqt.mw.reset method which is called after the AnkiWeb sync to refresh Anki's UI.
        # Otherwise it causes exceptions in the qt event loop when it is called after Anki is closed.
        mocker.patch.object(aqt.mw, "reset")

        # Mock the new deck subscriptions operation to just call its callback.
        self.check_and_install_new_deck_subscriptions_mock = mocker.patch(
            "ankihub.gui.operations.ankihub_sync.check_and_install_new_deck_subscriptions"
        )
        self.check_and_install_new_deck_subscriptions_mock.side_effect = (
            lambda *args, **kwargs: kwargs["on_done"](future_with_result(None))
        )


class TestAutoSyncRateLimit:
    @pytest.mark.parametrize(
        "delay_between_syncs_in_seconds, expected_call_count",
        [
            # When the delay is less than the rate limit, the sync should be called only once.
            (0.0, 1),
            # When the delay is higher than the rate limit, the sync should be called twice.
            (SYNC_RATE_LIMIT_SECONDS + 0.1, 2),
        ],
    )
    def test_rate_limit(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        qtbot: QtBot,
        mock_ankihub_sync_dependencies,
        delay_between_syncs_in_seconds: float,
        expected_call_count: int,
    ):
        # Run the entry point so that the auto sync and rate limit is set up.
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            sync_with_ankihub_mock = mocker.patch(
                "ankihub.gui.auto_sync.sync_with_ankihub"
            )

            # Trigger the sync two times, with a delay in between.
            aqt.mw._sync_collection_and_media(lambda: None)
            qtbot.wait(int(delay_between_syncs_in_seconds * 1000))
            aqt.mw._sync_collection_and_media(lambda: None)

            # Let the tasks run.
            qtbot.wait(500)

            assert sync_with_ankihub_mock.call_count == expected_call_count


def test_optional_tag_suggestion_dialog(
    anki_session_with_addon_data: AnkiSession,
    qtbot: QtBot,
    mocker: MockerFixture,
    import_ah_note: ImportAHNote,
    next_deterministic_uuid,
):
    anki_session = anki_session_with_addon_data

    with anki_session.profile_loaded():
        # Create 3 notes
        ah_did = next_deterministic_uuid()
        notes: List[Note] = []
        note_infos: List[NoteInfo] = []
        for _ in range(3):
            note_info = import_ah_note(ah_did=ah_did)
            note = aqt.mw.col.get_note(NoteId(note_info.anki_nid))
            note_infos.append(note_info)
            notes.append(note)

        # The first note has an optional tag associated with a valid tag group
        notes[0].tags = [
            f"{TAG_FOR_OPTIONAL_TAGS}::VALID::tag1",
        ]
        notes[0].flush()

        # The second note has an optional tag associated with an invalid tag group
        notes[1].tags = [
            f"{TAG_FOR_OPTIONAL_TAGS}::INVALID::tag1",
        ]
        notes[1].flush()

        # The third note has no optional tags
        notes[2].tags = []
        notes[2].flush()

        # Mock client methods
        mocker.patch.object(
            AnkiHubClient,
            "get_deck_extensions",
            return_value=[],
        )

        mocker.patch.object(
            AnkiHubClient,
            "prevalidate_tag_groups",
            return_value=[
                TagGroupValidationResponse(
                    tag_group_name="VALID",
                    deck_extension_id=1,
                    success=True,
                    errors=[],
                ),
                TagGroupValidationResponse(
                    tag_group_name="INVALID",
                    deck_extension_id=2,
                    success=False,
                    errors=["error message"],
                ),
            ],
        )

        # Open the dialog
        dialog = OptionalTagsSuggestionDialog(
            parent=aqt.mw, nids=[note.id for note in notes]
        )
        dialog.show()

        qtbot.wait(500)

        # Assert that the dialog is in the correct state
        # Items are sorted alphabetically and tooltips contain error messages if the tag group is invalid.
        assert dialog.tag_group_list.count() == 2
        assert dialog.tag_group_list.item(0).text() == "INVALID"
        assert "error message" in dialog.tag_group_list.item(0).toolTip()
        assert dialog.tag_group_list.item(1).text() == "VALID"
        assert dialog.tag_group_list.item(1).toolTip() == ""
        assert dialog.submit_btn.isEnabled()

        suggest_optional_tags_mock = mocker.patch.object(
            AnkiHubClient,
            "suggest_optional_tags",
        )

        # Select the "VALID" tag group and click the submit button
        dialog.tag_group_list.item(1).setSelected(True)

        qtbot.mouseClick(dialog.submit_btn, Qt.MouseButton.LeftButton)
        qtbot.wait_until(lambda: suggest_optional_tags_mock.call_count == 1)

        # Assert that the suggest_optional_tags function was called with the correct arguments.
        # Suggestions should be created for all notes, even if they don't have optional tags.
        # (To make it possible to remove all optional tags from notes.)
        assert suggest_optional_tags_mock.call_args.kwargs == {
            "suggestions": [
                OptionalTagSuggestion(
                    tag_group_name="VALID",
                    deck_extension_id=1,
                    ah_nid=note_infos[0].ah_nid,
                    tags=["AnkiHub_Optional::VALID::tag1"],
                ),
                OptionalTagSuggestion(
                    tag_group_name="VALID",
                    deck_extension_id=1,
                    ah_nid=note_infos[1].ah_nid,
                    tags=[],
                ),
                OptionalTagSuggestion(
                    tag_group_name="VALID",
                    deck_extension_id=1,
                    ah_nid=note_infos[2].ah_nid,
                    tags=[],
                ),
            ],
            "auto_accept": False,
        }


@pytest.mark.qt_no_exception_capture
def test_reset_optional_tags_action(
    anki_session_with_addon_data: AnkiSession,
    qtbot: QtBot,
    mocker: MockerFixture,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    entry_point.run()

    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        _, ah_did = install_sample_ah_deck()

        config.create_or_update_deck_extension_config(
            DeckExtension(
                id=1,
                ah_did=ah_did,
                owner_id=1,
                name="test99",
                tag_group_name="test99",
                description="",
                user_relation=UserDeckExtensionRelation.SUBSCRIBER,
            )
        )

        # add a note with an optional tag that should be reset
        nids = mw.col.find_notes("")
        nid = nids[0]

        note = mw.col.get_note(nid)
        note.tags = [f"{TAG_FOR_OPTIONAL_TAGS}::test99::test1"]
        note.flush()

        # create other note that should not be affected by the reset
        other_note = mw.col.new_note(mw.col.models.by_name("Basic"))
        other_note.tags = [f"{TAG_FOR_OPTIONAL_TAGS}::test99::test2"]
        mw.col.add_note(other_note, DeckId(1))

        # mock the choose_list function to always return the first item
        choose_list_mock = mocker.patch(
            "ankihub.gui.browser.browser.choose_list",
            return_value=0,
        )

        # mock the ask_user function to always confirm the reset
        mocker.patch("ankihub.gui.browser.browser.ask_user", return_value=True)

        # mock the is_logged_in function to always return True
        is_logged_in_mock = mocker.patch.object(
            config,
            "is_logged_in",
            return_value=True,
        )

        # mock method of ah_deck_updater
        update_decks_and_media_mock = mocker.patch.object(
            ah_deck_updater,
            "update_decks_and_media",
        )

        # run the reset action
        browser: Browser = dialogs.open("Browser", mw)
        qtbot.wait(300)

        _on_reset_optional_tags_action(browser)
        qtbot.wait(300)

        # assert that the ui behaved as expected
        assert choose_list_mock.call_count == 1
        assert choose_list_mock.call_args.kwargs["choices"] == ["test99 (Testdeck)"]

        # assert that the note was reset
        note = mw.col.get_note(nid)
        assert note.tags == []

        assert is_logged_in_mock.call_count == 1
        assert update_decks_and_media_mock.call_count == 1

        # the other note should not be affected, because it is in a different deck
        assert mw.col.get_note(other_note.id).tags == [
            f"{TAG_FOR_OPTIONAL_TAGS}::test99::test2"
        ]


class TestMediaSyncMediaDownload:
    def test_download_media(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        mocker: MockerFixture,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            _, ah_did = install_sample_ah_deck()

            # Mock client to return a deck media update
            latest_media_update = datetime.now()
            deck_media = DeckMediaFactory.create(
                name="image.png",
                modified=latest_media_update,
                referenced_on_accepted_note=True,
                exists_on_s3=True,
                download_enabled=True,
            )
            get_deck_media_updates_mock = mocker.patch.object(
                AnkiHubClient,
                "get_deck_media_updates",
                return_value=[
                    DeckMediaUpdateChunk(
                        media=[deck_media], latest_update=latest_media_update
                    ),
                ],
            )

            # Mock the client method for downloading media
            download_media_mock = mocker.patch.object(AnkiHubClient, "download_media")

            # Start the media sync and wait for it to finish
            media_sync.start_media_download()
            qtbot.wait_until(lambda: media_sync._download_in_progress is False)

            # Assert the client methods were called with the correct arguments
            get_deck_media_updates_mock.assert_called_once_with(
                ah_did,
                since=None,
            )
            download_media_mock.assert_called_once_with(["image.png"], ah_did)

            # Assert that the deck media was added to the database
            assert ankihub_db.downloadable_media_names_for_ankihub_deck(ah_did) == {
                deck_media.name
            }
            assert ankihub_db.media_names_exist_for_ankihub_deck(
                ah_did=ah_did, media_names={deck_media.name}
            ) == {deck_media.name: True}

            # Assert that the latest media update time was updated in the config
            assert (
                config.deck_config(ankihub_did=ah_did).latest_media_update
                == latest_media_update
            )

    def test_download_media_with_no_updates(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        mocker: MockerFixture,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            _, ah_did = install_sample_ah_deck()

            # Mock client to return an empty deck media update
            get_deck_media_updates_mock = mocker.patch.object(
                AnkiHubClient,
                "get_deck_media_updates",
                return_value=[
                    DeckMediaUpdateChunk(media=[], latest_update=datetime.now())
                ],
            )

            # Mock the client method for downloading media
            download_media_mock = mocker.patch.object(AnkiHubClient, "download_media")

            # Start the media sync and wait for it to finish
            media_sync.start_media_download()
            qtbot.wait_until(lambda: media_sync._download_in_progress is False)

            # Assert the client methods were called with the correct arguments
            get_deck_media_updates_mock.assert_called_once_with(
                ah_did,
                since=None,
            )
            download_media_mock.assert_not_called()


@fixture
def mock_client_media_upload(mocker: MockerFixture) -> Iterator[Mock]:
    """Setup a temporary media folder and mock client methods used for uploading media.
    Returns a mock for the _upload_file_to_s3_with_reusable_presigned_url method,
    which takes a filepath argument for the file to upload.
    This fixture also mocks the os.remove function so that the file to upload is not deleted
    by the client.
    """
    upload_file_to_s3_with_reusable_presigned_url_mock = mocker.patch.object(
        AnkiHubClient, "_upload_file_to_s3_with_reusable_presigned_url"
    )
    mocker.patch.object(AnkiHubClient, "_get_presigned_url_for_multiple_uploads")
    mocker.patch.object(AnkiHubClient, "media_upload_finished")

    # Mock os.remove so the zip is not deleted
    mocker.patch("os.remove")

    # Create a temporary media folder and copy the test media files to it.
    # Patch the media folder path to point to the temporary folder.
    with tempfile.TemporaryDirectory() as tmp_dir:
        for file in (TEST_DATA_PATH / "media").glob("*"):
            shutil.copy(file, Path(tmp_dir) / file.name)

        mocker.patch("anki.media.MediaManager.dir", return_value=tmp_dir)

        yield upload_file_to_s3_with_reusable_presigned_url_mock


class TestSuggestionsWithMedia:
    def test_suggest_note_update_with_media(
        self,
        anki_session_with_addon_data: AnkiSession,
        mock_client_media_upload: Mock,
        import_ah_note: ImportAHNote,
        create_change_suggestion: CreateChangeSuggestion,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            note_data = import_ah_note()
            note = mw.col.get_note(NoteId(note_data.anki_nid))

            # Add media reference to a note
            media_file_name = "testfile_1.jpeg"
            note["Front"] = f'<img src="{media_file_name}">'
            note.flush()

            # Create a suggestion for the note
            create_change_suggestion_mock = create_change_suggestion(
                note, wait_for_media_upload=True
            )

            # Assert that the suggestion was created with the correct media file name
            expected_file_name = self._new_media_file_name(media_file_name)
            self._assert_media_names_on_note_and_suggestion_as_expected(
                note=note,
                suggestion_request_mock=create_change_suggestion_mock,
                expected_media_name=expected_file_name,
            )
            self._assert_media_name_in_zip_as_expected(
                upload_request_mock=mock_client_media_upload,  # type: ignore
                expected_media_name=expected_file_name,
            )

    def _new_media_file_name(self, file_name: str) -> str:
        """Return the file name the media file should have when the image was uploaded to S3."""
        media_dir = Path(aqt.mw.col.media.dir())
        media_file_path = media_dir / file_name
        suffix = (
            ".webp"
            if AnkiHubClient()._media_file_should_be_converted_to_webp(media_file_path)
            else media_file_path.suffix
        )
        result = md5_file_hash(media_file_path) + suffix
        return result

    def test_suggest_new_note_with_media(
        self,
        anki_session_with_addon_data: AnkiSession,
        mock_client_media_upload: Mock,
        ankihub_basic_note_type: NotetypeDict,
        create_new_note_suggestion: CreateNewNoteSuggestion,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # Add media reference to a note
            media_file_name = "testfile_1.jpeg"
            note = mw.col.new_note(ankihub_basic_note_type)
            note["Front"] = f'<img src="{media_file_name}">'
            mw.col.add_note(note, DeckId(1))

            # Create a suggestion for the note
            ah_did = ankihub_db.ankihub_did_for_anki_nid(note.id)
            create_new_note_suggestion_mock = create_new_note_suggestion(
                note=note, ah_did=ah_did, wait_for_media_upload=True
            )

            # Assert that the suggestion was created with the correct media file name
            expected_file_name = self._new_media_file_name(media_file_name)
            self._assert_media_names_on_note_and_suggestion_as_expected(
                note=note,
                suggestion_request_mock=create_new_note_suggestion_mock,
                expected_media_name=expected_file_name,
            )
            self._assert_media_name_in_zip_as_expected(
                upload_request_mock=mock_client_media_upload,  # type: ignore
                expected_media_name=expected_file_name,
            )

    def test_do_not_upload_files_which_already_exist_in_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        mock_client_media_upload: Mock,
        import_ah_note: ImportAHNote,
        create_change_suggestion: CreateChangeSuggestion,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            # Import a note with a media reference
            existing_media_name = "foo.mp3"
            import_ah_note(
                note_data=NoteInfoFactory.create(
                    fields=[
                        Field(name="Front", value="front", order=0),
                        Field(
                            name="Back", value=f"[sound:{existing_media_name}]", order=1
                        ),
                    ]
                )
            )

            # Create a note with a reference to the same media file
            note_data = import_ah_note()
            note = mw.col.get_note(NoteId(note_data.anki_nid))
            note["Front"] = f"[sound:{existing_media_name}]"
            note.flush()

            # Create a suggestion for the note
            create_change_suggestion_mock = create_change_suggestion(
                note=note, wait_for_media_upload=False
            )

            # Assert that the suggestion was created
            assert create_change_suggestion_mock.called_once

            # Assert the file was not uploaded to S3
            assert mock_client_media_upload.call_count == 0

    def test_with_file_not_existing_in_collection(
        self,
        anki_session_with_addon_data: AnkiSession,
        mock_client_media_upload: Mock,
        import_ah_note: ImportAHNote,
        create_change_suggestion: CreateChangeSuggestion,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            note_data = import_ah_note()
            note = mw.col.get_note(NoteId(note_data.anki_nid))

            # Add reference to a media file that does not exist locally to the note
            note_content = '<img src="this_file_is_not_in_the_local_collection.png">'
            note["Front"] = note_content
            note.flush()

            # Create a suggestion for the note
            create_change_suggestion_mock = create_change_suggestion(
                note=note, wait_for_media_upload=False
            )

            # Assert that the suggestion was created
            assert create_change_suggestion_mock.called_once

            # Assert the file was not uploaded to S3
            assert mock_client_media_upload.call_count == 0

            # Assert note content is unchanged
            note.load()
            assert note["Front"] == note_content

    def test_with_matching_file_existing_for_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        mock_client_media_upload: Mock,
        import_ah_note: ImportAHNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        create_change_suggestion: CreateChangeSuggestion,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            media_dir = Path(mw.col.media.dir())
            ah_did = next_deterministic_uuid()

            note_data = import_ah_note(ah_did=ah_did)

            # Two media files with the contents, one will be in the collection and the other in the database.
            media_file_in_db = "testfile_1.jpeg"
            media_file_in_collection = "testfile_1_copy.jpeg"
            media_file_in_db_path = TEST_DATA_PATH / "media" / media_file_in_db
            media_file_hash = md5_file_hash(media_file_in_db_path)

            # Add a deck media entry to the database
            ankihub_db.upsert_deck_media_infos(
                ankihub_did=ah_did,
                media_list=[
                    DeckMediaFactory.create(
                        name=media_file_in_db,
                        file_content_hash=media_file_hash,
                    )
                ],
            )

            # Add the media file copy to the collection
            shutil.copy(
                media_file_in_db_path,
                media_dir / media_file_in_collection,
            )

            # Create a suggestion for a note that references the media file in the collection
            note = mw.col.get_note(NoteId(note_data.anki_nid))
            note_content = f'<img src="{media_file_in_collection}">'
            note["Front"] = note_content
            note.flush()

            create_change_suggestion_mock = create_change_suggestion(
                note=note, wait_for_media_upload=False
            )

            # Assert that the suggestion was created.
            assert create_change_suggestion_mock.called_once  # type: ignore

            # Assert the file was not uploaded to S3.
            assert mock_client_media_upload.call_count == 0

            # Assert that the media reference was replaced with a reference to the existing
            # media file in the database with the same hash on the note and in the suggestion.
            self._assert_media_names_on_note_and_suggestion_as_expected(
                note=note,
                suggestion_request_mock=create_change_suggestion_mock,
                expected_media_name=media_file_in_db,
            )

            # Assert both media files exist in the collection.
            # The first one already existed and the second was created by copying the first one.
            assert (media_dir / media_file_in_collection).is_file()
            assert (media_dir / media_file_in_db).is_file()

    def _assert_media_names_on_note_and_suggestion_as_expected(
        self,
        note: Note,
        suggestion_request_mock: Mock,
        expected_media_name: str,
    ):
        # Assert that the media name in the note is as expected.
        note.load()
        media_name_in_note = list(local_media_names_from_html(note["Front"]))[0]
        assert media_name_in_note == expected_media_name

        # Assert that the media name in the suggestion is as expected.
        suggestion: Union[ChangeNoteSuggestion, NewNoteSuggestion] = None
        if "change_note_suggestion" in suggestion_request_mock.call_args.kwargs:
            suggestion = suggestion_request_mock.call_args.kwargs[
                "change_note_suggestion"
            ]
        else:
            suggestion = suggestion_request_mock.call_args.kwargs["new_note_suggestion"]

        first_field_value = suggestion.fields[0].value
        media_name_in_suggestion = list(local_media_names_from_html(first_field_value))[
            0
        ]
        assert media_name_in_suggestion == expected_media_name

    def _assert_media_name_in_zip_as_expected(
        self,
        upload_request_mock: Mock,
        expected_media_name: str,
    ) -> None:
        zipfile_name = upload_request_mock.call_args.kwargs["filepath"]
        media_dir = Path(aqt.mw.col.media.dir())
        path_to_created_zip_file: Path = media_dir / zipfile_name
        with ZipFile(path_to_created_zip_file, "r") as zfile:
            namelist = zfile.namelist()
            name_of_uploaded_media = namelist[0]

        assert name_of_uploaded_media == expected_media_name


class TestAddonInstallAndUpdate:
    def test_install_and_update_addon(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
    ):
        """This test does not install the latest version of the add-on. It just tests
        that we are not breaking the add-on update process somehow."""

        assert aqt.mw.addonManager.allAddons() == []

        # Install the add-on
        with anki_session_with_addon_data.profile_loaded():
            result = aqt.mw.addonManager.install(file=str(ANKIHUB_ANKIADDON_FILE))
            assert isinstance(result, InstallOk)
            assert aqt.mw.addonManager.allAddons() == ["ankihub"]

        # Udpate the add-on
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            result = aqt.mw.addonManager.install(file=str(ANKIHUB_ANKIADDON_FILE))
            assert isinstance(result, InstallOk)
            assert aqt.mw.addonManager.allAddons() == ["ankihub"]

        # Start Anki
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            assert aqt.mw.addonManager.allAddons() == ["ankihub"]
            qtbot.wait(1000)


def test_check_and_prompt_for_updates_on_main_window(
    anki_session: AnkiSession,
):
    # Just check that the function did not change between Anki versions and that it does not throw an exception
    # when called.
    with anki_session.profile_loaded():
        utils.check_and_prompt_for_updates_on_main_window()


# without this mark the test sometimes fails on cleanup
@pytest.mark.qt_no_exception_capture
class TestDebugModule:
    def test_setup_logging_for_sync_collection_and_media(
        self, anki_session: AnkiSession, mocker: MockerFixture
    ):
        # Test that the original AnkiQt._sync_collection_and_media method gets called
        # despite the monkeypatching we do in debug.py.
        with anki_session.profile_loaded():
            mw = anki_session.mw

            # Mock the AnkiWeb sync to do nothing
            mocker.patch.object(aqt.sync, "sync_collection")
            # ... and reload the main module so that the mock is used.
            importlib.reload(aqt.main)

            # Mock the sync_will_start hook so that we can check if it was called when the sync starts.
            sync_will_start_mock = mocker.patch.object(gui_hooks, "sync_will_start")

            _setup_logging_for_sync_collection_and_media()

            mw._sync_collection_and_media(after_sync=lambda: None)

            sync_will_start_mock.assert_called_once()

    def test_setup_logging_for_db_begin(
        self, anki_session: AnkiSession, mocker: MockerFixture
    ):
        with anki_session.profile_loaded():
            mw = anki_session.mw

            db_begin_mock = mocker.patch.object(mw.col._backend, "db_begin")

            _setup_logging_for_db_begin()

            if point_version() <= 66:
                # `db.begin` was removed in newer Anki versions
                mw.col.db.begin()  # type: ignore

                db_begin_mock.assert_called_once()

    def test_log_stack(self):
        # Test that the _log_stack function does not throw an exception when called.
        _log_stack("test")


@pytest.mark.parametrize(
    "ah_nid, was_deleted_from_webapp",
    [
        (
            # This note was deleted from the webapp and is the first from the list of notes
            # in deleted_notes_from_anking_deck.json
            uuid.UUID("66973dbb-3a7a-4153-a944-4aa1f77ebc02"),
            True,
        ),
        (
            uuid.UUID("00000000-0000-0000-0000-000000000000"),
            False,
        ),
    ],
)
def test_handle_notes_deleted_from_webapp(
    anki_session_with_addon_data: AnkiSession,
    import_ah_note: ImportAHNote,
    ah_nid: uuid.UUID,
    was_deleted_from_webapp: bool,
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        # Import the note
        note_data = import_ah_note(ah_nid=ah_nid)

        # Make sure that the note has been added to the ankihub db
        assert ankihub_db.ankihub_nid_exists(ah_nid)

    # Run the entry point and load the profile to trigger the handling of the deleted notes.
    entry_point.run()
    with anki_session_with_addon_data.profile_loaded():
        # Assert that the note has been deleted from the ankihub db if it was deleted from the webapp
        assert not ankihub_db.ankihub_nid_exists(ah_nid) == was_deleted_from_webapp

        # Assert that ankihub_id field of the note has been cleared if the note was deleted from the webapp
        note = mw.col.get_note(NoteId(note_data.anki_nid))
        assert (note[ANKIHUB_NOTE_TYPE_FIELD_NAME] == "") == was_deleted_from_webapp

        # Assert that the note has a ankihub deleted tag if it was deleted from the webapp
        assert (TAG_FOR_DELETED_NOTES in note.tags) == was_deleted_from_webapp


def test_upload_logs_and_data(
    anki_session_with_addon_data: AnkiSession,
    mocker: MockerFixture,
    qtbot: QtBot,
):
    with anki_session_with_addon_data.profile_loaded():
        file_copy_path = TEST_DATA_PATH / "ankihub_debug_info_copy.zip"
        key: Optional[str] = None

        def upload_logs_mock(*args, **kwargs):
            shutil.copy(kwargs["file"], file_copy_path)

            nonlocal key
            key = kwargs["key"]

        # Mock the client.upload_logs method
        mocker.patch.object(AnkiHubClient, "upload_logs", side_effect=upload_logs_mock)

        # Start the upload in the background and wait until it is finished.
        upload_logs_and_data_in_background()

        def upload_finished():
            return key is not None

        qtbot.wait_until(upload_finished)

    try:
        # Check the contents of the zip file
        with ZipFile(file_copy_path, "r") as zip_file:
            assert "ankihub.log" in zip_file.namelist()
            assert f"{settings.profile_files_path().name}/" in zip_file.namelist()
            assert "collection.anki2" in zip_file.namelist()

        # Check the key
        assert key.startswith("ankihub_addon_debug_info_")
        assert key.endswith(".zip")
    finally:
        file_copy_path.unlink(missing_ok=True)


class TestConfigDialog:
    def test_ankihub_menu_item_exists(self, anki_session_with_addon_data: AnkiSession):
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            # Assert that the Config menu item exists
            config_action = next(
                child
                for child in menu_state.ankihub_menu.children()
                if isinstance(child, QAction) and child.text() == "⚙️ Config"
            )
            assert config_action is not None

    def test_open_config_dialog(
        self, anki_session_with_addon_data: AnkiSession, qtbot: QtBot
    ):
        with anki_session_with_addon_data.profile_loaded():
            setup_config_dialog_manager()

            from ankihub.gui.ankiaddonconfig import ConfigManager, ConfigWindow

            # Open the config dialog (similar code to the one here):
            # https://github.com/ankipalace/ankihub_addon/blob/1c45c6e7f2075e3338b21bcf99430f9822ccc7cf/manager.py#L118
            config_dialog_manager: ConfigManager = get_config_dialog_manager()
            config_window = ConfigWindow(config_dialog_manager)
            for fn in config_dialog_manager.window_open_hook:
                fn(config_window)
            config_window.on_open()
            config_window.show()

            # Check that opening the dialog does not throw an exception
            qtbot.wait(500)


class TestFlashCardSelector:
    @pytest.mark.sequential
    @pytest.mark.parametrize(
        "deck_id, feature_flag_active, expected_button_exists",
        [
            (ANKING_DECK_ID, True, True),
            (ANKING_DECK_ID, False, False),
            (uuid.uuid4(), True, False),
        ],
    )
    def test_flashcard_selector_button_exists_for_anking_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        qtbot: QtBot,
        deck_id: uuid.UUID,
        set_feature_flag_state: SetFeatureFlagState,
        feature_flag_active: bool,
        expected_button_exists: bool,
    ):
        set_feature_flag_state(
            "show_flashcards_selector_button", is_active=feature_flag_active
        )

        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            install_ah_deck(ah_did=deck_id)

            deckbrowser_web: AnkiWebView = aqt.mw.deckBrowser.web
            aqt.mw.deckBrowser.refresh()

            qtbot.wait(500)
            with qtbot.wait_callback() as callback:
                deckbrowser_web.evalWithCallback(
                    f"document.getElementById('{FLASHCARD_SELECTOR_OPEN_BUTTON_ID}') !== null",
                    callback,
                )
            callback.assert_called_with(expected_button_exists)

    @pytest.mark.sequential
    def test_clicking_button_opens_flashcard_selector_dialog(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        qtbot: QtBot,
        set_feature_flag_state: SetFeatureFlagState,
        mocker: MockerFixture,
    ):
        set_feature_flag_state("show_flashcards_selector_button")

        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            mocker.patch.object(config, "token")

            install_ah_deck(ah_did=ANKING_DECK_ID)

            deckbrowser_web: AnkiWebView = aqt.mw.deckBrowser.web
            aqt.mw.deckBrowser.refresh()

            qtbot.wait(500)
            deckbrowser_web.eval(
                f"document.getElementById('{FLASHCARD_SELECTOR_OPEN_BUTTON_ID}').click()",
            )

            def flashcard_selector_opened():
                if FlashCardSelectorDialog.dialog is None:
                    return False

                dialog: FlashCardSelectorDialog = FlashCardSelectorDialog.dialog
                return dialog.isVisible()

            qtbot.wait_until(flashcard_selector_opened)

    @pytest.mark.sequential
    def test_clicking_button_twice_shows_existing_dialog_again(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        qtbot: QtBot,
        set_feature_flag_state: SetFeatureFlagState,
        mocker: MockerFixture,
    ):
        set_feature_flag_state("show_flashcards_selector_button")

        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            mocker.patch.object(config, "token")

            install_ah_deck(ah_did=ANKING_DECK_ID)

            deckbrowser_web: AnkiWebView = aqt.mw.deckBrowser.web
            aqt.mw.deckBrowser.refresh()

            qtbot.wait(500)
            deckbrowser_web.eval(
                f"document.getElementById('{FLASHCARD_SELECTOR_OPEN_BUTTON_ID}').click()",
            )

            def flashcard_selector_opened():
                if FlashCardSelectorDialog.dialog is None:
                    return False

                dialog: FlashCardSelectorDialog = FlashCardSelectorDialog.dialog
                return dialog.isVisible()

            qtbot.wait_until(flashcard_selector_opened)

            dialog = cast(FlashCardSelectorDialog, FlashCardSelectorDialog.dialog)
            dialog.close()

            qtbot.wait_until(lambda: not FlashCardSelectorDialog.dialog.isVisible())

            deckbrowser_web.eval(
                f"document.getElementById('{FLASHCARD_SELECTOR_OPEN_BUTTON_ID}').click()",
            )

            qtbot.wait_until(flashcard_selector_opened)

            assert FlashCardSelectorDialog.dialog == dialog

    def test_with_no_auth_token(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            dialog = FlashCardSelectorDialog.display(aqt.mw)

            def auth_failure_was_handled() -> bool:
                return not dialog and AnkiHubLogin._window.isVisible()

            qtbot.wait_until(auth_failure_was_handled)

    @pytest.mark.sequential
    def test_with_auth_failing(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            mocker.patch.object(config, "token")

            self._mock_load_url_to_show_page(mocker, body="Invalid token")

            dialog = FlashCardSelectorDialog.display(aqt.mw)

            def auth_failure_was_handled() -> bool:
                return not dialog.isVisible() and AnkiHubLogin._window.isVisible()

            qtbot.wait_until(auth_failure_was_handled)

    def test_view_in_web_browser_button(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mocker.patch.object(config, "token")

            dialog = FlashCardSelectorDialog.display(aqt.mw)

            openLink_mock = mocker.patch("ankihub.gui.webview.openLink")

            dialog.view_in_web_browser_button.click()

            openLink_mock.assert_called_once_with(
                url_flashcard_selector(ANKING_DECK_ID)
            )
            assert not dialog.isVisible()

    @pytest.mark.sequential
    def test_sync_notes_actions(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            mocker.patch.object(config, "token")

            fetch_and_apply_pending_notes_actions_for_deck = mocker.patch.object(
                ah_deck_updater,
                "fetch_and_apply_pending_notes_actions_for_deck",
            )

            # Mock the page so that it's loaded and we can run javascript on it
            self._mock_load_url_to_show_page(mocker, body="")

            dialog = FlashCardSelectorDialog.display(aqt.mw)

            dialog.web.eval(
                f"pycmd('{FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD} {uuid.uuid4()}')"
            )

            qtbot.wait_until(
                lambda: fetch_and_apply_pending_notes_actions_for_deck.called
            )

    def _mock_load_url_to_show_page(self, mocker: MockerFixture, body: str):
        original_load_url = aqt.webview.AnkiWebView.load_url

        def new_load_url(self, url: QUrl, *args, **kwargs):
            self = cast(AnkiWebView, self)
            # Check if the URL is the flashcard selector page.
            # This is necessary, because stdHtml relies on other load_url calls to load the page.
            if "flashcard-selector" in url.toString():
                return self.stdHtml(body)
            else:
                return original_load_url(self, url, *args, **kwargs)

        mocker.patch("aqt.webview.AnkiWebView.load_url", new=new_load_url)


def test_delete_ankihub_private_config_on_deckBrowser__delete_option(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
    qtbot: QtBot,
    mocker: MockerFixture,
):
    entry_point.run()

    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw

        anki_deck_id, ah_did = install_sample_ah_deck()

        mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
        deck_uuid = config.get_deck_uuid_by_did(anki_deck_id)

        assert len(mids) == 2
        assert mw.col.decks.count() == 2
        assert deck_uuid

        mocker.patch("ankihub.gui.deckbrowser.ask_user", return_value=True)

        unsubscribe_from_deck_mock = mocker.patch.object(
            AnkiHubClient, "unsubscribe_from_deck"
        )
        mw.deckBrowser._delete(anki_deck_id)
        unsubscribe_from_deck_mock.assert_called_once()

        # Assert that the deck was removed from the private config
        deck_uuid = config.get_deck_uuid_by_did(anki_deck_id)
        assert deck_uuid is None

        # Assert that the note type modifications were undone
        assert all(not note_type_contains_field(mw.col.models.get(mid)) for mid in mids)
        assert all(
            not re.search(
                ANKIHUB_TEMPLATE_SNIPPET_RE, mw.col.models.get(mid)["tmpls"][0]["afmt"]
            )
            for mid in mids
        )

        # Assert that the deck was removed from the AnkiHub database
        mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
        assert len(mids) == 0

        nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
        assert len(nids) == 0

        # Assert that the deck gets removed from the Anki database
        qtbot.wait_until(lambda: mw.col.decks.count() == 1)


def test_not_delete_ankihub_private_config_on_deckBrowser__delete_option(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
    qtbot: QtBot,
    mocker: MockerFixture,
):
    entry_point.run()

    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw

        anki_deck_id, _ = install_sample_ah_deck()

        deck_uuid = config.get_deck_uuid_by_did(anki_deck_id)

        assert mw.col.decks.count() == 2
        assert deck_uuid

        mocker.patch("ankihub.gui.deckbrowser.ask_user", return_value=False)

        mw.deckBrowser._delete(anki_deck_id)

        # Assert that the deck was not removed from the private config
        deck_uuid = config.get_deck_uuid_by_did(anki_deck_id)
        assert deck_uuid is not None

        # Assert that the deck gets removed from the Anki database
        qtbot.wait_until(lambda: mw.col.decks.count() == 1)


@pytest.mark.qt_no_exception_capture
class TestAHDBCheck:
    def test_with_nothing_missing(self, qtbot: QtBot, mocker: MockerFixture):
        with qtbot.wait_callback() as callback:
            check_ankihub_db(on_success=callback)

    @pytest.mark.parametrize(
        "user_confirms, deck_exists_on_ankihub",
        [
            (True, True),
            (True, False),
            (False, True),
            (False, False),
        ],
    )
    def test_with_deck_missing_from_config(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        import_ah_note: ImportAHNote,
        mock_download_and_install_deck_dependencies: MockDownloadAndInstallDeckDependencies,
        ankihub_basic_note_type: NotetypeDict,
        mocker: MockerFixture,
        qtbot: QtBot,
        user_confirms: bool,
        deck_exists_on_ankihub: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Install a deck (side effect of importing note)
            ah_did = next_deterministic_uuid()
            import_ah_note(ah_did=ah_did)

            # Remove deck from config
            config.remove_deck_and_its_extensions(ah_did)

            # Mock dependencies for downloading and installing deck
            deck = DeckFactory.create(ah_did=ah_did)
            notes_data = [NoteInfoFactory.create(mid=ankihub_basic_note_type["id"])]
            mocks = mock_download_and_install_deck_dependencies(
                deck, notes_data, ankihub_basic_note_type
            )

            # Mock get_deck_by_id to return 404 if deck_exists_on_ankihub==False
            if not deck_exists_on_ankihub:

                def raise_404(*args, **kwargs) -> None:
                    response_404 = Response()
                    response_404.status_code = 404
                    raise AnkiHubHTTPError(response=response_404)

                mocker.patch.object(
                    AnkiHubClient,
                    "get_deck_by_id",
                    side_effect=raise_404,
                )

            # Mock ask_user function
            mocker.patch.object(ah_db_check, "ask_user", return_value=user_confirms)

            # Run the db check
            with qtbot.wait_callback() as callback:
                check_ankihub_db(on_success=callback)

            if user_confirms and deck_exists_on_ankihub:
                # The deck was downloaded and installed, is now also in config
                assert mocks["get_deck_by_id"].call_count == 1
                assert config.deck_ids() == [ah_did]
            elif user_confirms and not deck_exists_on_ankihub:
                # The deck could't be installed because it doesn't exist, was uninstalled completely
                assert ankihub_db.ankihub_deck_ids() == []
            else:
                # User didn't confirm, nothing to do
                assert mocks["get_deck_by_id"].call_count == 0
