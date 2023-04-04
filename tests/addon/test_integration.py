import copy
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple
from unittest.mock import MagicMock, Mock, PropertyMock

import aqt
import pytest
from anki.cards import CardId
from anki.consts import QUEUE_TYPE_SUSPENDED
from anki.decks import DeckId, FilteredDeckConfig
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.sync import SyncOutput
from aqt import AnkiQt, dialogs, gui_hooks
from aqt.addcards import AddCards
from aqt.addons import InstallOk
from aqt.browser import Browser
from aqt.importing import AnkiPackageImporter
from aqt.qt import Qt
from aqt.sync import sync_collection
from pytest import MonkeyPatch, fixture
from pytest_anki import AnkiSession
from pytestqt.qtbot import QtBot  # type: ignore
from requests_mock import Mocker

from ..factories import NoteInfoFactory
from ..fixtures import create_or_get_ah_version_of_note_type
from .conftest import TEST_PROFILE_ID

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub import entry_point
from ankihub.addons import (
    _change_file_permissions_of_addon_files,
    _maybe_change_file_permissions_of_addon_files,
    _with_disabled_log_file_handler,
)
from ankihub.ankihub_client import (
    ANKIHUB_DATETIME_FORMAT_STR,
    AnkiHubClient,
    AnkiHubRequestError,
    ChangeNoteSuggestion,
    Deck,
    DeckExtensionUpdateChunk,
    Field,
    NewNoteSuggestion,
    NoteCustomization,
    NoteInfo,
    OptionalTagSuggestion,
    SuggestionType,
    TagGroupValidationResponse,
    transform_notes_data,
)
from ankihub.auto_sync import setup_ankihub_sync_on_ankiweb_sync
from ankihub.common_utils import IMG_NAME_IN_IMG_TAG_REGEX
from ankihub.db import ankihub_db, attached_ankihub_db
from ankihub.debug import (
    _log_stack,
    _setup_logging_for_db_begin,
    _setup_logging_for_sync_collection_and_media,
    _setup_sentry_reporting_for_error_on_addon_update,
    _user_files_context_dict,
)
from ankihub.exporting import to_note_data
from ankihub.gui import utils
from ankihub.gui.browser import (
    ModifiedAfterSyncSearchNode,
    NewNoteSearchNode,
    SuggestionTypeSearchNode,
    UpdatedInTheLastXDaysSearchNode,
    _on_reset_optional_tags_action,
    custom_columns,
)
from ankihub.gui.custom_search_nodes import UpdatedSinceLastReviewSearchNode
from ankihub.gui.decks import SubscribedDecksDialog
from ankihub.gui.editor import _on_suggestion_button_press, _refresh_buttons
from ankihub.gui.optional_tag_suggestion_dialog import OptionalTagsSuggestionDialog
from ankihub.importing import (
    AnkiHubImporter,
    adjust_note_types,
    reset_note_types_of_notes,
)
from ankihub.note_conversion import (
    ADDON_INTERNAL_TAGS,
    ANKI_INTERNAL_TAGS,
    TAG_FOR_OPTIONAL_TAGS,
    TAG_FOR_PROTECTING_FIELDS,
)
from ankihub.register_decks import create_collaborative_deck, modify_note_type
from ankihub.reset_changes import reset_local_changes_to_notes
from ankihub.settings import (
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    AnkiHubCommands,
    DeckExtension,
    DeckExtensionConfig,
    config,
    profile_files_path,
)
from ankihub.subdecks import (
    SUBDECK_TAG,
    build_subdecks_and_move_cards_to_them,
    flatten_deck,
)
from ankihub.suggestions import (
    suggest_new_note,
    suggest_note_update,
    suggest_notes_in_bulk,
)
from ankihub.sync import AnkiHubSync, ah_sync
from ankihub.utils import (
    ANKIHUB_TEMPLATE_SNIPPET_RE,
    all_dids,
    get_note_types_in_deck,
    note_type_contains_field,
)

SAMPLE_MODEL_ID = NotetypeId(1656968697414)
TEST_DATA_PATH = Path(__file__).parent.parent / "test_data"
SAMPLE_DECK_APKG = TEST_DATA_PATH / "small.apkg"
ANKIHUB_SAMPLE_DECK_APKG = TEST_DATA_PATH / "small_ankihub.apkg"
SAMPLE_NOTES_DATA = eval((TEST_DATA_PATH / "small_ankihub.txt").read_text())

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
    def _install_sample_ah_deck():
        # Can only be used in an anki_session_with_addon.profile_loaded() context

        ah_did = next_deterministic_uuid()
        mw = anki_session_with_addon_data.mw
        anki_did = import_sample_ankihub_deck(mw, ankihub_did=ah_did)
        config.save_subscription(name="Testdeck", ankihub_did=ah_did, anki_did=anki_did)
        return anki_did, ah_did

    return _install_sample_ah_deck


def import_sample_ankihub_deck(
    mw: aqt.AnkiQt, ankihub_did: uuid.UUID, assert_created_deck=True
) -> DeckId:
    import_note_types_for_sample_deck(mw)

    # import the deck from the notes data
    dids_before_import = all_dids()
    importer = AnkiHubImporter()
    local_did = importer._import_ankihub_deck_inner(
        ankihub_did=ankihub_did,
        notes_data=ankihub_sample_deck_notes_data(),
        deck_name="Testdeck",
        protected_fields={},
        protected_tags=[],
        remote_note_types={},
    ).anki_did
    new_dids = all_dids() - dids_before_import

    if assert_created_deck:
        assert len(new_dids) == 1
        assert local_did == list(new_dids)[0]

    return local_did


class MakeAHNote(Protocol):
    def __call__(
        self,
        ankihub_nid: uuid.UUID = None,
        note_type_id: Optional[NotetypeId] = None,
        generate_anki_id: bool = False,
    ) -> Note:
        ...


