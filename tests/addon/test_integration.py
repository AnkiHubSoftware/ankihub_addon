import copy
import gzip
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, Mock

import aqt
from anki.decks import DeckId
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from aqt.importing import AnkiPackageImporter
from pytest_anki import AnkiSession

SAMPLE_MODEL_ID = NotetypeId(1656968697414)
TEST_DATA_PATH = Path(__file__).parent.parent / "test_data"
SAMPLE_DECK_APKG = TEST_DATA_PATH / "small.apkg"
ANKIHUB_SAMPLE_DECK_APKG = TEST_DATA_PATH / "small_ankihub.apkg"
SAMPLE_NOTES_DATA = eval((TEST_DATA_PATH / "small_ankihub.txt").read_text())

UUID_1 = uuid.UUID("1f28bc9e-f36d-4e1d-8720-5dd805f12dd0")
UUID_2 = uuid.UUID("2f28bc9e-f36d-4e1d-8720-5dd805f12dd0")


def ankihub_sample_deck_notes_data():
    from ankihub.ankihub_client import NoteInfo, transform_notes_data

    notes_data_raw = transform_notes_data(SAMPLE_NOTES_DATA)
    result = [NoteInfo.from_dict(x) for x in notes_data_raw]
    return result


def test_entry_point(anki_session_with_addon: AnkiSession):
    from aqt.main import AnkiQt

    from ankihub import entry_point

    mw = entry_point.run()
    assert isinstance(mw, AnkiQt)


def test_editor(anki_session_with_addon: AnkiSession, requests_mock, monkeypatch):
    from ankihub.gui.editor import on_suggestion_button_press, refresh_suggestion_button
    from ankihub.settings import (
        ANKIHUB_NOTE_TYPE_FIELD_NAME,
        API_URL_BASE,
        AnkiHubCommands,
    )

    ankihub_note_uuid = UUID_1
    ankihub_deck_uuid = UUID_2

    monkeypatch.setattr("ankihub.gui.editor.SuggestionDialog.exec", Mock())

    # when the decks in the config are empty on_suggestion_button_press returns early
    monkeypatch.setattr(
        "ankihub.settings.config._private_config.decks",
        {str(ankihub_deck_uuid): Mock()},
    )

    expected_response = {}  # type: ignore
    requests_mock.post(
        f"{API_URL_BASE}/notes/{ankihub_note_uuid}/suggestion/",
        status_code=201,
        json=expected_response,
    )

    with anki_session_with_addon.profile_loaded():
        mw = anki_session_with_addon.mw

        import_sample_ankihub_deck(mw)

        editor = MagicMock()
        editor.note = note = mw.col.get_note(mw.col.find_notes("")[0])
        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = ""

        refresh_suggestion_button(editor)
        assert editor.ankihub_command == AnkiHubCommands.NEW.value
        on_suggestion_button_press(editor)

        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_note_uuid)

        refresh_suggestion_button(editor)
        assert editor.ankihub_command == AnkiHubCommands.CHANGE.value
        on_suggestion_button_press(editor)


