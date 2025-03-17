import copy
import os
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Protocol, Type
from unittest.mock import MagicMock, Mock

import aqt
import pytest
from anki.cards import CardId
from anki.consts import REVLOG_LRN
from anki.decks import DeckId
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from aqt.main import AnkiQt
from pytest import MonkeyPatch, fixture
from pytest_anki import AnkiSession
from pytest_mock import MockerFixture

from .factories import NoteInfoFactory

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client import NoteInfo
from ankihub.ankihub_client.ankihub_client import AnkiHubClient
from ankihub.ankihub_client.models import Deck, SuggestionType, UserDeckRelation
from ankihub.feature_flags import _setup_feature_flags
from ankihub.gui.media_sync import _AnkiHubMediaSync
from ankihub.gui.suggestion_dialog import SuggestionMetadata
from ankihub.main.importing import AnkiHubImporter
from ankihub.main.utils import modified_note_type
from ankihub.settings import BehaviorOnRemoteNoteDeleted, DeckConfig, config


@fixture
def next_deterministic_uuid() -> Callable[[], uuid.UUID]:
    """Returns a function that returns a new uuid.UUID each time it is called.
    The uuids are deterministic and are based on the number of times the function has been called.
    """
    counter = 0

    def _next_deterministic_uuid() -> uuid.UUID:
        nonlocal counter
        counter += 1
        return uuid.UUID(int=counter)

    return _next_deterministic_uuid


@fixture
def next_deterministic_id() -> Callable[[], int]:
    """Returns a function that returns a new int each time it is called.
    The ints are deterministic and are based on the number of times the function has been called.
    """
    counter = 0

    def _next_deterministic_id() -> int:
        nonlocal counter
        counter += 1
        return counter

    return _next_deterministic_id


@fixture
def ankihub_basic_note_type(anki_session_with_addon_data: AnkiSession) -> NotetypeDict:
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw
        result = create_or_get_ah_version_of_note_type(
            mw, mw.col.models.by_name("Basic")
        )
        return result


def create_or_get_ah_version_of_note_type(
    mw: AnkiQt, note_type: NotetypeDict
) -> NotetypeDict:
    note_type = copy.deepcopy(note_type)
    note_type["id"] = 0
    note_type["name"] = note_type["name"] + " (AnkiHub)"

    if model := mw.col.models.by_name(note_type["name"]):
        return model

    note_type = modified_note_type(note_type)
    mw.col.models.add_dict(note_type)
    return mw.col.models.by_name(note_type["name"])


class SetFeatureFlagState(Protocol):
    def __call__(self, feature_flag_name: str, is_active: bool = True) -> None:
        ...


@pytest.fixture
def set_feature_flag_state(monkeypatch: MonkeyPatch) -> SetFeatureFlagState:
    """Patches the AnkiHubClient.get_feature_flags method to return the desired value for
    the provided feature flag and reloads feature flags."""

    def set_feature_flag_state_inner(feature_flag_name, is_active=True) -> None:
        old_get_feature_flags = AnkiHubClient.get_feature_flags

        def new_get_feature_flags(*args, **kwargs) -> Dict[str, bool]:
            old_get_feature_flags_result = old_get_feature_flags(*args, **kwargs)
            old_get_feature_flags_result[feature_flag_name] = is_active
            return old_get_feature_flags_result

        monkeypatch.setattr(
            "ankihub.ankihub_client.ankihub_client.AnkiHubClient.get_feature_flags",
            new_get_feature_flags,
        )

        # this is needed so that the feature flags are reloaded for the feature_flags singleton
        _setup_feature_flags()

    return set_feature_flag_state_inner


class ImportAHNote(Protocol):
    def __call__(
        self,
        note_data: Optional[NoteInfo] = None,
        ah_nid: Optional[uuid.UUID] = None,
        mid: Optional[NotetypeId] = None,
        ah_did: Optional[uuid.UUID] = None,
        anki_did: Optional[DeckId] = None,
    ) -> NoteInfo:
        ...