@fixture
def make_ah_note(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    next_deterministic_id: Callable[[], int],
) -> MakeAHNote:
    # Can only be used in an anki_session_with_addon.profile_loaded() context

    def _make_ah_note(
        ankihub_nid: uuid.UUID = None,
        note_type_id: Optional[NotetypeId] = None,
        generate_anki_id: bool = False,
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
        if generate_anki_id:
            note.id = NoteId(next_deterministic_id())

        # fields of the note will be set to "old <field_name>"
        # except for the ankihub note _type field (if it exists) which will be set to the ankihub nid
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
    notes_data_raw = transform_notes_data(SAMPLE_NOTES_DATA)
    result = [NoteInfo.from_dict(x) for x in notes_data_raw]
    return result


def test_entry_point(anki_session_with_addon_data: AnkiSession, qtbot: QtBot):
    entry_point.run()
    with anki_session_with_addon_data.profile_loaded():
        qtbot.wait(1000)

    # this test is just to make sure the entry point doesn't crash
    # and that the add-on doesn't crash on Anki startup


def test_editor(
    anki_session_with_addon_data: AnkiSession,
    requests_mock: Mocker,
    monkeypatch: MonkeyPatch,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    install_sample_ah_deck: InstallSampleAHDeck,
    disable_image_support_feature_flag,
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        install_sample_ah_deck()

        # mock the dialog so it doesn't block the testq
        monkeypatch.setattr(
            "ankihub.gui.suggestion_dialog.SuggestionDialog.exec", Mock()
        )

        add_cards_dialog: AddCards = dialogs.open("AddCards", mw)
        editor = add_cards_dialog.editor

        # test a new note suggestion
        editor.note = mw.col.new_note(mw.col.models.by_name("Basic (Testdeck / user1)"))

        note_1_ah_nid = next_deterministic_uuid()

        monkeypatch.setattr("ankihub.exporting.uuid.uuid4", lambda: note_1_ah_nid)

        requests_mock.post(
            f"{config.api_url}/notes/{note_1_ah_nid}/suggestion/",
            status_code=201,
            json={},
        )

        _refresh_buttons(editor)
        assert editor.ankihub_command == AnkiHubCommands.NEW.value  # type: ignore
        _on_suggestion_button_press(editor)

        # test a change note suggestion
        note = mw.col.get_note(mw.col.find_notes("")[0])
        editor.note = note

        note_2_ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)

        requests_mock.post(
            f"{config.api_url}/notes/{note_2_ah_nid}/suggestion/",
            status_code=201,
            json={},
        )

        _refresh_buttons(editor)
        assert editor.ankihub_command == AnkiHubCommands.CHANGE.value  # type: ignore

        # this should not trigger a suggestion because the note has not been changed
        _on_suggestion_button_press(editor)
        assert requests_mock.call_count == 0

        # change the front of the note
        note["Front"] = "new front"
        note.flush()

        # this should trigger a suggestion because the note has been changed
        _on_suggestion_button_press(editor)

        # mocked requests: f"{config.api_url_base}/notes/{notes_2_ah_nid}/suggestion/" and
        # request to check feature flags
        assert requests_mock.call_count == 2


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
    monkeypatch: MonkeyPatch,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    enable_image_support_feature_flag,
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
        upload_deck_mock = Mock()
        upload_deck_mock.return_value = ah_did
        ah_nid = next_deterministic_uuid()
        with monkeypatch.context() as m:
            m.setattr(
                "ankihub.ankihub_client.AnkiHubClient.upload_deck", upload_deck_mock
            )
            m.setattr("uuid.uuid4", lambda: ah_nid)
            create_collaborative_deck(deck_name, private=False)

        # re-load note to get updated note.mid
        note.load()

        # check that the client method was called with the correct data
        expected_note_types_data = [mw.col.models.get(note.mid)]
        expected_note_data = NoteInfo(
            ankihub_note_uuid=ah_nid,
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
        assert (
            ankihub_db.scalar(
                "SELECT mod from notes WHERE ankihub_note_id = ?", str(ah_nid)
            )
            == note.mod
        )


def test_get_deck_by_id(
    requests_mock: Mocker, next_deterministic_uuid: Callable[[], uuid.UUID]
):
    client = AnkiHubClient(local_media_dir_path=Path("/tmp/ankihub_media"))

    # test get deck by id
    ankihub_deck_uuid = next_deterministic_uuid()
    date_time = datetime.now(tz=timezone.utc)
    expected_data = {
        "id": str(ankihub_deck_uuid),
        "name": "test",
        "owner": 1,
        "anki_id": 1,
        "csv_last_upload": date_time.strftime(ANKIHUB_DATETIME_FORMAT_STR),
        "csv_notes_filename": "test.csv",
    }

    requests_mock.get(
        f"{config.api_url}/decks/{ankihub_deck_uuid}/", json=expected_data
    )
    deck_info = client.get_deck_by_id(ankihub_deck_uuid=ankihub_deck_uuid)  # type: ignore
    assert deck_info == Deck(
        ankihub_deck_uuid=ankihub_deck_uuid,
        anki_did=1,
        owner=True,
        name="test",
        csv_last_upload=date_time,
        csv_notes_filename="test.csv",
    )

    # test get deck by id unauthenticated
    requests_mock.get(f"{config.api_url}/decks/{ankihub_deck_uuid}/", status_code=403)

    try:
        client.get_deck_by_id(ankihub_deck_uuid=ankihub_deck_uuid)  # type: ignore
    except AnkiHubRequestError as e:
        exc = e
    assert exc is not None and exc.response.status_code == 403


def test_suggest_note_update(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
    monkeypatch: MonkeyPatch,
    disable_image_support_feature_flag,
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

        # Suggest the changes
        create_change_note_suggestion_mock = MagicMock()
        monkeypatch.setattr(
            "ankihub.ankihub_client.AnkiHubClient.create_change_note_suggestion",
            create_change_note_suggestion_mock,
        )

        suggest_note_update(
            note=note,
            change_type=SuggestionType.NEW_CONTENT,
            comment="test",
        )

        # Check that the correct suggestion was created
        create_change_note_suggestion_mock.assert_called_once_with(
            change_note_suggestion=ChangeNoteSuggestion(
                anki_nid=note.id,
                ankihub_note_uuid=ankihub_db.ankihub_nid_for_anki_nid(note.id),
                change_type=SuggestionType.NEW_CONTENT,
                fields=[Field(name="Front", value="updated", order=0)],
                added_tags=["added"],
                removed_tags=["removed"],
                comment="test",
            ),
            auto_accept=False,
        )


def test_suggest_new_note(
    anki_session_with_addon_data: AnkiSession,
    requests_mock: Mocker,
    install_sample_ah_deck: InstallSampleAHDeck,
    disable_image_support_feature_flag,
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
            )
        except AnkiHubRequestError as e:
            exc = e
        assert exc is not None and exc.response.status_code == 403


def test_suggest_notes_in_bulk(
    anki_session_with_addon_data: AnkiSession,
    monkeypatch: MonkeyPatch,
    install_sample_ah_deck: InstallSampleAHDeck,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    disable_image_support_feature_flag,
):
    anki_session = anki_session_with_addon_data
    bulk_suggestions_method_mock = MagicMock()
    monkeypatch.setattr(
        "ankihub.ankihub_client.AnkiHubClient.create_suggestions_in_bulk",
        bulk_suggestions_method_mock,
    )
    with anki_session.profile_loaded():
        mw = anki_session.mw

        anki_did, ah_did = install_sample_ah_deck()

        # add a new note
        new_note = mw.col.new_note(mw.col.models.by_name("Basic (Testdeck / user1)"))
        mw.col.add_note(new_note, deck_id=anki_did)

        CHANGED_NOTE_ID = NoteId(1608240057545)
        changed_note = mw.col.get_note(CHANGED_NOTE_ID)
        changed_note["Front"] = "changed front"
        changed_note.tags += ["a"]
        changed_note.flush()

        # suggest two notes, one new and one updated, check if the client method was called with the correct arguments
        nids = [changed_note.id, new_note.id]
        notes = [mw.col.get_note(nid) for nid in nids]
        # also add one optional tag to each one of them to verify that the optional tags are not sent
        for note in notes:
            note.tags = list(
                set(note.tags)
                | set([f"{TAG_FOR_OPTIONAL_TAGS}::TAG_GROUP::OptionalTag"])
            )
        mw.col.update_notes(notes)

        new_note_ah_id = next_deterministic_uuid()
        with monkeypatch.context() as m:
            m.setattr("uuid.uuid4", lambda: new_note_ah_id)
            suggest_notes_in_bulk(
                notes=notes,
                auto_accept=False,
                change_type=SuggestionType.NEW_CONTENT,
                comment="test",
            )

        assert bulk_suggestions_method_mock.call_count == 1
        assert bulk_suggestions_method_mock.call_args.kwargs == {
            "change_note_suggestions": [
                ChangeNoteSuggestion(
                    ankihub_note_uuid=uuid.UUID("67f182c2-7306-47f8-aed6-d7edb42cd7de"),
                    anki_nid=CHANGED_NOTE_ID,
                    fields=[
                        Field(
                            name="Front",
                            order=0,
                            value="changed front",
                        ),
                    ],
                    added_tags=["a"],
                    removed_tags=[],
                    comment="test",
                    change_type=SuggestionType.NEW_CONTENT,
                ),
            ],
            "new_note_suggestions": [
                NewNoteSuggestion(
                    ankihub_note_uuid=new_note_ah_id,
                    anki_nid=new_note.id,
                    fields=[
                        Field(name="Front", order=0, value=""),
                        Field(name="Back", order=1, value=""),
                    ],
                    tags=[],
                    guid=new_note.guid,
                    comment="test",
                    ankihub_deck_uuid=ah_did,
                    note_type_name="Basic (Testdeck / user1)",
                    anki_note_type_id=1657023668893,
                ),
            ],
            "auto_accept": False,
        }


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
        adjust_note_types(remote_note_types)

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
        reset_note_types_of_notes(nid_mid_pairs)

        assert mw.col.get_note(note.id).mid == cloze["id"]


class TestAnkiHubImporter:
    def test_import_new_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        from aqt import mw

        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():

            # import the apkg to get the note types, then delete the deck
            file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
            importer = AnkiPackageImporter(mw.col, file)
            importer.run()
            mw.col.decks.remove([mw.col.decks.id_for_name("Testdeck")])

            ankihub_deck_uuid = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer._import_ankihub_deck_inner(
                ankihub_did=ankihub_deck_uuid,
                notes_data=ankihub_sample_deck_notes_data(),
                deck_name="test",
                remote_note_types={},
                protected_fields={},
                protected_tags=[],
            )
            anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            assert (
                len(new_dids) == 1
            )  # we have no mechanism for importing subdecks from a csv yet, so ti will be just onen deck
            assert anki_did == list(new_dids)[0]

            assert len(import_result.created_nids) == 3
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(
                ankihub_deck_uuid=ankihub_deck_uuid
            )

    def test_import_existing_deck_1(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        from aqt import mw

        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():

            # import the apkg
            file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
            importer = AnkiPackageImporter(mw.col, file)
            importer.run()
            existing_did = mw.col.decks.id_for_name("Testdeck")

            ankihub_deck_uuid = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer._import_ankihub_deck_inner(
                ankihub_did=ankihub_deck_uuid,
                notes_data=ankihub_sample_deck_notes_data(),
                deck_name="test",
                remote_note_types={},
                protected_fields={},
                protected_tags=[],
            )
            anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            assert not new_dids
            assert anki_did == existing_did

            # no notes should be changed because they already exist
            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(
                ankihub_deck_uuid=ankihub_deck_uuid
            )

    def test_import_existing_deck_2(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        from aqt import mw

        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():

            # import the apkg
            file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
            importer = AnkiPackageImporter(mw.col, file)
            importer.run()

            # move one card to another deck
            other_deck_id = mw.col.decks.add_normal_deck_with_name("other deck").id
            cids = mw.col.find_cards("deck:Testdeck")
            assert len(cids) == 3
            mw.col.set_deck([cids[0]], other_deck_id)

            ankihub_deck_uuid = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer._import_ankihub_deck_inner(
                ankihub_did=ankihub_deck_uuid,
                notes_data=ankihub_sample_deck_notes_data(),
                deck_name="test",
                remote_note_types={},
                protected_fields={},
                protected_tags=[],
            )
            anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            # when the existing cards are in multiple seperate decks a new deck is created
            assert len(new_dids) == 1
            assert anki_did == list(new_dids)[0]

            # no notes should be changed because they already exist
            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(
                ankihub_deck_uuid=ankihub_deck_uuid
            )

    def test_import_existing_deck_3(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        from aqt import mw

        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():

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

            ankihub_deck_uuid = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer._import_ankihub_deck_inner(
                ankihub_did=ankihub_deck_uuid,
                notes_data=ankihub_sample_deck_notes_data(),
                deck_name="test",
                remote_note_types={},
                protected_fields={},
                protected_tags=[],
            )
            anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            assert not new_dids
            assert anki_did == existing_did

            assert len(import_result.created_nids) == 1
            assert len(import_result.updated_nids) == 2

            assert_that_only_ankihub_sample_deck_info_in_database(
                ankihub_deck_uuid=ankihub_deck_uuid
            )

    def test_update_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():

            anki_did, _ = install_sample_ah_deck()
            first_local_did = anki_did

            ankihub_deck_uuid = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer._import_ankihub_deck_inner(
                ankihub_did=ankihub_deck_uuid,
                notes_data=ankihub_sample_deck_notes_data(),
                deck_name="test",
                remote_note_types={},
                protected_fields={},
                protected_tags=[],
                local_did=first_local_did,
            )
            second_anki_did = import_result.anki_did
            new_dids = all_dids() - dids_before_import

            assert len(new_dids) == 0
            assert first_local_did == second_anki_did

            # no notes should be changed because they already exist
            assert len(import_result.created_nids) == 0
            assert len(import_result.updated_nids) == 0

            assert_that_only_ankihub_sample_deck_info_in_database(
                ankihub_deck_uuid=ankihub_deck_uuid
            )

    def test_update_deck_when_it_was_deleted(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        from aqt import mw

        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():

            anki_did, _ = install_sample_ah_deck()
            first_local_did = anki_did

            # move cards to another deck and remove the original one
            other_deck = mw.col.decks.add_normal_deck_with_name("other deck").id
            cids = mw.col.find_cards(f"deck:{mw.col.decks.name(first_local_did)}")
            assert len(cids) == 3
            mw.col.set_deck(cids, other_deck)
            mw.col.decks.remove([first_local_did])

            ankihub_deck_uuid = next_deterministic_uuid()
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer._import_ankihub_deck_inner(
                ankihub_did=ankihub_deck_uuid,
                notes_data=ankihub_sample_deck_notes_data(),
                deck_name="test",
                remote_note_types={},
                protected_fields={},
                protected_tags=[],
                local_did=first_local_did,
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

            assert_that_only_ankihub_sample_deck_info_in_database(
                ankihub_deck_uuid=ankihub_deck_uuid
            )

    def test_update_deck_with_subdecks(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        from aqt import mw

        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():

            anki_did, ah_did = install_sample_ah_deck()

            # add a subdeck tag to a note
            notes_data = ankihub_sample_deck_notes_data()
            note_data = notes_data[0]
            note_data.tags = [f"{SUBDECK_TAG}::Testdeck::A::B"]
            note = mw.col.get_note(NoteId(note_data.anki_nid))

            # import the deck again, now with the changed note data
            dids_before_import = all_dids()
            ankihub_importer = AnkiHubImporter()
            import_result = ankihub_importer._import_ankihub_deck_inner(
                ankihub_did=ah_did,
                notes_data=notes_data,
                deck_name="test",
                remote_note_types={},
                protected_fields={},
                protected_tags=[],
                local_did=anki_did,
                subdecks=True,
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

            assert_that_only_ankihub_sample_deck_info_in_database(
                ankihub_deck_uuid=ah_did
            )

            # check that cards of the note were moved to the subdeck
            assert note.cards()
            for card in note.cards():
                assert card.did == mw.col.decks.id_for_name("Testdeck::A::B")

    def test_suspend_new_cards_of_existing_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            ankihub_cloze = create_or_get_ah_version_of_note_type(
                mw, mw.col.models.by_name("Cloze")
            )

            ah_nid = next_deterministic_uuid()
            ah_did = next_deterministic_uuid()

            def test_case(suspend_existing_card_before_update: bool):
                # create a cloze note with one card, optionally suspend the existing card,
                # then update the note using AnkiHubImporter adding a new cloze
                # which results in a new card getting created for the added cloze

                note = mw.col.new_note(ankihub_cloze)
                note["Text"] = "{{c1::foo}}"
                mw.col.add_note(note, DeckId(0))

                if suspend_existing_card_before_update:
                    # suspend the only card of the note
                    card = note.cards()[0]
                    card.queue = QUEUE_TYPE_SUSPENDED
                    card.flush()

                # update the note using the AnkiHub importer
                note_data = NoteInfo(
                    anki_nid=note.id,
                    ankihub_note_uuid=ah_nid,
                    fields=[
                        Field(name="Text", value="{{c1::foo}} {{c2::bar}}", order=0)
                    ],
                    tags=[],
                    mid=note.model()["id"],
                    last_update_type=None,
                    guid=note.guid,
                )

                # note has to be active in the database or the importer won't update it
                ankihub_db.upsert_notes_data(ankihub_did=ah_did, notes_data=[note_data])

                importer = AnkiHubImporter()
                updated_note = importer._update_or_create_note(
                    note_data=note_data,
                    anki_did=DeckId(0),
                    protected_fields={},
                    protected_tags=[],
                    first_import_of_deck=False,
                )
                assert len(updated_note.cards()) == 2
                return updated_note

            def get_new_card(note: Note):
                # the card with the higher id was created later
                return max(note.cards(), key=lambda c: c.id)

            # test "always" option
            config.public_config["suspend_new_cards_of_existing_notes"] = "always"

            updated_note = test_case(suspend_existing_card_before_update=False)
            assert get_new_card(updated_note).queue == QUEUE_TYPE_SUSPENDED

            updated_note = test_case(suspend_existing_card_before_update=True)
            assert get_new_card(updated_note).queue == QUEUE_TYPE_SUSPENDED

            # test "never" option
            config.public_config["suspend_new_cards_of_existing_notes"] = "never"

            updated_note = test_case(suspend_existing_card_before_update=False)
            assert get_new_card(updated_note).queue != QUEUE_TYPE_SUSPENDED

            updated_note = test_case(suspend_existing_card_before_update=True)
            assert get_new_card(updated_note).queue != QUEUE_TYPE_SUSPENDED

            # test "if_siblings_are_suspended" option
            config.public_config[
                "suspend_new_cards_of_existing_notes"
            ] = "if_siblings_are_suspended"

            updated_note = test_case(suspend_existing_card_before_update=False)
            assert all(
                card.queue != QUEUE_TYPE_SUSPENDED for card in updated_note.cards()
            )

            updated_note = test_case(suspend_existing_card_before_update=True)
            assert all(
                card.queue == QUEUE_TYPE_SUSPENDED for card in updated_note.cards()
            )

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
            importer._import_ankihub_deck_inner(
                ankihub_did=ah_did,
                notes_data=[note_data],
                deck_name="test",
                protected_fields={note_type_id: [protected_field_name]},
                protected_tags=["protected_tag"],
                remote_note_types={},
            )

            # assert that the fields are saved correctly in the Anki DB (protected)
            assert note[protected_field_name] == protected_field_content

            # assert that the tags are saved correctly in the Anki DB (protected)
            note = mw.col.get_note(nid)
            assert set(note.tags) == set(["tag1", "tag2", "protected_tag"])

            # assert that the note_data was saved correctly in the AnkiHub DB (without modifications)
            note_data_from_db = ankihub_db.note_data(nid)
            assert note_data_from_db == note_data

    def test_conflicting_notes_dont_get_imported(
        self,
        anki_session_with_addon_data: AnkiSession,
        ankihub_basic_note_type: NotetypeDict,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            anki_nid = NoteId(1)

            mid_1 = ankihub_basic_note_type["id"]
            mid_2 = create_copy_of_note_type(mw, ankihub_basic_note_type)["id"]

            # import the first note
            ah_did_1 = next_deterministic_uuid()
            note_info_1 = NoteInfoFactory.create(
                anki_nid=anki_nid,
                tags=["tag1"],
                mid=mid_1,
            )
            importer = AnkiHubImporter()
            import_result = importer._import_ankihub_deck_inner(
                ankihub_did=ah_did_1,
                notes_data=[note_info_1],
                deck_name="test",
            )
            assert import_result.created_nids == [anki_nid]
            assert import_result.updated_nids == []
            assert import_result.skipped_nids == []

            mod_1 = ankihub_db.scalar("SELECT mod FROM notes WHERE anki_note_id = ?", 1)
            sleep(0.1)  # sleep to test for mod value changes

            # import the second note with the same nid
            ah_did_2 = next_deterministic_uuid()
            note_info_2 = NoteInfoFactory.create(
                anki_nid=anki_nid,
                tags=["tag2"],
                mid=mid_2,
            )
            importer = AnkiHubImporter()
            import_result = importer._import_ankihub_deck_inner(
                ankihub_did=ah_did_2,
                notes_data=[note_info_2],
                deck_name="test",
            )
            assert import_result.created_nids == []
            assert import_result.updated_nids == []
            assert import_result.skipped_nids == [anki_nid]

            # Check that the first note wasn't changed by the second import.
            assert ankihub_db.note_data(anki_nid) == note_info_1
            assert ankihub_db.ankihub_deck_ids() == [ah_did_1]

            # Check that the mod value of the first note was not changed.
            mod_2 = ankihub_db.scalar("SELECT mod FROM notes WHERE anki_note_id = ?", 1)
            assert mod_2 == mod_1

            # Check that the note in the Anki database wasn't changed by the second import.
            assert mw.col.get_note(anki_nid).tags == ["tag1"]
            assert mw.col.get_note(anki_nid).mid == mid_1
            assert to_note_data(mw.col.get_note(anki_nid)) == note_info_1


def assert_that_only_ankihub_sample_deck_info_in_database(ankihub_deck_uuid: uuid.UUID):
    assert ankihub_db.ankihub_deck_ids() == [ankihub_deck_uuid]
    assert len(ankihub_db.anki_nids_for_ankihub_deck(ankihub_deck_uuid)) == 3


def create_copy_of_note_type(mw: AnkiQt, note_type: NotetypeDict) -> NotetypeDict:
    new_model = copy.deepcopy(note_type)
    new_model["id"] = 0
    mw.col.models.add_dict(new_model)
    return new_model


def test_unsubsribe_from_deck(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    from aqt import mw

    anki_session = anki_session_with_addon_data
    with anki_session.profile_loaded():
        _, ah_did = install_sample_ah_deck()

        mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
        assert len(mids) == 2

        SubscribedDecksDialog.unsubscribe_from_deck(ah_did)

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
        make_ah_note: MakeAHNote,
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

            note = make_ah_note(ankihub_nid=ankihub_nid, generate_anki_id=True)
            note.tags = ["a", "b"]
            note_was_changed_1 = prepare_note(
                note,
                first_import_of_deck=True,
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
                first_import_of_deck=True,
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
            note = make_ah_note(ankihub_nid=ankihub_nid, generate_anki_id=True)
            note.tags = list(ADDON_INTERNAL_TAGS)
            note_was_changed_5 = prepare_note(note, tags=[], first_import_of_deck=True)
            assert not note_was_changed_5
            assert set(note.tags) == set(ADDON_INTERNAL_TAGS)

            # assert that fields protected by tags are in fact protected
            note = make_ah_note(ankihub_nid=ankihub_nid, generate_anki_id=True)
            note.tags = [f"{TAG_FOR_PROTECTING_FIELDS}::Front"]
            note["Front"] = "old front"
            note_was_changed_6 = prepare_note(
                note,
                fields=[Field(name="Front", value="new front", order=0)],
                first_import_of_deck=True,
            )
            assert not note_was_changed_6
            assert note["Front"] == "old front"

            # assert that fields protected by tags are in fact protected
            note = make_ah_note(ankihub_nid=ankihub_nid, generate_anki_id=True)
            note.tags = [f"{TAG_FOR_PROTECTING_FIELDS}::All"]
            note_was_changed_7 = prepare_note(
                note,
                fields=[
                    Field(name="Front", value="new front", order=0),
                    Field(name="Back", value="new back", order=1),
                ],
                first_import_of_deck=True,
            )
            assert not note_was_changed_7
            assert note["Front"] == "old front"
            assert note["Back"] == "old back"

            # assert that the tag for protecting all fields works
            note = make_ah_note(ankihub_nid=ankihub_nid, generate_anki_id=True)
            note.tags = [f"{TAG_FOR_PROTECTING_FIELDS}::All"]
            note_was_changed_7 = prepare_note(
                note,
                fields=[
                    Field(name="Front", value="new front", order=0),
                    Field(name="Back", value="new back", order=1),
                ],
                first_import_of_deck=True,
            )
            assert not note_was_changed_7
            assert note["Front"] == "old front"
            assert note["Back"] == "old back"

            # assert that the note guid is changed
            note = make_ah_note(ankihub_nid=ankihub_nid, generate_anki_id=True)
            note_was_changed_8 = prepare_note(
                note,
                guid="new guid",
                first_import_of_deck=True,
            )
            assert note_was_changed_8
            assert note.guid == "new guid"

    def test_prepare_note_protect_field_with_spaces(
        self,
        anki_session_with_addon_data: AnkiSession,
        make_ah_note: MakeAHNote,
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
            note = make_ah_note(
                ankihub_nid=ankihub_nid,
                note_type_id=ah_basic_variation_id,
                generate_anki_id=True,
            )
            note.tags = [
                f"{TAG_FOR_PROTECTING_FIELDS}::{field_name_with_spaces.replace(' ', '_')}"
            ]
            note_changed = prepare_note(
                note=note,
                ankihub_nid=ankihub_nid,
                fields=[Field(name=field_name_with_spaces, value="new front", order=0)],
                first_import_of_deck=True,
            )
            assert not note_changed
            assert note[field_name_with_spaces] == "old field name with spaces"

            # assert that field is not protected without this tag (to make sure the test is correct)
            note = make_ah_note(
                ankihub_nid=ankihub_nid,
                note_type_id=ah_basic_variation_id,
                generate_anki_id=True,
            )
            note_changed = prepare_note(
                note=note,
                ankihub_nid=ankihub_nid,
                fields=[Field(name=field_name_with_spaces, value="new front", order=0)],
                first_import_of_deck=True,
            )
            assert note_changed
            assert note[field_name_with_spaces] == "new front"


def prepare_note(
    note,
    first_import_of_deck: bool,
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
        ankihub_note_uuid=ankihub_nid,
        anki_nid=note.id,
        fields=fields,
        tags=tags,
        mid=note.mid,
        guid=guid,
        last_update_type=last_update_type,
    )

    ankihub_importer = AnkiHubImporter()
    result = ankihub_importer.prepare_note(
        note,
        note_data=note_data,
        protected_fields=protected_fields,
        protected_tags=protected_tags,
        first_import_of_deck=first_import_of_deck,
    )
    return result


class TestCustomSearchNodes:
    def test_ModifiedAfterSyncSearchNode_with_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()
            all_nids = mw.col.find_notes("")

            browser = Mock()
            browser.table.is_notes_mode.return_value = True

            with attached_ankihub_db():
                assert (
                    ModifiedAfterSyncSearchNode(browser, "yes").filter_ids(all_nids)
                    == []
                )
                assert (
                    ModifiedAfterSyncSearchNode(browser, "no").filter_ids(all_nids)
                    == all_nids
                )

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
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()
            all_cids = mw.col.find_cards("")

            browser = Mock()
            browser.table.is_notes_mode.return_value = False

            with attached_ankihub_db():
                assert (
                    ModifiedAfterSyncSearchNode(browser, "yes").filter_ids(all_cids)
                    == []
                )
                assert (
                    ModifiedAfterSyncSearchNode(browser, "no").filter_ids(all_cids)
                    == all_cids
                )

                # we can't use freeze_time here because note.mod is set by the Rust backend
                sleep(1.1)

                # modify a note - this changes its mod value in the Anki DB
                cid = all_cids[0]
                note = mw.col.get_note(mw.col.get_card(cid).nid)
                note["Front"] = "new front"
                note.flush()

                cids = ModifiedAfterSyncSearchNode(browser, "yes").filter_ids(all_cids)
                assert cids == [cid]

    def test_UpdatedInTheLastXDaysSearchNode(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()

            all_nids = mw.col.find_notes("")

            browser = Mock()
            browser.table.is_notes_mode.return_value = True

            with attached_ankihub_db():
                assert (
                    UpdatedInTheLastXDaysSearchNode(browser, "1").filter_ids(all_nids)
                    == all_nids
                )
                assert (
                    UpdatedInTheLastXDaysSearchNode(browser, "2").filter_ids(all_nids)
                    == all_nids
                )

                yesterday_timestamp = int(
                    (datetime.now() - timedelta(days=1)).timestamp()
                )
                mw.col.db.execute(
                    f"UPDATE ankihub_db.notes SET mod = {yesterday_timestamp}"
                )

                assert (
                    UpdatedInTheLastXDaysSearchNode(browser, "1").filter_ids(all_nids)
                    == []
                )
                assert (
                    UpdatedInTheLastXDaysSearchNode(browser, "2").filter_ids(all_nids)
                    == all_nids
                )

    def test_NewNoteSearchNode(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
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
            AnkiHubImporter()._import_ankihub_deck_inner(
                ankihub_did=next_deterministic_uuid(),
                notes_data=notes_data,
                remote_note_types=ankihub_models,
                protected_fields={},
                protected_tags=[],
                deck_name="Test-Deck",
            )

            all_nids = mw.col.find_notes("")

            browser = Mock()
            browser.table.is_notes_mode.return_value = True

            with attached_ankihub_db():
                # notes without a last_update_type are new
                assert NewNoteSearchNode(browser, "").filter_ids(all_nids) == [
                    notes_data[0].anki_nid,
                    notes_data[1].anki_nid,
                ]

    def test_SuggestionTypeSearchNode(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
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
            AnkiHubImporter()._import_ankihub_deck_inner(
                ankihub_did=next_deterministic_uuid(),
                notes_data=notes_data,
                remote_note_types=ankihub_models,
                protected_fields={},
                protected_tags=[],
                deck_name="Test-Deck",
            )

            all_nids = mw.col.find_notes("")

            browser = Mock()
            browser.table.is_notes_mode.return_value = True

            with attached_ankihub_db():
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
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            install_sample_ah_deck()

            all_nids = mw.col.find_notes("")

            browser = Mock()
            browser.table.is_notes_mode.return_value = True

            with attached_ankihub_db():
                assert (
                    UpdatedSinceLastReviewSearchNode(browser, "").filter_ids(all_nids)
                    == []
                )

            # Add a review entry for a card to the database.
            nid = all_nids[0]
            note = mw.col.get_note(nid)
            cid = note.card_ids()[0]

            record_review(mw, cid, mod_seconds=1)

            # Update the mod time in the ankihub database to simulate a note update.
            ankihub_db.execute(
                "UPDATE notes SET mod = ? WHERE anki_note_id = ?",
                2,
                nid,
            )

            # Check that the note of the card is now included in the search results.
            with attached_ankihub_db():
                assert UpdatedSinceLastReviewSearchNode(browser, "").filter_ids(
                    all_nids
                ) == [nid]

            # Add another review entry for the card to the database.
            record_review(mw, cid, mod_seconds=3)

            # Check that the note of the card is not included in the search results anymore.
            with attached_ankihub_db():
                assert (
                    UpdatedSinceLastReviewSearchNode(browser, "").filter_ids(all_nids)
                    == []
                )


def record_review(mw: AnkiQt, cid: CardId, mod_seconds: int):
    mw.col.db.execute(
        "INSERT INTO revlog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        # the revlog table stores the timestamp in milliseconds
        mod_seconds * 1000,
        cid,
        1,
        1,
        1,
        1,
        1,
        1,
        0,
    )


class TestBrowserTreeView:
    # without this mark the test sometime fails on clean-up
    @pytest.mark.qt_no_exception_capture
    def test_ankihub_items_exist_and_work(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        from aqt import dialogs
        from aqt.browser import Browser
        from aqt.browser.sidebar.item import SidebarItem
        from aqt.browser.sidebar.tree import SidebarTreeView

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
        from aqt import dialogs
        from aqt.browser import Browser
        from aqt.browser.sidebar.item import SidebarItem
        from aqt.browser.sidebar.tree import SidebarTreeView

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
    from aqt import dialogs

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
            str(notes_data[0].ankihub_note_uuid),
            "No",
            "No",
        ]


class TestBuildSubdecksAndMoveCardsToThem:
    def test_basic(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_sample_ah_deck: InstallSampleAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            _, ah_did = install_sample_ah_deck()

            # add subdeck tags to notes
            nids = mw.col.find_notes("deck:Testdeck")
            note1 = mw.col.get_note(nids[0])
            note1.tags = [f"{SUBDECK_TAG}::Testdeck"]
            note1.flush()

            note2 = mw.col.get_note(nids[1])
            note2.tags = [f"{SUBDECK_TAG}::Testdeck::B::C"]
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
    monkeypatch: MonkeyPatch,
    install_sample_ah_deck: InstallSampleAHDeck,
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

        # Mock the import function (that is called by reset_local_changes_to_notes)
        # so that it doesn't try to fetch data from AnkiHub
        # and just use empty remote note types and protected fields and tags.
        # This works because the note types are not deleted in the test and protected
        # fields and tags are also not used in the test.
        def mock_import_ankihub_deck(self: AnkiHubImporter, *args, **kwargs):
            self._import_ankihub_deck_inner(
                *args,
                **kwargs,
                remote_note_types=dict(),  # type: ignore
                protected_fields=dict(),  # type: ignore
                protected_tags=list(),  # type: ignore
            )

        monkeypatch.setattr(
            "ankihub.importing.AnkiHubImporter.import_ankihub_deck",
            mock_import_ankihub_deck,
        )
        # reset local changes
        nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
        reset_local_changes_to_notes(nids=nids, ankihub_deck_uuid=ah_did)

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
    monkeypatch: MonkeyPatch,
    disable_image_support_feature_flag,
):
    anki_session = anki_session_with_addon_before_profile_support

    # mock the ah_sync object so that the add-on doesn't try to sync with AnkiHub
    monkeypatch.setattr(
        "ankihub.sync.ah_sync.sync_all_decks_and_media", lambda *args, **kwargs: None
    )

    # run the entrypoint and load the profile to trigger the migration
    entry_point.run()
    with anki_session.profile_loaded():
        pass

    user_files_path = Path(anki_session.base) / "addons21" / "ankihub" / "user_files"
    profile_files_path = user_files_path / str(TEST_PROFILE_ID)

    assert set([x.name for x in profile_files_path.glob("*")]) == {
        "ankihub.db",
        ".private_config.json",
    }

    assert set([x.name for x in user_files_path.glob("*")]) == {
        str(TEST_PROFILE_ID),
        "README.md",
        "ankihub.log",
        "ankihub.log.1",
    }


def test_profile_swap(
    anki_session_with_addon_data: AnkiSession,
    monkeypatch: MonkeyPatch,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    anki_session = anki_session_with_addon_data

    USER_FILES_PATH = Path(anki_session.base) / "addons21/ankihub/user_files"
    # already exists
    PROFILE_1_NAME = "User 1"
    PROFILE_1_ID = TEST_PROFILE_ID
    # will be created in the test
    PROFILE_2_NAME = "User 2"
    PROFILE_2_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

    general_setup_mock = Mock()
    monkeypatch.setattr("ankihub.entry_point.general_setup", general_setup_mock)

    entry_point.run()

    # load the first profile and import a deck
    with anki_session.profile_loaded():
        mw = anki_session.mw

        assert profile_files_path() == USER_FILES_PATH / str(PROFILE_1_ID)

        install_sample_ah_deck()

        # the database should contain the imported deck
        assert len(ankihub_db.ankihub_deck_ids()) == 1
        # the config should contain the deck subscription
        assert len(config.deck_ids()) == 1

    # create the second profile
    mw.pm.create(PROFILE_2_NAME)

    # load the second profile
    mw.pm.load(PROFILE_2_NAME)
    # monkeypatch uuid4 so that the id of the second profile is known
    with monkeypatch.context() as m:
        m.setattr("uuid.uuid4", lambda: PROFILE_2_ID)
        with anki_session.profile_loaded():
            assert profile_files_path() == USER_FILES_PATH / str(PROFILE_2_ID)
            # the database should be empty
            assert len(ankihub_db.ankihub_deck_ids()) == 0
            # the config should not conatin any deck subscriptions
            assert len(config.deck_ids()) == 0

    # load the first profile again
    mw.pm.load(PROFILE_1_NAME)
    with anki_session.profile_loaded():
        assert profile_files_path() == USER_FILES_PATH / str(PROFILE_1_ID)
        # the database should contain the imported deck
        assert len(ankihub_db.ankihub_deck_ids()) == 1
        # the config should contain the deck subscription
        assert len(config.deck_ids()) == 1

    # assert that the general_setup function was only called once
    assert general_setup_mock.call_count == 1


class TestAutoSync:
    def setup_method(self):
        # Mock the token so that the AnkiHub sync is not aborted.
        config.token = PropertyMock(return_value=lambda: "test_token")

    def test_with_on_ankiweb_sync_config_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        monkeypatch: MonkeyPatch,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            patch_ankiweb_sync_to_do_nothing(mw, monkeypatch)
            sync_all_decks_and_media_mock = self._mock_sync_all_decks_and_media(
                monkeypatch
            )

            setup_ankihub_sync_on_ankiweb_sync()

            config.public_config["auto_sync"] = "on_ankiweb_sync"

            # Trigger the AnkiWeb sync and assert that the AnkiHub sync is invoked.
            sync_collection(mw, on_done=lambda: None)
            qtbot.wait(200)
            assert sync_all_decks_and_media_mock.call_count == 1

    def test_with_never_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        monkeypatch: MonkeyPatch,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            patch_ankiweb_sync_to_do_nothing(mw, monkeypatch)
            sync_all_decks_mock = self._mock_sync_all_decks_and_media(monkeypatch)

            setup_ankihub_sync_on_ankiweb_sync()

            config.public_config["auto_sync"] = "never"

            # Trigger the AnkiWeb sync and assert that the AnkiHub sync is invoked.
            sync_collection(mw, on_done=lambda: None)
            qtbot.wait(200)
            assert sync_all_decks_mock.call_count == 0

    def test_with_on_startup_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        monkeypatch: MonkeyPatch,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            patch_ankiweb_sync_to_do_nothing(mw, monkeypatch)
            sync_all_decks_mock = self._mock_sync_all_decks_and_media(monkeypatch)

            setup_ankihub_sync_on_ankiweb_sync()

            config.public_config["auto_sync"] = "on_startup"

            # Trigger the AnkiWeb sync and assert that the AnkiHub sync is invoked.
            sync_collection(mw, on_done=lambda: None)
            qtbot.wait(200)
            assert sync_all_decks_mock.call_count == 1

            # Trigger the AnkiWeb sync again and assert that the AnkiHub sync is not invoked this time.
            sync_collection(mw, on_done=lambda: None)
            qtbot.wait(200)
            assert sync_all_decks_mock.call_count == 1

    def _mock_sync_all_decks_and_media(self, monkeypatch: MonkeyPatch) -> Mock:
        # Mock the sync with AnkiHub so that it doesn't actually sync.
        sync_all_decks_mock = Mock()
        monkeypatch.setattr(ah_sync, "sync_all_decks_and_media", sync_all_decks_mock)
        return sync_all_decks_mock


def patch_ankiweb_sync_to_do_nothing(mw: AnkiQt, monkeypatch: MonkeyPatch):
    """Patch AnkiWeb sync so that when this is called:
    https://github.com/ankitects/anki/blob/e5d5d1d4bdecfac326353d154c933e477c4e3eb8/qt/aqt/sync.py#L87
    this runs:
    https://github.com/ankitects/anki/blob/e5d5d1d4bdecfac326353d154c933e477c4e3eb8/qt/aqt/sync.py#L122-L127
    but the AnkiWeb sync does nothing and no error dialogs show show up.
    """

    # Mock the sync_auth function so that the sync is not aborted.
    monkeypatch.setattr(mw.pm, "sync_auth", lambda: True)

    # Mock the sync with AnkiWeb so that it doesn't actually sync.
    # Also mock the sync output so that Anki doesn't trigger a full sync or show a message.
    sync_output_mock = Mock(
        host_number=1,
        server_message=[],
        required=SyncOutput.NO_CHANGES,
        NO_CHANGES=SyncOutput.NO_CHANGES,
    )
    monkeypatch.setattr(
        mw.col._backend, "sync_collection", lambda *args: sync_output_mock
    )

    # Mock the latest_progress function because it is called by a timer during the sync
    # and would otherwise open an error message dialog.
    monkeypatch.setattr(mw.col, "latest_progress", lambda *args, **kwargs: Mock())

    # Mock the progress.set_title function because it is called by a timer during the sync
    # (with the latest_progress as argument).
    monkeypatch.setattr(mw.progress, "set_title", lambda *args, **kwargs: False)

    # Mock the can_auto_sync function so that no sync is triggered when Anki is closed.
    monkeypatch.setattr(mw, "can_auto_sync", lambda *args, **kwargs: False)


def test_sync_with_optional_content(
    anki_session_with_addon_data: AnkiSession,
    monkeypatch: MonkeyPatch,
    next_deterministic_uuid: Callable[[], uuid.UUID],
):
    anki_session = anki_session_with_addon_data

    with anki_session.profile_loaded():
        with anki_session.deck_installed(SAMPLE_DECK_APKG) as _:
            mw = anki_session.mw

            ankihub_deck_uuid = next_deterministic_uuid()
            deck_extension_id = 31

            notes_data = ankihub_sample_deck_notes_data()
            ankihub_db.upsert_notes_data(ankihub_deck_uuid, notes_data)
            note_data = notes_data[0]
            note = mw.col.get_note(NoteId(note_data.anki_nid))

            assert set(note.tags) == set(["my::tag2", "my::tag"])

            latest_update = datetime.now()
            with monkeypatch.context() as m:
                m.setattr(
                    "ankihub.ankihub_client.AnkiHubClient.get_deck_extensions_by_deck_id",
                    lambda *args, **kwargs: [
                        DeckExtension(
                            id=deck_extension_id,
                            owner_id=1,
                            ankihub_deck_uuid=ankihub_deck_uuid,
                            name="test99",
                            tag_group_name="test99",
                            description="",
                        )
                    ],
                )
                m.setattr(
                    "ankihub.ankihub_client.AnkiHubClient.get_deck_extension_updates",
                    lambda *args, **kwargs: [
                        DeckExtensionUpdateChunk(
                            note_customizations=[
                                NoteCustomization(
                                    ankihub_nid=note_data.ankihub_note_uuid,
                                    tags=[
                                        "AnkiHub_Optional::test99::test1",
                                        "AnkiHub_Optional::test99::test2",
                                    ],
                                ),
                            ],
                            latest_update=latest_update,
                        ),
                    ],
                )
                sync = AnkiHubSync()
                sync._sync_deck_extensions(ankihub_deck_uuid)

            updated_note = mw.col.get_note(note.id)

            expected_tags = [
                "my::tag2",
                "my::tag",
                "AnkiHub_Optional::test99::test2",
                "AnkiHub_Optional::test99::test1",
            ]

            assert set(updated_note.tags) == set(expected_tags)

            # assert that the deck extension info was saved in the config
            assert config.deck_extension_config(
                extension_id=deck_extension_id
            ) == DeckExtensionConfig(
                ankihub_deck_uuid=ankihub_deck_uuid,
                owner_id=1,
                name="test99",
                tag_group_name="test99",
                description="",
                latest_update=latest_update,
            )


def test_optional_tag_suggestion_dialog(
    anki_session_with_addon_data: AnkiSession,
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    anki_session = anki_session_with_addon_data

    with anki_session.profile_loaded():
        mw = anki_session.mw

        # import a sample deck and give notes optional tags
        install_sample_ah_deck()

        nids = mw.col.find_notes("")
        notes = [mw.col.get_note(nid) for nid in nids]

        notes[0].tags = [
            f"{TAG_FOR_OPTIONAL_TAGS}::VALID::tag1",
        ]
        notes[0].flush()

        notes[1].tags = [
            f"{TAG_FOR_OPTIONAL_TAGS}::INVALID::tag1",
        ]
        notes[1].flush()

        # open the dialog
        monkeypatch.setattr(
            "ankihub.ankihub_client.AnkiHubClient.prevalidate_tag_groups",
            lambda *args, **kwargs: [
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
        dialog = OptionalTagsSuggestionDialog(parent=mw, nids=nids)
        dialog.show()

        qtbot.wait(500)

        # assert that the dialog is in the correct state
        assert dialog.tag_group_list.count() == 2

        # items are sorted alphabetically
        assert dialog.tag_group_list.item(0).text() == "INVALID"
        assert "error message" in dialog.tag_group_list.item(0).toolTip()

        assert dialog.tag_group_list.item(1).text() == "VALID"
        # empty tooltip means that the tag group is valid because invalid tag groups
        # have a tooltip with the error message
        assert dialog.tag_group_list.item(1).toolTip() == ""

        assert dialog.submit_btn.isEnabled()

        suggest_optional_tags_mock = Mock()
        monkeypatch.setattr(
            "ankihub.ankihub_client.AnkiHubClient.suggest_optional_tags",
            suggest_optional_tags_mock,
        )

        # select the "VALID" tag group and click the submit button
        dialog.tag_group_list.item(1).setSelected(True)
        qtbot.mouseClick(dialog.submit_btn, Qt.MouseButton.LeftButton)
        qtbot.wait(500)

        assert suggest_optional_tags_mock.call_count == 1

        # assert that the suggest_optional_tags function was called with the correct arguments
        assert suggest_optional_tags_mock.call_args.kwargs == {
            "suggestions": [
                OptionalTagSuggestion(
                    tag_group_name="VALID",
                    deck_extension_id=1,
                    ankihub_note_uuid=uuid.UUID("e2857855-b414-4a2a-a0bf-2a0eac273f21"),
                    tags=["AnkiHub_Optional::VALID::tag1"],
                )
            ],
            "auto_accept": False,
        }


@pytest.mark.qt_no_exception_capture
def test_reset_optional_tags_action(
    anki_session_with_addon_data: AnkiSession,
    qtbot: QtBot,
    monkeypatch: MonkeyPatch,
    install_sample_ah_deck: InstallSampleAHDeck,
):
    from aqt import dialogs

    entry_point.run()

    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        _, ah_did = install_sample_ah_deck()

        config.create_or_update_deck_extension_config(
            DeckExtension(
                id=1,
                ankihub_deck_uuid=ah_did,
                owner_id=1,
                name="test99",
                tag_group_name="test99",
                description="",
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
        choose_list_mock = Mock()
        choose_list_mock.return_value = 0
        monkeypatch.setattr("ankihub.gui.browser.choose_list", choose_list_mock)

        # mock the ask_user function to always confirm the reset
        monkeypatch.setattr(
            "ankihub.gui.browser.ask_user", lambda *args, **kwargs: True
        )

        # mock the is_logged_in function to always return True
        is_logged_in_mock = Mock()
        is_logged_in_mock.return_value = True
        monkeypatch.setattr(config, "is_logged_in", is_logged_in_mock)

        # mock method of ah_sync
        sync_all_decks_and_media_mock = Mock()
        monkeypatch.setattr(
            ah_sync, "sync_all_decks_and_media", sync_all_decks_and_media_mock
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
        assert sync_all_decks_and_media_mock.call_count == 1

        # the other note should not be affected, because it is in a different deck
        assert mw.col.get_note(other_note.id).tags == [
            f"{TAG_FOR_OPTIONAL_TAGS}::test99::test2"
        ]


def test_download_images_on_sync(
    anki_session_with_addon_data: AnkiSession,
    install_sample_ah_deck: InstallSampleAHDeck,
    monkeypatch: MonkeyPatch,
    qtbot: QtBot,
    enable_image_support_feature_flag,
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        _, ah_did = install_sample_ah_deck()

        # Add a reference to a local image to a note.
        nids = mw.col.find_notes("")
        notes = [ankihub_db.note_data(nid) for nid in nids]
        notes[0].fields[0].value = "Some text. <img src='image.png'>"
        ankihub_db.upsert_notes_data(ah_did, notes)

        # Mock the token to simulate that the user is logged in.
        monkeypatch.setattr(config, "token", lambda: "test token")

        # Mock the client to simulate that there are no deck updates and extensions.
        monkeypatch.setattr(
            AnkiHubClient,
            "get_deck_updates",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            AnkiHubClient,
            "get_deck_extensions_by_deck_id",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            AnkiHubClient,
            "get_asset_disabled_fields",
            lambda *args, **kwargs: {},
        )

        # Mock the client method for downloading images.
        download_images_mock = Mock()
        monkeypatch.setattr(AnkiHubClient, "download_images", download_images_mock)

        # Run the sync.
        ah_sync.sync_all_decks_and_media()

        # Let the background thread (which downloads missing media) finish.
        qtbot.wait(200)

        # Assert that the client method for downloading images was called with the correct arguments.
        download_images_mock.assert_called_once_with(["image.png"], ah_did)


def test_upload_assets_individually(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    monkeypatch: MonkeyPatch,
    requests_mock: Mocker,
):
    import tempfile

    with anki_session_with_addon_data.profile_loaded():
        fake_presigned_url = "https://fake_presigned_url.com"
        monkeypatch.setattr(
            AnkiHubClient,
            "get_presigned_url_for_multiple_uploads",
            lambda *args, **kwargs: {
                "url": fake_presigned_url,
                "fields": {
                    "key": "deck_images/test/${filename}",
                },
            },
        )

        s3_upload_request_mock = requests_mock.post(
            fake_presigned_url, json={"success": True}, status_code=204
        )

        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            file_path = Path(f.name)
            fake_deck_id = next_deterministic_uuid()
            client = AnkiHubClient(local_media_dir_path=file_path.parent)
            client._upload_assets_individually(
                set([file_path.name]), ah_did=fake_deck_id
            )

        assert len(s3_upload_request_mock.request_history) == 1  # type: ignore

        file_name_from_request = re.findall(
            r'filename="(.*?)"', s3_upload_request_mock.last_request.text  # type: ignore
        )[0]
        assert file_name_from_request == file_path.name


class TestSuggestionsWithImages:
    def test_suggest_note_update_with_image(
        self,
        anki_session_with_addon_data: AnkiSession,
        requests_mock: Mocker,
        monkeypatch: MonkeyPatch,
        install_sample_ah_deck: Callable[[], Tuple[uuid.UUID, int]],
        qtbot: QtBot,
        enable_image_support_feature_flag,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            install_sample_ah_deck()

            fake_presigned_url = "https://fake_presigned_url.com"
            s3_upload_request_mock = requests_mock.post(
                fake_presigned_url, json={"success": True}, status_code=204
            )

            monkeypatch.setattr(
                AnkiHubClient,
                "get_presigned_url_for_multiple_uploads",
                lambda *args, **kwargs: {
                    "url": fake_presigned_url,
                    "fields": {
                        "key": "deck_images/test/${filename}",
                    },
                },
            )

            with tempfile.NamedTemporaryFile(suffix=".png") as f:
                # add file to media folder
                file_name_in_col = mw.col.media.add_file(f.name)
                file_path_in_col = Path(mw.col.media.dir()) / file_name_in_col

                nids = mw.col.find_notes("")
                note = mw.col.get_note(nids[0])

                # add file reference to a note
                file_name_in_col = Path(file_path_in_col.name).name
                note["Front"] = f'<img src="{file_name_in_col}">'
                note.flush()

                ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)
                suggestion_request_mock = requests_mock.post(
                    f"{config.api_url}/notes/{ah_nid}/suggestion/", status_code=201
                )

                # create a suggestion for the note
                suggest_note_update(
                    note=note,
                    change_type=SuggestionType.NEW_CONTENT,
                    comment="test",
                )

                # Wait for the background thread that uploads the images to finish.
                qtbot.wait(200)

                assert len(suggestion_request_mock.request_history) == 1  # type: ignore

                # assert that the image was uploaded
                assert len(s3_upload_request_mock.request_history) == 1  # type: ignore

                self._assert_img_names_as_expected(
                    note=note,
                    upload_request_mock=s3_upload_request_mock,  # type: ignore
                    suggestion_request_mock=suggestion_request_mock,  # type: ignore
                )

    def test_suggest_new_note_with_image(
        self,
        anki_session_with_addon_data: AnkiSession,
        requests_mock: Mocker,
        monkeypatch: MonkeyPatch,
        install_sample_ah_deck: InstallSampleAHDeck,
        qtbot: QtBot,
        enable_image_support_feature_flag,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            _, ah_did = install_sample_ah_deck()

            fake_presigned_url = "https://fake_presigned_url.com"
            s3_upload_request_mock = requests_mock.post(
                fake_presigned_url, json={"success": True}, status_code=204
            )

            monkeypatch.setattr(
                AnkiHubClient,
                "get_presigned_url_for_multiple_uploads",
                lambda *args, **kwargs: {
                    "url": fake_presigned_url,
                    "fields": {
                        "key": "deck_images/test/${filename}",
                    },
                },
            )

            suggestion_request_mock = requests_mock.post(
                f"{config.api_url}/decks/{ah_did}/note-suggestion/",
                status_code=201,
            )

            with tempfile.NamedTemporaryFile(suffix=".png") as f:
                # add file to media folder
                file_name_in_col = mw.col.media.add_file(f.name)
                file_path_in_col = Path(mw.col.media.dir()) / file_name_in_col

                # add file reference to a note
                file_name_in_col = Path(file_path_in_col.name).name
                note = mw.col.new_note(
                    mw.col.models.by_name("Basic (Testdeck / user1)")
                )
                note["Front"] = f'<img src="{file_name_in_col}">'
                mw.col.add_note(note, DeckId(1))

                suggest_new_note(
                    note=note,
                    ankihub_did=ah_did,
                    comment="test",
                )

                # Wait for the background thread that uploads the images to finish.
                qtbot.wait(200)

                self._assert_img_names_as_expected(
                    note=note,
                    upload_request_mock=s3_upload_request_mock,  # type: ignore
                    suggestion_request_mock=suggestion_request_mock,  # type: ignore
                )

    def _assert_img_names_as_expected(
        self, note: Note, upload_request_mock: Mocker, suggestion_request_mock: Mocker
    ):
        # Assert that the image names in the suggestion, the note and the uploaded image are as expected.
        note.load()
        img_name_in_note = re.search(IMG_NAME_IN_IMG_TAG_REGEX, note["Front"]).group(1)

        name_of_uploaded_image = re.findall(
            r'filename="(.*?)"', upload_request_mock.last_request.text  # type: ignore
        )[0]

        suggestion_dict = suggestion_request_mock.last_request.json()  # type: ignore
        first_field_value = suggestion_dict["fields"][0]["value"]
        img_name_in_suggestion = re.search(
            IMG_NAME_IN_IMG_TAG_REGEX, first_field_value
        ).group(1)

        # The expected_img_name will be the same on each test run because the file is empty and thus
        # the hash will be the same each time.
        expected_img_name = "d41d8cd98f00b204e9800998ecf8427e.png"
        assert img_name_in_suggestion == expected_img_name
        assert img_name_in_note == expected_img_name
        assert name_of_uploaded_image == expected_img_name

    def test_should_ignore_asset_file_names_not_present_at_local_collection(
        self,
        anki_session_with_addon_data: AnkiSession,
        requests_mock: Mocker,
        monkeypatch: MonkeyPatch,
        install_sample_ah_deck: Callable[[], Tuple[uuid.UUID, int]],
        enable_image_support_feature_flag,
    ):
        anki_session = anki_session_with_addon_data
        with anki_session.profile_loaded():
            mw = anki_session.mw

            install_sample_ah_deck()

            fake_presigned_url = "https://fake_presigned_url.com"
            s3_upload_request_mock = requests_mock.post(
                fake_presigned_url, json={"success": True}, status_code=204
            )

            monkeypatch.setattr(
                AnkiHubClient,
                "get_presigned_url_for_multiple_uploads",
                lambda *args, **kwargs: {
                    "url": fake_presigned_url,
                    "fields": {
                        "key": "deck_images/test/${filename}",
                    },
                },
            )

            # grab a note from the deck
            nids = mw.col.find_notes("")
            note = mw.col.get_note(nids[0])

            # add reference to a note of an asset that does not exist locally
            note_content = '<img src="this_image_is_not_in_the_local_collection.png">'
            note["Front"] = note_content
            note.flush()

            ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)

            # create a suggestion for the note
            suggestion_request_mock = requests_mock.post(
                f"{config.api_url}/notes/{ah_nid}/suggestion/", status_code=201
            )

            suggest_note_update(
                note=note,
                change_type=SuggestionType.NEW_CONTENT,
                comment="test",
            )

            # assert that the suggestion is made
            assert len(suggestion_request_mock.request_history) == 1  # type: ignore

            # assert that the image was NOT uploaded
            assert len(s3_upload_request_mock.request_history) == 0  # type: ignore

            note.load()

            # Assert note content is unchanged
            assert note_content == note["Front"]


class TestAddonUpdate:
    def test_addon_update(
        self,
        anki_session_with_addon_data: AnkiSession,
        monkeypatch: MonkeyPatch,
        qtbot: QtBot,
    ):
        # Install the add-on so that all files are in the add-on folder.
        # The anki_session fixture does not setup the add-ons code in the add-ons folder.
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            result = mw.addonManager.install(file=str(ANKIHUB_ANKIADDON_FILE))
            assert isinstance(result, InstallOk)

        # The purpose of this mocks is to test whether our modifications to the add-on update process
        # (defined in ankihub.addons) are used.
        # The original functions will still be called because this sets the side effect to be the original functions,
        # but this way we can check if they were called.
        maybe_change_file_permissions_of_addon_files_mock = Mock()
        maybe_change_file_permissions_of_addon_files_mock.side_effect = (
            _maybe_change_file_permissions_of_addon_files
        )
        monkeypatch.setattr(
            "ankihub.addons._maybe_change_file_permissions_of_addon_files",
            maybe_change_file_permissions_of_addon_files_mock,
        )

        with_disabled_log_file_handler_mock = Mock()
        with_disabled_log_file_handler_mock.side_effect = _with_disabled_log_file_handler  # type: ignore
        monkeypatch.setattr(
            "ankihub.addons._with_disabled_log_file_handler",
            with_disabled_log_file_handler_mock,
        )

        # Udpate the AnkiHub add-on entry point has to be run so that the add-on is loaded and
        # the patches to the update process are applied
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            result = mw.addonManager.install(file=str(ANKIHUB_ANKIADDON_FILE))
            assert isinstance(result, InstallOk)

            assert mw.addonManager.allAddons() == ["ankihub"]

        # This is called tree times: for backupUserFiles, deleteAddon, and restoreUserFiles.
        assert with_disabled_log_file_handler_mock.call_count == 3

        # This is called twice: for backupUserFiles and for deleteAddon.
        assert maybe_change_file_permissions_of_addon_files_mock.call_count == 2

        # start Anki
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            assert mw.addonManager.allAddons() == ["ankihub"]
            qtbot.wait(1000)

    def test_that_changing_file_permissions_of_addons_folder_does_not_break_addon_load(
        self, anki_session_with_addon_data: AnkiSession, qtbot: QtBot
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            addon_dir = Path(mw.addonManager.addonsFolder("ankihub"))
            _change_file_permissions_of_addon_files(addon_dir=addon_dir)

        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

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
        self, anki_session: AnkiSession, monkeypatch: MonkeyPatch, qtbot: QtBot
    ):
        # Test that the original AnkiQt._sync_collection_and_media method gets called
        # despite the monkeypatching we do in debug.py.
        with anki_session.profile_loaded():
            mw = anki_session.mw

            # Mock the sync fuction so that it does not throw errors when called.
            # It expects to be authenticated with AnkiWeb among other things.
            patch_ankiweb_sync_to_do_nothing(mw, monkeypatch)
            monkeypatch.setattr(mw.col, "_backend", Mock())
            monkeypatch.setattr(mw.taskman, "with_progress", Mock())

            # Mock the sync_will_start hook so that we can check if it was called when the sync starts.
            sync_will_start_mock = Mock()
            monkeypatch.setattr(gui_hooks, "sync_will_start", sync_will_start_mock)

            _setup_logging_for_sync_collection_and_media()

            mw._sync_collection_and_media(after_sync=lambda: None)

            sync_will_start_mock.assert_called_once()

    def test_setup_logging_for_db_begin(
        self, anki_session: AnkiSession, monkeypatch: MonkeyPatch
    ):
        with anki_session.profile_loaded():
            mw = anki_session.mw

            db_begin_mock = Mock()
            monkeypatch.setattr(mw.col._backend, "db_begin", db_begin_mock)

            _setup_logging_for_db_begin()

            mw.col.db.begin()

            db_begin_mock.assert_called_once()

    def test_setup_sentry_reporting_for_error_on_addon_update(
        self, anki_session: AnkiSession, monkeypatch: MonkeyPatch
    ):
        # Test that the original AddonManager._install method gets called despite the monkeypatching we do in debug.py
        with anki_session.profile_loaded():

            # Mock the _install function so that it does not throw errors when called.
            install_mock = Mock()
            monkeypatch.setattr(aqt.mw.addonManager, "_install", install_mock)

            _setup_sentry_reporting_for_error_on_addon_update()

            # Using fake arguments just to make sure that the original function (which is now mocked)
            # is called with the same arguments.
            aqt.mw.addonManager._install("arg1", "arg2")  # type: ignore

            install_mock.assert_called_once_with("arg1", "arg2")

    def test_user_files_context_dict(self, anki_session: AnkiSession):
        # Test that the user_files_context_dict function does not throw an exception when called.
        with anki_session.profile_loaded():
            _user_files_context_dict()

    def test_log_stack(self):
        # Test that the _log_stack function does not throw an exception when called.
        _log_stack("test")