def test_get_note_types_in_deck(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(SAMPLE_DECK_APKG) as deck_id:
            # test get note types in deck
            from ankihub.utils import get_note_types_in_deck

            note_model_ids = get_note_types_in_deck(DeckId(deck_id))
            # TODO test on a deck that has more than one note type.
            assert len(note_model_ids) == 2
            assert note_model_ids == [1656968697414, 1656968697418]


def test_note_type_contains_field(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(SAMPLE_DECK_APKG):
            from ankihub.settings import ANKIHUB_NOTE_TYPE_FIELD_NAME
            from ankihub.utils import note_type_contains_field

            note_type = anki_session.mw.col.models.get(SAMPLE_MODEL_ID)
            assert note_type_contains_field(note_type, SAMPLE_MODEL_ID) is False
            new_field = {"name": ANKIHUB_NOTE_TYPE_FIELD_NAME}
            note_type["flds"].append(new_field)
            assert note_type_contains_field(note_type, ANKIHUB_NOTE_TYPE_FIELD_NAME)
            note_type["flds"].remove(new_field)


def test_modify_note_type(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(SAMPLE_DECK_APKG):
            from ankihub.register_decks import modify_note_type
            from ankihub.settings import ANKIHUB_NOTE_TYPE_FIELD_NAME

            note_type = anki_session.mw.col.models.by_name("Basic")
            original_note_type = copy.deepcopy(note_type)
            original_note_template = original_note_type["tmpls"][0]["afmt"]
            modify_note_type(note_type)
            modified_template = note_type["tmpls"][0]["afmt"]
            # # TODO Make more precise assertions.
            assert ANKIHUB_NOTE_TYPE_FIELD_NAME in modified_template
            assert original_note_template != modified_template


def test_create_collaborative_deck_and_upload(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    anki_session = anki_session_with_addon

    from ankihub.db import ankihub_db
    from ankihub.settings import API_URL_BASE

    with anki_session.profile_loaded():
        with anki_session.deck_installed(SAMPLE_DECK_APKG) as deck_id:
            mw = anki_session.mw

            from ankihub.register_decks import create_collaborative_deck

            deck_name = mw.col.decks.name(DeckId(deck_id))

            requests_mock.get(
                f"{API_URL_BASE}/decks/pre-signed-url",
                status_code=200,
                json={"pre_signed_url": "http://fake_url"},
            )
            requests_mock.put(
                "http://fake_url/",
                status_code=200,
            )
            ankihub_deck_uuid = UUID_1
            requests_mock.post(
                f"{API_URL_BASE}/decks/",
                status_code=201,
                json={"deck_id": str(ankihub_deck_uuid)},
            )

            create_collaborative_deck(deck_name, private=False)

            # check that the deck payload is correct
            assert requests_mock.request_history[-2].url == "http://fake_url/"
            payload = json.loads(
                gzip.decompress(requests_mock.request_history[-2].body).decode("utf-8")
            )
            notes_in_deck = [
                mw.col.get_note(nid) for nid in mw.col.find_notes(f"deck:{deck_name}")
            ]
            assert len(payload["notes"]) == len(notes_in_deck)
            assert len(payload["note_types"]) == len(
                set([note.mid for note in notes_in_deck])
            )

            # check that the request to the create deck endpoint is correct
            assert requests_mock.last_request.json() == {
                "key": requests_mock.last_request.json()["key"],
                "name": deck_name,
                "anki_id": deck_id,
                "is_private": False,
            }

            # check that deck info is in db
            assert ankihub_db.ankihub_deck_ids() == [ankihub_deck_uuid]
            assert len(ankihub_db.notes_for_ankihub_deck(str(ankihub_deck_uuid))) == 3


def test_get_deck_by_id(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.ankihub_client import (
        ANKIHUB_DATETIME_FORMAT_STR,
        AnkiHubRequestError,
        Deck,
    )
    from ankihub.settings import API_URL_BASE

    client = AnkiHubClient(hooks=[])

    # test get deck by id
    ankihub_deck_uuid = UUID_1
    date_time = datetime.now(tz=timezone.utc)
    expected_data = {
        "id": str(ankihub_deck_uuid),
        "name": "test",
        "owner": 1,
        "anki_id": 1,
        "csv_last_upload": date_time.strftime(ANKIHUB_DATETIME_FORMAT_STR),
        "csv_notes_filename": "test.csv",
    }

    requests_mock.get(f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/", json=expected_data)
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
    requests_mock.get(f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/", status_code=403)

    try:
        client.get_deck_by_id(ankihub_deck_uuid=ankihub_deck_uuid)  # type: ignore
    except AnkiHubRequestError as e:
        exc = e
    assert exc is not None and exc.response.status_code == 403


def test_suggest_note_upate(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubRequestError, NoteInfo, SuggestionType
    from ankihub.note_conversion import ADDON_INTERNAL_TAGS, ANKI_INTERNAL_TAGS
    from ankihub.settings import API_URL_BASE
    from ankihub.suggestions import suggest_note_update

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        mw = anki_session.mw

        import_sample_ankihub_deck(mw, str(UUID_1))
        notes_data: NoteInfo = ankihub_sample_deck_notes_data()
        note = mw.col.get_note(notes_data[0].anki_nid)
        ankihub_note_uuid = notes_data[0].ankihub_note_uuid

        # test create change note suggestion
        adapter = requests_mock.post(
            f"{API_URL_BASE}/notes/{ankihub_note_uuid}/suggestion/", status_code=201
        )

        note.tags = ["a", *ADDON_INTERNAL_TAGS, *ANKI_INTERNAL_TAGS]
        suggest_note_update(
            note=note,
            change_type=SuggestionType.NEW_CONTENT,
            comment="test",
        )

        # ... assert that internal tags were filtered out
        suggestion_data = adapter.last_request.json()
        assert set(suggestion_data["tags"]) == set(
            [
                "a",
            ]
        )

        # test create change note suggestion unauthenticated
        requests_mock.post(
            f"{API_URL_BASE}/notes/{ankihub_note_uuid}/suggestion/", status_code=403
        )

        try:
            suggest_note_update(
                note=note,
                change_type=SuggestionType.NEW_CONTENT,
                comment="test",
            )
        except AnkiHubRequestError as e:
            exc = e
        assert exc is not None and exc.response.status_code == 403


def test_suggest_new_note(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubRequestError
    from ankihub.note_conversion import ADDON_INTERNAL_TAGS
    from ankihub.settings import API_URL_BASE
    from ankihub.suggestions import suggest_new_note

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        mw = anki_session.mw

        import_sample_ankihub_deck(mw, str(UUID_1))
        note = mw.col.new_note(mw.col.models.by_name("Basic (Testdeck / user1)"))
        ankihub_deck_uuid = UUID_1

        adapter = requests_mock.post(
            f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/note-suggestion/",
            status_code=201,
        )

        note.tags = ["a", *ADDON_INTERNAL_TAGS]
        suggest_new_note(
            note=note,
            ankihub_deck_uuid=ankihub_deck_uuid,
            comment="test",
        )

        # ... assert that add-on internal tags were filtered out
        suggestion_data = adapter.last_request.json()
        assert set(suggestion_data["tags"]) == set(
            [
                "a",
            ]
        )

        # test create change note suggestion unauthenticated
        requests_mock.post(
            f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/note-suggestion/",
            status_code=403,
        )

        try:
            suggest_new_note(
                note=note,
                ankihub_deck_uuid=ankihub_deck_uuid,
                comment="test",
            )
        except AnkiHubRequestError as e:
            exc = e
        assert exc is not None and exc.response.status_code == 403


def test_suggest_notes_in_bulk(anki_session_with_addon: AnkiSession, monkeypatch):
    from uuid import UUID

    from ankihub.ankihub_client import (
        ChangeNoteSuggestion,
        Field,
        NewNoteSuggestion,
        SuggestionType,
    )
    from ankihub.suggestions import suggest_notes_in_bulk

    anki_session = anki_session_with_addon
    bulk_suggestions_method_mock = MagicMock()
    monkeypatch.setattr(
        "ankihub.ankihub_client.AnkiHubClient.create_suggestions_in_bulk",
        bulk_suggestions_method_mock,
    )
    with anki_session.profile_loaded():
        mw = anki_session.mw

        anki_did = import_sample_ankihub_deck(mw, str(UUID_1))

        # add a new note
        new_note = mw.col.new_note(mw.col.models.by_name("Basic (Testdeck / user1)"))
        mw.col.add_note(new_note, deck_id=anki_did)

        new_note_ankihub_uuid = UUID_2
        monkeypatch.setattr("uuid.uuid4", lambda: new_note_ankihub_uuid)

        CHANGED_NOTE_ID = 1608240057545
        changed_note = mw.col.get_note(CHANGED_NOTE_ID)
        changed_note["Front"] = "changed front"
        changed_note.flush()

        # suggest two notes, one new and one updated, check if the client method was called with the correct arguments
        nids = [changed_note.id, new_note.id]
        notes = [mw.col.get_note(nid) for nid in nids]
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
                    ankihub_note_uuid=UUID("67f182c2-7306-47f8-aed6-d7edb42cd7de"),
                    anki_nid=CHANGED_NOTE_ID,
                    fields=[
                        Field(
                            name="Front",
                            order=0,
                            value="changed front",
                        ),
                    ],
                    tags=None,
                    comment="test",
                    change_type=SuggestionType.NEW_CONTENT,
                ),
            ],
            "new_note_suggestions": [
                NewNoteSuggestion(
                    ankihub_note_uuid=new_note_ankihub_uuid,
                    anki_nid=new_note.id,
                    fields=[
                        Field(name="Front", order=0, value=""),
                        Field(name="Back", order=1, value=""),
                    ],
                    tags=[],
                    guid=new_note.guid,
                    comment="test",
                    ankihub_deck_uuid=UUID("1f28bc9e-f36d-4e1d-8720-5dd805f12dd0"),
                    note_type_name="Basic (Testdeck / user1)",
                    anki_note_type_id=1657023668893,
                ),
            ],
            "auto_accept": False,
        }


def test_adjust_note_types(anki_session_with_addon: AnkiSession):
    from ankihub.importing import adjust_note_types
    from ankihub.utils import modify_note_type

    anki_session = anki_session_with_addon
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


def test_reset_note_types_of_notes(anki_session_with_addon: AnkiSession):
    from ankihub.utils import reset_note_types_of_notes

    anki_session = anki_session_with_addon
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


def test_import_new_ankihub_deck(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.sync import AnkiHubImporter
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        # import the apkg to get the note types, then delete the deck
        file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
        importer = AnkiPackageImporter(mw.col, file)
        importer.run()
        mw.col.decks.remove([mw.col.decks.id_for_name("Testdeck")])

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        ankihub_importer = AnkiHubImporter()
        local_did = ankihub_importer._import_ankihub_deck_inner(
            ankihub_did=str(ankihub_deck_uuid),
            notes_data=ankihub_sample_deck_notes_data(),
            deck_name="test",
            remote_note_types={},
            protected_fields={},
            protected_tags=[],
        )
        new_dids = all_dids() - dids_before_import

        assert (
            len(new_dids) == 1
        )  # we have no mechanism for importing subdecks from a csv yet, so ti will be just onen deck
        assert local_did == list(new_dids)[0]

        assert ankihub_importer.num_notes_created == 3
        assert ankihub_importer.num_notes_updated == 0

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_import_existing_ankihub_deck(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.sync import AnkiHubImporter
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        # import the apkg
        file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
        importer = AnkiPackageImporter(mw.col, file)
        importer.run()
        existing_did = mw.col.decks.id_for_name("Testdeck")

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        ankihub_importer = AnkiHubImporter()
        local_did = ankihub_importer._import_ankihub_deck_inner(
            ankihub_did=str(ankihub_deck_uuid),
            notes_data=ankihub_sample_deck_notes_data(),
            deck_name="test",
            remote_note_types={},
            protected_fields={},
            protected_tags=[],
        )
        new_dids = all_dids() - dids_before_import

        assert not new_dids
        assert local_did == existing_did

        # no notes should be changed because they already exist
        assert ankihub_importer.num_notes_created == 0
        assert ankihub_importer.num_notes_updated == 0

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def assert_that_only_ankihub_sample_deck_info_in_database(ankihub_deck_uuid: uuid.UUID):
    from ankihub.db import ankihub_db

    assert ankihub_db.ankihub_deck_ids() == [ankihub_deck_uuid]
    assert len(ankihub_db.notes_for_ankihub_deck(str(ankihub_deck_uuid))) == 3


def test_import_existing_ankihub_deck_2(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.sync import AnkiHubImporter
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
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

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        ankihub_importer = AnkiHubImporter()
        local_did = ankihub_importer._import_ankihub_deck_inner(
            ankihub_did=str(ankihub_deck_uuid),
            notes_data=ankihub_sample_deck_notes_data(),
            deck_name="test",
            remote_note_types={},
            protected_fields={},
            protected_tags=[],
        )
        new_dids = all_dids() - dids_before_import

        # when the existing cards are in multiple seperate decks a new deck is created
        assert len(new_dids) == 1
        assert local_did == list(new_dids)[0]

        # no notes should be changed because they already exist
        assert ankihub_importer.num_notes_created == 0
        assert ankihub_importer.num_notes_updated == 0

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_import_existing_ankihub_deck_3(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.sync import AnkiHubImporter
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        # import the apkg
        file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
        importer = AnkiPackageImporter(mw.col, file)
        importer.run()
        existing_did = mw.col.decks.id_for_name("Testdeck")

        # modify two notes
        note_1 = mw.col.get_note(1608240057545)
        note_1["Front"] = "new front"

        note_2 = mw.col.get_note(1656968819662)
        note_2.tags.append("foo")

        mw.col.update_notes([note_1, note_2])

        # delete one note
        mw.col.remove_notes([1608240029527])

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        ankihub_importer = AnkiHubImporter()
        local_did = ankihub_importer._import_ankihub_deck_inner(
            ankihub_did=str(ankihub_deck_uuid),
            notes_data=ankihub_sample_deck_notes_data(),
            deck_name="test",
            remote_note_types={},
            protected_fields={},
            protected_tags=[],
        )
        new_dids = all_dids() - dids_before_import

        assert not new_dids
        assert local_did == existing_did

        assert ankihub_importer.num_notes_created == 1
        assert ankihub_importer.num_notes_updated == 2

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_update_ankihub_deck(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.sync import AnkiHubImporter
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        first_local_did = import_sample_ankihub_deck(mw)

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        ankihub_importer = AnkiHubImporter()
        second_local_did = ankihub_importer._import_ankihub_deck_inner(
            ankihub_did=str(ankihub_deck_uuid),
            notes_data=ankihub_sample_deck_notes_data(),
            deck_name="test",
            remote_note_types={},
            protected_fields={},
            protected_tags=[],
            local_did=first_local_did,
        )
        new_dids = all_dids() - dids_before_import

        assert len(new_dids) == 0
        assert first_local_did == second_local_did

        # no notes should be changed because they already exist
        assert ankihub_importer.num_notes_created == 0
        assert ankihub_importer.num_notes_updated == 0

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_update_ankihub_deck_when_deck_was_deleted(
    anki_session_with_addon: AnkiSession,
):
    from aqt import mw

    from ankihub.sync import AnkiHubImporter
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        first_local_did = import_sample_ankihub_deck(mw)

        # move cards to another deck and remove the original one
        other_deck = mw.col.decks.add_normal_deck_with_name("other deck").id
        cids = mw.col.find_cards(f"deck:{mw.col.decks.name(first_local_did)}")
        assert len(cids) == 3
        mw.col.set_deck(cids, other_deck)
        mw.col.decks.remove([first_local_did])

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        ankihub_importer = AnkiHubImporter()
        second_local_id = ankihub_importer._import_ankihub_deck_inner(
            ankihub_did=str(ankihub_deck_uuid),
            notes_data=ankihub_sample_deck_notes_data(),
            deck_name="test",
            remote_note_types={},
            protected_fields={},
            protected_tags=[],
            local_did=first_local_did,
        )
        new_dids = all_dids() - dids_before_import

        # deck with first_local_did should be recreated
        assert len(new_dids) == 1
        assert list(new_dids)[0] == first_local_did
        assert second_local_id == first_local_did

        # no notes should be changed because they already exist
        assert ankihub_importer.num_notes_created == 0
        assert ankihub_importer.num_notes_updated == 0

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_suspend_new_cards_of_existing_notes(
    anki_session_with_addon: AnkiSession, monkeypatch
):
    from anki.consts import QUEUE_TYPE_SUSPENDED
    from aqt import AnkiQt

    from ankihub.ankihub_client import Field, NoteInfo
    from ankihub.settings import config
    from ankihub.sync import AnkiHubImporter

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        mw: AnkiQt = anki_session.mw

        ankihub_cloze = create_ankihub_version_of_note_type(
            mw, mw.col.models.by_name("Cloze")
        )

        def test_case(suspend_existing_card_before_update: bool):
            # create a cloze note with one card, optionally suspend the existing card,
            # then update the note using AnkiHubImporter adding a new cloze
            # which results in a new card getting created for the added cloze

            note = mw.col.new_note(ankihub_cloze)
            note["Text"] = "{{c1::foo}}"
            mw.col.add_note(note, 0)

            if suspend_existing_card_before_update:
                # suspend the only card of the note
                card = note.cards()[0]
                card.queue = QUEUE_TYPE_SUSPENDED
                card.flush()

            # update the note using the AnkiHub importer
            note_data = NoteInfo(
                anki_nid=note.id,
                ankihub_note_uuid=UUID_1,
                fields=[Field(name="Text", value="{{c1::foo}} {{c2::bar}}", order=0)],
                tags=[],
                mid=note.model()["id"],
                last_update_type=None,
                guid=note.guid,
            )
            importer = AnkiHubImporter()
            updated_note = importer.update_or_create_note(
                note_data=note_data,
                anki_did=0,
                protected_fields={},
                protected_tags=[],
                first_import_of_deck=False,
            )
            assert len(updated_note.cards()) == 2
            return updated_note

        def new_card(note: Note):
            # the card with the higher was created later
            return max(note.cards(), key=lambda c: c.id)

        # test "always" option
        config.public_config["suspend_new_cards_of_existing_notes"] = "always"

        updated_note = test_case(suspend_existing_card_before_update=False)
        assert new_card(updated_note).queue == QUEUE_TYPE_SUSPENDED

        updated_note = test_case(suspend_existing_card_before_update=True)
        assert new_card(updated_note).queue == QUEUE_TYPE_SUSPENDED

        # test "never" option
        config.public_config["suspend_new_cards_of_existing_notes"] = "never"

        updated_note = test_case(suspend_existing_card_before_update=False)
        assert new_card(updated_note).queue != QUEUE_TYPE_SUSPENDED

        updated_note = test_case(suspend_existing_card_before_update=True)
        assert new_card(updated_note).queue != QUEUE_TYPE_SUSPENDED

        # test "if_siblings_are_suspended" option
        config.public_config[
            "suspend_new_cards_of_existing_notes"
        ] = "if_siblings_are_suspended"

        updated_note = test_case(suspend_existing_card_before_update=False)
        assert all(card.queue != QUEUE_TYPE_SUSPENDED for card in updated_note.cards())

        updated_note = test_case(suspend_existing_card_before_update=True)
        assert all(card.queue == QUEUE_TYPE_SUSPENDED for card in updated_note.cards())


def create_ankihub_version_of_note_type(mw, note_type: NotetypeDict) -> NotetypeDict:
    from ankihub.utils import modify_note_type

    note_type["id"] = 0
    note_type["name"] = note_type["name"] + " (AnkiHub)"
    modify_note_type(note_type)
    mw.col.models.add_dict(note_type)
    return mw.col.models.by_name(note_type["name"])


def test_unsubsribe_from_deck(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.db import ankihub_db
    from ankihub.gui.decks import SubscribedDecksDialog
    from ankihub.utils import ANKIHUB_TEMPLATE_SNIPPET_RE, note_type_contains_field

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        ankihub_did = "1"

        import_sample_ankihub_deck(ankihub_did=ankihub_did, mw=mw)

        mids = ankihub_db.note_types_for_ankihub_deck(ankihub_did)
        assert len(mids) == 2

        SubscribedDecksDialog.unsubscribe_from_deck(ankihub_did)

        # check if note type modifications were removed
        assert all(not note_type_contains_field(mw.col.models.get(mid)) for mid in mids)

        assert all(
            not re.search(
                ANKIHUB_TEMPLATE_SNIPPET_RE, mw.col.models.get(mid)["tmpls"][0]["afmt"]
            )
            for mid in mids
        )

        # check if the deck was removed from the db
        mids = ankihub_db.note_types_for_ankihub_deck(ankihub_did)
        assert len(mids) == 0

        nids = ankihub_db.notes_for_ankihub_deck(ankihub_did)
        assert len(nids) == 0


def import_sample_ankihub_deck(
    mw: aqt.AnkiQt, ankihub_did: Optional[str] = None
) -> DeckId:
    from ankihub.sync import AnkiHubImporter
    from ankihub.utils import all_dids

    if ankihub_did is None:
        ankihub_did = "1"

    # import the apkg to get the note types, then delete the deck
    file = str(ANKIHUB_SAMPLE_DECK_APKG.absolute())
    importer = AnkiPackageImporter(mw.col, file)
    importer.run()
    mw.col.decks.remove([mw.col.decks.id_for_name("Testdeck")])

    dids_before_import = all_dids()
    importer = AnkiHubImporter()
    local_did = importer._import_ankihub_deck_inner(
        ankihub_did=ankihub_did,
        notes_data=ankihub_sample_deck_notes_data(),
        deck_name="test",
        protected_fields={},
        protected_tags=[],
        remote_note_types={},
    )
    new_dids = all_dids() - dids_before_import

    assert len(new_dids) == 1
    assert local_did == list(new_dids)[0]

    return local_did


def test_prepare_note(anki_session_with_addon: AnkiSession):
    from ankihub.ankihub_client import Field, NoteInfo, SuggestionType
    from ankihub.importing import AnkiHubImporter
    from ankihub.note_conversion import (
        ADDON_INTERNAL_TAGS,
        TAG_FOR_PROTECTING_FIELDS,
        TAG_FOR_SUGGESTION_TYPE,
    )
    from ankihub.settings import ANKIHUB_NOTE_TYPE_FIELD_NAME

    anki_session = anki_session_with_addon
    with anki_session_with_addon.profile_loaded():
        mw = anki_session.mw

        ankihub_nid = str(UUID_1)

        def prepare_note(
            note,
            first_import_of_deck: bool,
            tags: List[str] = None,
            fields: Optional[List[Field]] = None,
            protected_fields: Optional[Dict] = {},
            protected_tags: List[str] = [],
            last_update_type: SuggestionType = SuggestionType.NEW_CONTENT,
            guid: Optional[str] = None,
        ):
            note_data = NoteInfo(
                ankihub_note_uuid=str(UUID_1),
                anki_nid=note.id,
                fields=fields or [],
                tags=tags or [],
                mid=note.mid,
                last_update_type=last_update_type,
                guid=note.guid if guid is None else guid,
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

        # create ankihub_basic note type because prepare_note needs a note type with an ankihub_id field
        ankihub_basic = create_ankihub_version_of_note_type(
            mw, mw.col.models.by_name("Basic")
        )

        def example_note() -> Note:
            # create a new note with non-empty fields
            # that has the ankihub_basic note type
            note = mw.col.new_note(ankihub_basic)
            note["Front"] = "old front"
            note["Back"] = "old back"
            note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = ankihub_nid
            note.tags = []
            note.id = 42  # to simulate an existing note
            note.guid = "old guid"
            return note

        new_fields = [
            Field(name="Front", value="new front", order=0),
            Field(name="Back", value="new back", order=1),
        ]
        new_tags = ["c", "d"]

        note = example_note()
        note.tags = ["a", "b"]
        note_was_changed_1 = prepare_note(
            note,
            first_import_of_deck=True,
            fields=new_fields,
            tags=new_tags,
            protected_fields={ankihub_basic["id"]: ["Back"]},
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
            protected_fields={ankihub_basic["id"]: ["Back"]},
            protected_tags=["a"],
        )
        assert not note_was_changed_2
        assert note["Front"] == "new front"
        assert note["Back"] == "old back"
        assert set(note.tags) == set(["a", "c", "d"])

        # assert that the special tag for new notes gets added when a note gets created and first_import_of_deck=False
        note = example_note()
        note.id = 0  # simulate new note
        note_was_changed_3 = prepare_note(note, tags=["a"], first_import_of_deck=False)
        assert note_was_changed_3
        assert set(note.tags) == {"a", "AnkiHub_Update::New_Note"}

        # assert that tags for updated notes get added when a note gets updated and first_import_of_deck=False
        note = example_note()
        note.tags = []
        note_was_changed_4 = prepare_note(
            note,
            tags=["e"],
            first_import_of_deck=False,
            last_update_type=SuggestionType.UPDATED_CONTENT,
        )
        assert note_was_changed_4
        assert set(note.tags) == set(
            ["e", TAG_FOR_SUGGESTION_TYPE[SuggestionType.UPDATED_CONTENT]]
        )

        # assert that addon-internal don't get removed
        note = example_note()
        note.tags = list(ADDON_INTERNAL_TAGS)
        note_was_changed_5 = prepare_note(note, tags=[], first_import_of_deck=True)
        assert not note_was_changed_5
        assert set(note.tags) == set(ADDON_INTERNAL_TAGS)

        # assert that fields protected by tags are in fact protected
        note = example_note()
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
        note = example_note()
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
        note = example_note()
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
        note = example_note()
        note_was_changed_8 = prepare_note(
            note,
            guid="new guid",
            first_import_of_deck=True,
        )
        assert note_was_changed_8
        assert note.guid == "new guid"


def test_prepare_note_protect_field_with_spaces(anki_session_with_addon: AnkiSession):
    from ankihub.ankihub_client import Field, NoteInfo, SuggestionType
    from ankihub.importing import AnkiHubImporter
    from ankihub.note_conversion import TAG_FOR_PROTECTING_FIELDS
    from ankihub.settings import ANKIHUB_NOTE_TYPE_FIELD_NAME

    anki_session = anki_session_with_addon
    with anki_session_with_addon.profile_loaded():
        mw = anki_session.mw

        ankihub_nid = str(UUID_1)

        def prepare_note(
            note,
            first_import_of_deck: bool,
            tags: List[str] = [],
            fields: Optional[List[Field]] = [],
            protected_fields: Optional[Dict] = {},
            protected_tags: List[str] = [],
            last_update_type: str = SuggestionType.NEW_CONTENT,
        ):
            note_data = NoteInfo(
                ankihub_note_uuid=ankihub_nid,
                anki_nid=note.id,
                fields=fields,
                tags=tags,
                mid=note.mid,
                last_update_type=last_update_type,
                guid=note.guid,
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

        # create ankihub_basic note type because prepare_note needs a note type with an ankihub_id field
        ankihub_basic = create_ankihub_version_of_note_type(
            mw, mw.col.models.by_name("Basic")
        )

        field_name_with_spaces = "Field name with spaces"
        ankihub_basic["flds"][0]["name"] = field_name_with_spaces
        ankihub_basic["tmpls"][0]["qfmt"] = ankihub_basic["tmpls"][0]["qfmt"].replace(
            "Front", field_name_with_spaces
        )
        mw.col.models.update_dict(ankihub_basic)

        def example_note() -> Note:
            # create a new note with non-empty fields
            # that has the ankihub_basic note type
            note = mw.col.new_note(ankihub_basic)
            note[field_name_with_spaces] = "old front"
            note["Back"] = "old back"
            note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = ankihub_nid
            note.tags = []
            note.id = 42  # to simulate an existing note
            return note

        # assert that fields with spaces are protected by tags that have spaces replaced by underscores
        note = example_note()
        note.tags = [
            f"{TAG_FOR_PROTECTING_FIELDS}::{field_name_with_spaces.replace(' ', '_')}"
        ]
        note_changed = prepare_note(
            note,
            fields=[Field(name=field_name_with_spaces, value="new front", order=0)],
            first_import_of_deck=True,
        )
        assert not note_changed
        assert note[field_name_with_spaces] == "old front"

        # assert that field is not protected without this tag (to make sure the test is correct)
        note = example_note()
        note_changed = prepare_note(
            note,
            fields=[Field(name=field_name_with_spaces, value="new front", order=0)],
            first_import_of_deck=True,
        )
        assert note_changed
        assert note[field_name_with_spaces] == "new front"


def test_import_deck_and_check_that_values_are_saved_to_databases(
    anki_session_with_addon: AnkiSession,
):
    from ankihub.db import AnkiHubDB
    from ankihub.importing import AnkiHubImporter

    with anki_session_with_addon.profile_loaded():
        mw = anki_session_with_addon.mw

        # import the deck to setup note types
        anki_did = import_sample_ankihub_deck(mw)

        note_data = ankihub_sample_deck_notes_data()[0]

        # set fields and tags of note_data
        # so that we can check if protected fields and tags are handled correctly
        protected_field_name = note_data.fields[0].name
        note_data.fields[0].value = "new field content"
        note_type_id = note_data.mid

        note_data.tags = ["tag1", "tag2"]

        nid = note_data.anki_nid
        note = mw.col.get_note(nid)
        note.tags = ["protected_tag"]

        protected_field_content = "protected field content"
        note[protected_field_name] = protected_field_content

        note.flush()

        importer = AnkiHubImporter()
        importer._import_ankihub_deck_inner(
            ankihub_did=anki_did,
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
        note_data_from_db = AnkiHubDB().note_data(nid)
        # ... last_update_type is not relevant here because it is not saved in the AnkiHub DB
        note_data_from_db.last_update_type = note_data.last_update_type
        assert note_data_from_db == note_data