@fixture
def import_ah_note(next_deterministic_uuid: Callable[[], uuid.UUID]) -> ImportAHNote:
    """Import a note into the Anki and AnkiHub databases and return the note info.
    The note type of the note is created in the Anki database if it does not exist yet.
    The default value for the note type is an AnkiHub version of the Basic note type.
    Can only be used in an anki_session_with_addon.profile_loaded() context.
    Use the import_ah_notes fixture if you want to import many notes at once.

    Parameters:
    Can be passed to override the default values of the note. When certain
    parameters are overwritten, the note type can become incompatible with the
    note, in this case an exception is raised.

    Purpose:
    Easily create notes in the Anki and AnkiHub databases without
    having to worry about creating note types, decks and the import process.
    """
    # All notes created by this fixture will be created in the same deck.
    default_ah_did = next_deterministic_uuid()
    deck_name = "test"

    def _import_ah_note(
        note_data: Optional[NoteInfo] = None,
        ah_nid: Optional[uuid.UUID] = None,
        mid: Optional[NotetypeId] = None,
        ah_did: Optional[uuid.UUID] = default_ah_did,
        anki_did: Optional[DeckId] = None,
    ) -> NoteInfo:
        if mid is None:
            ah_basic_note_type = create_or_get_ah_version_of_note_type(
                aqt.mw, aqt.mw.col.models.by_name("Basic")
            )
            mid = ah_basic_note_type["id"]

        if note_data is None:
            note_data = NoteInfoFactory.create()

        note_data.mid = mid

        if ah_nid:
            note_data.ah_nid = ah_nid

        # Check if note data is compatible with the note type.
        # For each field in note_data, check if there is a field in the note type with the same name.
        note_type = aqt.mw.col.models.get(mid)
        field_names_of_note_type = set(field["name"] for field in note_type["flds"])
        fields_are_compatible = all(
            field.name in field_names_of_note_type for field in note_data.fields
        )
        assert fields_are_compatible, (
            f"Note data is not compatible with the note type.\n"
            f"\tNote data: {note_data.fields}, note type: {field_names_of_note_type}"
        )

        if deck_config := config.deck_config(ah_did):
            suspend_new_cards_of_new_notes = deck_config.suspend_new_cards_of_new_notes
            suspend_new_cards_of_existing_notes = (
                deck_config.suspend_new_cards_of_existing_notes
            )
        else:
            suspend_new_cards_of_new_notes = (
                DeckConfig.suspend_new_cards_of_new_notes_default(ah_did)
            )
            suspend_new_cards_of_existing_notes = (
                DeckConfig.suspend_new_cards_of_existing_notes_default()
            )

        AnkiHubImporter().import_ankihub_deck(
            ankihub_did=ah_did,
            notes=[note_data],
            note_types={note_type["id"]: note_type},
            protected_fields={},
            protected_tags=[],
            deck_name=deck_name,
            is_first_import_of_deck=False,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
            anki_did=anki_did,
            suspend_new_cards_of_new_notes=suspend_new_cards_of_new_notes,
            suspend_new_cards_of_existing_notes=suspend_new_cards_of_existing_notes,
        )
        return note_data

    return _import_ah_note


class ImportAHNotes(Protocol):
    def __call__(
        self,
        note_infos: List[NoteInfo],
        ah_did: Optional[uuid.UUID] = None,
        anki_did: Optional[DeckId] = None,
    ) -> None:
        ...


@fixture
def import_ah_notes(next_deterministic_uuid: Callable[[], uuid.UUID]) -> ImportAHNotes:
    """Alternative to import_ah_note that imports multiple notes at once.
    Offers less flexibility than import_ah_note but is more efficient when importing multiple notes.
    """
    # All notes created by this fixture will be created in the same deck.
    default_ah_did = next_deterministic_uuid()
    deck_name = "test"

    def _import_ah_notes(
        note_infos: List[NoteInfo],
        ah_did: Optional[uuid.UUID] = default_ah_did,
        anki_did: Optional[DeckId] = None,
    ) -> None:
        assert len(note_infos) > 0, "note_infos must not be empty"
        assert (
            note_info.mid == note_infos[0].mid for note_info in note_infos
        ), "All notes must have the same note type"

        # Check if the note_infos are compatible with the note type.
        # For each field in note_data, check if there is a field in the note type with the same name.
        mid = note_infos[0].mid
        note_type = aqt.mw.col.models.get(NotetypeId(mid))
        assert note_type is not None, f"Note type with id {mid} does not exist."

        field_names_of_note_type = set(field["name"] for field in note_type["flds"])
        for note_info in note_infos:
            fields_are_compatible = all(
                field.name in field_names_of_note_type for field in note_info.fields
            )
            assert fields_are_compatible, (
                f"Note data is not compatible with the note type.\n"
                f"\tNote data: {note_info.fields}, note type: {field_names_of_note_type}"
            )

        if deck_config := config.deck_config(ah_did):
            suspend_new_cards_of_new_notes = deck_config.suspend_new_cards_of_new_notes
            suspend_new_cards_of_existing_notes = (
                deck_config.suspend_new_cards_of_existing_notes
            )
        else:
            suspend_new_cards_of_new_notes = (
                DeckConfig.suspend_new_cards_of_new_notes_default(ah_did)
            )
            suspend_new_cards_of_existing_notes = (
                DeckConfig.suspend_new_cards_of_existing_notes_default()
            )

        AnkiHubImporter().import_ankihub_deck(
            ankihub_did=ah_did,
            notes=note_infos,
            note_types={note_type["id"]: note_type},
            protected_fields={},
            protected_tags=[],
            deck_name=deck_name,
            is_first_import_of_deck=False,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
            anki_did=anki_did,
            suspend_new_cards_of_new_notes=suspend_new_cards_of_new_notes,
            suspend_new_cards_of_existing_notes=suspend_new_cards_of_existing_notes,
        )

    return _import_ah_notes


class ImportAHNoteType(Protocol):
    def __call__(
        self,
        note_type: Optional[NotetypeDict] = None,
        ah_did: Optional[uuid.UUID] = None,
        force_new: bool = False,
    ) -> NotetypeDict:
        ...


@pytest.fixture
def import_ah_note_type(
    next_deterministic_uuid: Callable[[], uuid.UUID],
    ankihub_basic_note_type: NotetypeDict,
) -> ImportAHNoteType:
    """Imports a note type into the AnkiHub DB and Anki. Returns the note type.
    You can optionally pass in a note type and/or an AnkiHub deck ID.
    If force_new is True, a new unique id will be generated for the note type.
    Otherwise, subsequent calls to this function that use the same note type won't create a new note type.
    """
    default_ah_did = next_deterministic_uuid()
    default_note_type = ankihub_basic_note_type

    def import_ah_note_type_inner(
        note_type: Optional[NotetypeDict] = None,
        ah_did: Optional[uuid.UUID] = None,
        force_new: bool = False,
    ) -> NotetypeDict:
        if note_type is None:
            note_type = copy.deepcopy(default_note_type)
        if ah_did is None:
            ah_did = default_ah_did

        if force_new:
            # Generate a new unique id for the note type
            new_mid = max(model["id"] for model in aqt.mw.col.models.all()) + 1
            note_type["id"] = new_mid

        importer = AnkiHubImporter()
        importer.import_ankihub_deck(
            deck_name="test",
            ankihub_did=ah_did,
            note_types={note_type["id"]: note_type},
            notes=[],
            protected_fields={},
            protected_tags=[],
            is_first_import_of_deck=False,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
            suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                ah_did
            ),
            suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
        )
        return note_type

    return import_ah_note_type_inner


class AddAnkiNote(Protocol):
    def __call__(
        self,
        note_type: Optional[NotetypeDict] = None,
        anki_did: Optional[DeckId] = None,
        anki_nid: Optional[NoteId] = None,
    ) -> Note:
        ...


@pytest.fixture
def add_anki_note() -> AddAnkiNote:
    """Creates a new note with the given note type and adds it to the given deck.
    If no note type is provided, the Basic note type is used.
    If no deck is provided, the default deck is used."""

    def add_note_inner(
        note_type: Optional[NotetypeDict] = None,
        anki_did: Optional[DeckId] = None,
        anki_nid: Optional[NoteId] = None,
    ) -> Note:
        if anki_did is None:
            anki_did = DeckId(1)

        if note_type is None:
            note_type = aqt.mw.col.models.by_name("Basic")

        note = aqt.mw.col.new_note(note_type)
        note[note.keys()[0]] = "some text"

        aqt.mw.col.add_note(note, DeckId(anki_did))

        if anki_nid:
            aqt.mw.col.db.execute(
                f"UPDATE notes SET id = {anki_nid} WHERE id = {note.id};"
            )
            aqt.mw.col.db.execute(
                f"UPDATE cards SET nid = {anki_nid} WHERE nid = {note.id};"
            )
        return note

    return add_note_inner


class InstallAHDeck(Protocol):
    def __call__(
        self,
        ah_did: Optional[uuid.UUID] = None,
        ah_deck_name: Optional[str] = None,
        anki_did: Optional[DeckId] = None,
        anki_deck_name: Optional[str] = None,
        has_note_embeddings: bool = False,
    ) -> uuid.UUID:
        ...


@pytest.fixture
def install_ah_deck(
    next_deterministic_uuid: Callable[[], uuid.UUID],
    next_deterministic_id: Callable[[], int],
    import_ah_note: ImportAHNote,
) -> InstallAHDeck:
    """Installs a deck with the given AnkiHub and Anki names and ids.
    The deck is imported and added to the private config.
    Returns the AnkiHub deck id."""

    def install_ah_deck_inner(
        ah_did: Optional[uuid.UUID] = None,
        ah_deck_name: Optional[str] = None,
        anki_did: Optional[DeckId] = None,
        anki_deck_name: Optional[str] = None,
        has_note_embeddings: bool = False,
    ) -> uuid.UUID:
        if not ah_did:
            ah_did = next_deterministic_uuid()
        if not anki_did:
            anki_did = DeckId(next_deterministic_id() + 1)  # 1 is the default deck
        if not ah_deck_name:
            ah_deck_name = f"Deck {ah_did}"
        if not anki_deck_name:
            anki_deck_name = ah_deck_name

        # Add deck to the config
        config.add_deck(
            name=ah_deck_name,
            ankihub_did=ah_did,
            anki_did=anki_did,
            user_relation=UserDeckRelation.SUBSCRIBER,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
            has_note_embeddings=has_note_embeddings,
        )

        # Create deck by importing a note for it
        import_ah_note(ah_did=ah_did, anki_did=anki_did)
        aqt.mw.col.decks.rename(aqt.mw.col.decks.get(anki_did), anki_deck_name)
        return ah_did

    return install_ah_deck_inner


class MockShowDialogWithCB(Protocol):
    def __call__(
        self,
        target_object: Any,
        button_index: Optional[int],
    ) -> MagicMock:
        ...


@pytest.fixture
def mock_show_dialog_with_cb(monkeypatch: MonkeyPatch) -> MockShowDialogWithCB:
    """Mocks ankihub.gui.utils.show_dialog to call the callback with the provided button index
    instead of showing the dialog."""

    def mock_show_dialog_with_cb_inner(
        target_object: Any,
        button_index: Optional[int],
    ) -> None:
        def show_dialog_mock(*args, **kwargs) -> MagicMock:
            kwargs["callback"](button_index),
            return MagicMock()

        monkeypatch.setattr(target_object, show_dialog_mock)

    return mock_show_dialog_with_cb_inner


class MockDownloadAndInstallDeckDependencies(Protocol):
    def __call__(
        self,
        deck: Deck,
        notes_data: List[NoteInfo],
        note_type: NotetypeDict,
    ) -> Dict[str, Mock]:
        ...


@pytest.fixture
def mock_download_and_install_deck_dependencies(
    monkeypatch: MonkeyPatch,
    mock_show_dialog_with_cb: MockShowDialogWithCB,
    mocker: MockerFixture,
) -> MockDownloadAndInstallDeckDependencies:
    """Mocks the dependencies of the download_and_install_deck function.
    deck: The deck that is downloaded and installed.
    notes_data: The notes of the deck.
    note_type: The note type of the notes of the deck.

    Returns a dictionary of mocked functions.
    """

    def mock_install_deck_dependencies(
        deck: Deck,
        notes_data: List[NoteInfo],
        note_type: NotetypeDict,
    ) -> Dict[str, Mock]:
        mocks: Dict[str, Mock] = dict()

        def add_mock(object, func_name: str, return_value: Any = None):
            mocks[func_name] = Mock()
            mocks[func_name].return_value = return_value
            monkeypatch.setattr(object, func_name, mocks[func_name])

        # Mock client functions
        add_mock(AnkiHubClient, "get_deck_by_id", deck)
        add_mock(AnkiHubClient, "download_deck", notes_data)
        add_mock(AnkiHubClient, "get_note_type", note_type)
        add_mock(AnkiHubClient, "get_protected_fields", {})
        add_mock(AnkiHubClient, "get_protected_tags", [])

        # Mock media sync
        add_mock(_AnkiHubMediaSync, "start_media_download")

        # Mock UI interactions
        mock_show_dialog_with_cb(
            "ankihub.gui.operations.new_deck_subscriptions.show_dialog", button_index=1
        )

        return mocks

    return mock_install_deck_dependencies


class MockMessageBoxWithCB(Protocol):
    def __call__(
        self,
        target_object: Any,
        button_index: int,
    ) -> None:
        ...


class MessageBoxMock:
    def __init__(self, button_index, *args, **kwargs):
        callback = kwargs["callback"]
        aqt.mw.taskman.run_in_background(task=lambda: callback(button_index))

    def setCheckBox(self, *args, **kwargs):
        pass


@pytest.fixture
def mock_message_box_with_cb(monkeypatch: MonkeyPatch) -> MockMessageBoxWithCB:
    """Mocks the aqt.utils.MessageBox dialog to call the callback with the provided button index
    instead of showing the dialog."""

    def mock_message_box_with_cb_inner(
        target_object: Any,
        button_index: int,
    ) -> None:
        monkeypatch.setattr(
            target_object,
            lambda *args, **kwargs: MessageBoxMock(
                button_index=button_index, *args, **kwargs  # type: ignore
            ),
        )

    return mock_message_box_with_cb_inner


def create_anki_deck(deck_name: str) -> DeckId:
    """Creates an Anki deck with the given name and returns the id."""
    deck = aqt.mw.col.decks.new_deck()
    deck.name = deck_name
    changes = aqt.mw.col.decks.add_deck(deck)
    return DeckId(changes.id)


def add_basic_anki_note_to_deck(anki_did: DeckId) -> None:
    """Adds a basic Anki note to the given deck."""
    note = aqt.mw.col.new_note(aqt.mw.col.models.by_name("Basic"))
    note["Front"] = "some text"
    aqt.mw.col.add_note(note, anki_did)


class MockStudyDeckDialogWithCB(Protocol):
    def __call__(
        self,
        target_object: Any,
        deck_name: str,
    ) -> None:
        ...


@fixture
def mock_study_deck_dialog_with_cb(
    monkeypatch: MonkeyPatch,
) -> MockStudyDeckDialogWithCB:
    """Mocks the aqt.studydeck.StudyDeck dialog to call the callback with the provided deck name
    instead of showing the dialog."""

    def mock_study_deck_dialog_inner(
        target_object: Any,
        deck_name: str,
    ) -> None:
        dialog_mock = Mock()

        def dialog_mock_side_effect(*args, **kwargs) -> None:
            callback = kwargs["callback"]
            cb_study_deck_mock = Mock()
            cb_study_deck_mock.name = deck_name
            callback(cb_study_deck_mock)

        dialog_mock.side_effect = dialog_mock_side_effect
        monkeypatch.setattr(
            target_object,
            dialog_mock,
        )

    return mock_study_deck_dialog_inner


class MockSuggestionDialog(Protocol):
    def __call__(
        self,
        user_cancels: bool,
        suggestion_type: SuggestionType = SuggestionType.UPDATED_CONTENT,
    ) -> None:
        ...


@pytest.fixture
def mock_suggestion_dialog(monkeypatch: MonkeyPatch) -> MockSuggestionDialog:
    """Mock the SuggestionDialog class to run the callback which is passed to its __init__ method
    on the main thread when the __init__ method is called.

    user_cancels: If True, the callback is called with None as the suggestion metadata.
        If False, the callback is called with a SuggestionMetadata object as the suggestion metadata.
    """

    def mock_suggestion_dialog_inner(
        user_cancels: bool,
        suggestion_type: SuggestionType = SuggestionType.UPDATED_CONTENT,
    ) -> None:
        suggestion_dialog_mock = Mock()

        def side_effect(*args, callback, **kwargs):
            if user_cancels:
                suggestion_metadata = None
            else:
                suggestion_metadata = SuggestionMetadata(
                    comment="test",
                    change_type=suggestion_type,
                )
            aqt.mw.taskman.run_on_main(lambda: callback(suggestion_metadata))
            return suggestion_dialog_mock

        suggestion_dialog_mock.side_effect = side_effect
        monkeypatch.setattr(
            "ankihub.gui.suggestion_dialog.SuggestionDialog", suggestion_dialog_mock
        )

    return mock_suggestion_dialog_inner


class LatestInstanceTracker:
    def __init__(self, mocker: MockerFixture):
        self.latest_instances: Dict[Type, Any] = {}
        self.mocker = mocker

    def track(self, cls: Type) -> None:
        original_init = cls.__init__
        latest_instances_alias = self.latest_instances

        def new_init(self, *args, **kwargs):
            latest_instances_alias[cls] = self
            original_init(self, *args, **kwargs)

        self.mocker.patch.object(cls, "__init__", new=new_init)

    def get_latest_instance(self, cls: Type) -> Any:
        return self.latest_instances.get(cls)


@pytest.fixture
def latest_instance_tracker(mocker: MockerFixture) -> LatestInstanceTracker:
    """Tracks the latest instances of classes that are passed to the track method.
    This allows you to access the latest instance of a class that was created.
    """
    return LatestInstanceTracker(mocker)


def record_review_for_anki_nid(
    anki_nid: NoteId,
    date_time: Optional[datetime] = None,
    revlog_type: int = REVLOG_LRN,
) -> None:
    """Adds a review for the note with the given anki_nid at the given date_time."""
    if date_time is None:
        date_time = datetime.now()

    cid = aqt.mw.col.get_note(anki_nid).card_ids()[0]
    record_review(cid, int(date_time.timestamp() * 1000), revlog_type=revlog_type)


def record_review(
    cid: CardId, review_time_ms: int, revlog_type: int = REVLOG_LRN
) -> None:
    aqt.mw.col.db.execute(
        "INSERT INTO revlog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        # the revlog table stores the timestamp in milliseconds
        review_time_ms,
        cid,
        1,
        1,
        1,
        1,
        1,
        1,
        revlog_type,
    )


def assert_datetime_equal_ignore_milliseconds(dt1: datetime, dt2: datetime) -> None:
    """Asserts that the two datetimes are equal, ignoring the milliseconds."""
    dt1 = dt1.replace(microsecond=dt1.microsecond // 1000 * 1000)
    dt2 = dt2.replace(microsecond=dt2.microsecond // 1000 * 1000)
    assert dt1 == dt2
