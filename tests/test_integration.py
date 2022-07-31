import copy
import pathlib
import re
import uuid
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, Mock

import aqt
from anki.decks import DeckId
from anki.models import NotetypeId
from anki.notes import NoteId
from aqt.importing import AnkiPackageImporter
from pytest_anki import AnkiSession

sample_model_id = NotetypeId(1656968697414)
sample_deck = pathlib.Path(__file__).parent / "test_data" / "small.apkg"

ankihub_sample_deck = pathlib.Path(__file__).parent / "test_data" / "small_ankihub.apkg"

UUID_1 = uuid.UUID("1f28bc9e-f36d-4e1d-8720-5dd805f12dd0")
UUID_2 = uuid.UUID("2f28bc9e-f36d-4e1d-8720-5dd805f12dd0")


def ankihub_sample_deck_notes_data():
    from ankihub.ankihub_client import NoteUpdate, transform_notes_data

    notes_data_raw = eval(
        (pathlib.Path(__file__).parent / "test_data" / "small_ankihub.txt").read_text()
    )
    notes_data_raw = transform_notes_data(notes_data_raw)
    result = [NoteUpdate.from_dict(x) for x in notes_data_raw]
    return result


def test_entry_point(anki_session_with_addon: AnkiSession):
    from aqt.main import AnkiQt

    from ankihub import entry_point

    mw = entry_point.run()
    assert isinstance(mw, AnkiQt)


def test_editor(anki_session_with_addon: AnkiSession, requests_mock, monkeypatch):
    from ankihub.constants import (
        ANKIHUB_NOTE_TYPE_FIELD_NAME,
        API_URL_BASE,
        AnkiHubCommands,
    )
    from ankihub.gui.editor import (
        on_ankihub_button_press,
        refresh_ankihub_button,
        setup,
    )

    setup()
    editor = MagicMock()
    editor.mw = MagicMock()
    editor.note = MagicMock()
    editor.web = MagicMock()

    ankihub_note_uuid = UUID_1
    editor.note.fields = ["a", "b", str(ankihub_note_uuid)]
    editor.note.tags = ["test_tag"]
    field_value = {ANKIHUB_NOTE_TYPE_FIELD_NAME: ""}
    editor.note.__contains__.return_value = True
    editor.note.__getitem__.side_effect = lambda k: field_value[k]

    # TODO Mock what this is actually expected to return
    expected_response = {}  # type: ignore
    requests_mock.post(
        f"{API_URL_BASE}/notes/{ankihub_note_uuid}/suggestion/",
        status_code=201,
        json=expected_response,
    )

    monkeypatch.setattr("ankihub.gui.editor.SuggestionDialog.exec", Mock())

    # when the decks in the config are empty on_ankihub_button_press returns early
    monkeypatch.setattr(
        "ankihub.gui.editor.config.private_config.decks", {str(UUID_1): Mock()}
    )

    # this makes it so that the note is added to the first ankihub deck from the list
    # it could be any deck, we just don't want the dialog to open
    monkeypatch.setattr("ankihub.gui.editor.chooseList", lambda *args, **kwargs: 0)

    refresh_ankihub_button(editor)
    assert editor.ankihub_command == AnkiHubCommands.NEW.value
    on_ankihub_button_press(editor)

    field_value[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_note_uuid)

    refresh_ankihub_button(editor)
    assert editor.ankihub_command == AnkiHubCommands.CHANGE.value
    on_ankihub_button_press(editor)

    # This test is quite limited since we don't know how to run this test with a
    # "real," editor, instead of the manually instantiated one above. So for
    # now, this test just checks that on_ankihub_button_press runs without
    # raising any errors.


def test_get_note_types_in_deck(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck) as deck_id:
            # test get note types in deck
            from ankihub.utils import get_note_types_in_deck

            note_model_ids = get_note_types_in_deck(DeckId(deck_id))
            # TODO test on a deck that has more than one note type.
            assert len(note_model_ids) == 2
            assert note_model_ids == [1656968697414, 1656968697418]


def test_note_type_contains_field(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck):
            from ankihub.constants import ANKIHUB_NOTE_TYPE_FIELD_NAME
            from ankihub.utils import note_type_contains_field

            note_type = anki_session.mw.col.models.get(sample_model_id)
            assert note_type_contains_field(note_type, sample_model_id) is False
            new_field = {"name": ANKIHUB_NOTE_TYPE_FIELD_NAME}
            note_type["flds"].append(new_field)
            assert note_type_contains_field(note_type, ANKIHUB_NOTE_TYPE_FIELD_NAME)
            note_type["flds"].remove(new_field)


def test_modify_note_type(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck):
            from ankihub.constants import ANKIHUB_NOTE_TYPE_FIELD_NAME
            from ankihub.register_decks import modify_note_type

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

    from ankihub.constants import API_URL_BASE
    from ankihub.db import AnkiHubDB

    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck) as deck_id:

            from ankihub.register_decks import create_collaborative_deck

            deck_name = anki_session.mw.col.decks.name(DeckId(deck_id))

            requests_mock.get(
                f"{API_URL_BASE}/decks/pre-signed-url",
                status_code=200,
                json={"pre_signed_url": "http://fake_url"},
            )
            requests_mock.put(
                "http://fake_url",
                status_code=200,
            )
            ankihub_deck_uuid = UUID_1
            requests_mock.post(
                f"{API_URL_BASE}/decks/",
                status_code=201,
                json={"deck_id": str(ankihub_deck_uuid)},
            )

            create_collaborative_deck(deck_name)

            # check if deck info is in db
            db = AnkiHubDB()
            assert db.ankihub_deck_ids() == [str(ankihub_deck_uuid)]
            assert len(db.notes_for_ankihub_deck(str(ankihub_deck_uuid))) == 3


def test_upload_deck(anki_session_with_addon: AnkiSession, monkeypatch):

    # check if moving cards temporarily from filtered decks into the main deck doesn't throw errors
    # and if the cards are moved back to the filtered decks at the end
    with anki_session_with_addon.profile_loaded():
        from aqt import mw

        from ankihub.register_decks import upload_deck

        # create a deck
        main_did = mw.col.decks.add_normal_deck_with_name("main").id
        print(f"{main_did=}")

        # add a note to it
        note = mw.col.new_note(mw.col.models.by_name("Basic"))
        note["Front"] = "test"
        mw.col.add_note(note, main_did)

        # move card of note into filtered deck
        # decks created by new_filtered have a term of "" and limit of 100 by default
        # so the card will be moved into the deck
        filtered_did = mw.col.decks.new_filtered("filtered")
        mw.col.sched.rebuild_filtered_deck(filtered_did)
        assert mw.col.get_note(note.id).cards()[0].did == filtered_did

        monkeypatch.setattr("ankihub.register_decks.AnkiHubClient.upload_deck", Mock())
        upload_deck(main_did)

        # assert that note is still in the filtered deck after upload_deck
        card = mw.col.get_note(note.id).cards()[0]
        assert card.did == filtered_did


def test_client_login_and_signout(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient()
    credentials_data = {"username": "test", "password": "testpassword"}
    requests_mock.post(f"{API_URL_BASE}/login/", json={"token": "f4k3t0k3n"})
    requests_mock.post(
        f"{API_URL_BASE}/logout/", json={"token": "f4k3t0k3n"}, status_code=204
    )

    # test login
    token = client.login(credentials=credentials_data)
    assert token == "f4k3t0k3n"
    assert client.session.headers["Authorization"] == "Token f4k3t0k3n"

    # test signout
    client.signout()
    assert client.session.headers["Authorization"] == ""


def test_client_upload_deck(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.ankihub_client import AnkiHubRequestError
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

    requests_mock.get(
        f"{API_URL_BASE}/decks/pre-signed-url",
        status_code=200,
        json={"pre_signed_url": "http://fake_url"},
    )
    requests_mock.put(
        "http://fake_url",
        status_code=200,
    )
    ankihub_deck_uuid = UUID_1
    requests_mock.post(
        f"{API_URL_BASE}/decks/",
        status_code=201,
        json={"anki_id": 1, "key": "small.apkg", "deck_id": str(ankihub_deck_uuid)},
    )

    # test upload deck
    client.upload_deck(file=pathlib.Path(sample_deck), anki_deck_id=1)

    # test upload deck unauthenticated
    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=403)
    exc = None
    try:
        client.upload_deck(pathlib.Path(sample_deck), anki_deck_id=1)
    except AnkiHubRequestError as e:
        exc = e
    assert exc is not None and exc.response.status_code == 403


def test_get_deck_updates(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.ankihub_client import (
        AnkiHubRequestError,
        DeckUpdateChunk,
        FieldUpdate,
        NoteUpdate,
    )
    from ankihub.constants import API_URL_BASE

    # test get deck updates
    ankihub_deck_uuid = UUID_1
    ankihub_note_uuid = UUID_2
    timestamp = "2022-04-05T10:56:19.456+00:00"
    expected_data = {
        "total": 1,
        "current_page": 1,
        "has_next": False,
        "latest_update": timestamp,
        "notes": [
            {
                "fields": [{"name": "Text", "order": 0, "value": "Fake value"}],
                "deck_id": str(ankihub_deck_uuid),
                "note_id": str(ankihub_note_uuid),
                "anki_id": 1,
                "note_type_id": 1,
                "tags": ["New Tag"],
            }
        ],
        "protected_fields": {1: ["Back"]},
        "protected_tags": ["Test"],
    }

    requests_mock.get(
        f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/updates", json=expected_data
    )

    client = AnkiHubClient(hooks=[])
    deck_updates = list(
        client.get_deck_updates(
            ankihub_deck_uuid=ankihub_deck_uuid, since=timestamp  # type: ignore
        )
    )
    assert len(deck_updates) == 1
    assert deck_updates[0] == DeckUpdateChunk(
        latest_update=timestamp,
        notes=[
            NoteUpdate(
                fields=[FieldUpdate(name="Text", order=0, value="Fake value")],
                ankihub_note_uuid=ankihub_note_uuid,
                mid=1,
                anki_nid=1,
                tags=["New Tag"],
            )
        ],
        protected_fields={1: ["Back"]},
        protected_tags=["Test"],
    )

    # test get deck updates unauthenticated
    requests_mock.get(
        f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/updates", status_code=403
    )

    exc = None
    try:
        for response in client.get_deck_updates(
            ankihub_deck_uuid=ankihub_deck_uuid, since=timestamp  # type: ignore
        ):
            pass
    except AnkiHubRequestError as e:
        exc = e
    assert exc is not None and exc.response.status_code == 403


def test_get_deck_by_id(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.ankihub_client import AnkiHubRequestError, DeckInfo
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

    # test get deck by id
    ankihub_deck_uuid = UUID_1
    date_time_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    expected_data = {
        "id": str(ankihub_deck_uuid),
        "name": "test",
        "owner": 1,
        "anki_id": 1,
        "csv_last_upload": date_time_str,
        "csv_notes_filename": "test.csv",
    }

    requests_mock.get(f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/", json=expected_data)
    deck_info = client.get_deck_by_id(ankihub_deck_uuid=ankihub_deck_uuid)  # type: ignore
    assert deck_info == DeckInfo(
        ankihub_deck_uuid=ankihub_deck_uuid,
        anki_did=1,
        owner=True,
        name="test",
        csv_last_upload=date_time_str,
        csv_notes_filename="test.csv",
    )

    # test get deck by id unauthenticated
    requests_mock.get(f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/", status_code=403)

    try:
        client.get_deck_by_id(ankihub_deck_uuid=ankihub_deck_uuid)  # type: ignore
    except AnkiHubRequestError as e:
        exc = e
    assert exc is not None and exc.response.status_code == 403


def test_create_change_note_suggestion(
    anki_session_with_addon: AnkiSession, requests_mock
):
    from ankihub.ankihub_client import AnkiHubClient, AnkiHubRequestError
    from ankihub.constants import API_URL_BASE, ChangeTypes

    client = AnkiHubClient(hooks=[])
    # test create change note suggestion
    ankihub_note_uuid = UUID_1
    requests_mock.post(
        f"{API_URL_BASE}/notes/{ankihub_note_uuid}/suggestion/", status_code=201
    )
    client.create_change_note_suggestion(
        ankihub_note_uuid=ankihub_note_uuid,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CONTENT,
        comment="",
    )

    # test create change note suggestion unauthenticated
    requests_mock.post(
        f"{API_URL_BASE}/notes/{ankihub_note_uuid}/suggestion/", status_code=403
    )
    try:
        client.create_change_note_suggestion(
            ankihub_note_uuid=ankihub_note_uuid,
            fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
            tags=["test"],
            change_type=ChangeTypes.NEW_CONTENT,
            comment="",
        )
    except AnkiHubRequestError as e:
        exc = e
    assert exc is not None and exc.response.status_code == 403


def test_create_new_note_suggestion(
    anki_session_with_addon: AnkiSession, requests_mock
):
    from ankihub.ankihub_client import AnkiHubClient, AnkiHubRequestError
    from ankihub.constants import API_URL_BASE, ChangeTypes

    client = AnkiHubClient(hooks=[])
    # test create new note suggestion
    ankihub_deck_uuid = UUID_1
    ankihub_note_uuid = UUID_2
    requests_mock.post(
        f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/note-suggestion/", status_code=201
    )
    client.create_new_note_suggestion(
        ankihub_deck_uuid=ankihub_deck_uuid,
        ankihub_note_uuid=ankihub_note_uuid,
        anki_note_id=1,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CARD_TO_ADD,
        note_type_name="Basic",
        anki_note_type_id=1,
        comment="",
    )

    # test create new note suggestion unauthenticated
    requests_mock.post(
        f"{API_URL_BASE}/decks/{ankihub_deck_uuid}/note-suggestion/", status_code=403
    )
    try:
        client.create_new_note_suggestion(
            ankihub_deck_uuid=ankihub_deck_uuid,
            ankihub_note_uuid=ankihub_note_uuid,
            anki_note_id=1,
            fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
            tags=["test"],
            change_type=ChangeTypes.NEW_CARD_TO_ADD,
            note_type_name="Basic",
            anki_note_type_id=1,
            comment="",
        )
    except AnkiHubRequestError as e:
        exc = e
    assert exc is not None and exc.response.status_code == 403


def test_adjust_note_types(anki_session_with_addon: AnkiSession):
    from ankihub.sync import adjust_note_types
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

    from ankihub.sync import import_ankihub_deck_inner
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        # import the apkg to get the note types, then delete the deck
        file = str(ankihub_sample_deck.absolute())
        importer = AnkiPackageImporter(mw.col, file)
        importer.run()
        mw.col.decks.remove([mw.col.decks.id_for_name("Testdeck")])

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        local_did = import_ankihub_deck_inner(
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

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_import_existing_ankihub_deck(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.sync import import_ankihub_deck_inner
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        # import the apkg
        file = str(ankihub_sample_deck.absolute())
        importer = AnkiPackageImporter(mw.col, file)
        importer.run()
        existing_did = mw.col.decks.id_for_name("Testdeck")

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        local_did = import_ankihub_deck_inner(
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

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def assert_that_only_ankihub_sample_deck_info_in_database(ankihub_deck_uuid: uuid.UUID):
    from ankihub.db import AnkiHubDB

    db = AnkiHubDB()
    assert db.ankihub_deck_ids() == [str(ankihub_deck_uuid)]
    assert len(db.notes_for_ankihub_deck(str(ankihub_deck_uuid))) == 4


def test_import_existing_ankihub_deck_2(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.sync import import_ankihub_deck_inner
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        # import the apkg
        file = str(ankihub_sample_deck.absolute())
        importer = AnkiPackageImporter(mw.col, file)
        importer.run()

        # move one card to another deck
        other_deck_id = mw.col.decks.add_normal_deck_with_name("other deck").id
        cids = mw.col.find_cards("deck:Testdeck")
        mw.col.set_deck([cids[0]], other_deck_id)

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        local_did = import_ankihub_deck_inner(
            ankihub_did=str(ankihub_deck_uuid),
            notes_data=ankihub_sample_deck_notes_data(),
            deck_name="test",
            remote_note_types={},
            protected_fields={},
            protected_tags=[],
        )
        new_dids = all_dids() - dids_before_import

        # if the existing cards are in multiple seperate decks a new deck is created deck
        assert len(new_dids) == 1
        assert local_did == list(new_dids)[0]

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_update_ankihub_deck(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.sync import import_ankihub_deck_inner
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        first_local_did = import_sample_ankihub_deck(mw)

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        second_local_did = import_ankihub_deck_inner(
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

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_update_ankihub_deck_when_deck_was_deleted(
    anki_session_with_addon: AnkiSession,
):
    from aqt import mw

    from ankihub.sync import import_ankihub_deck_inner
    from ankihub.utils import all_dids

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():

        first_local_did = import_sample_ankihub_deck(mw)

        # move cards to other deck and delete the deck
        other_deck = mw.col.decks.add_normal_deck_with_name("other deck").id
        cids = mw.col.find_cards("deck:Testdeck")
        mw.col.set_deck(cids, other_deck)
        mw.col.decks.remove([first_local_did])

        ankihub_deck_uuid = UUID_1
        dids_before_import = all_dids()
        second_local_id = import_ankihub_deck_inner(
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

        assert_that_only_ankihub_sample_deck_info_in_database(
            ankihub_deck_uuid=ankihub_deck_uuid
        )


def test_unsubsribe_from_deck(anki_session_with_addon: AnkiSession):
    from aqt import mw

    from ankihub.db import AnkiHubDB
    from ankihub.gui.decks import SubscribedDecksDialog
    from ankihub.utils import ANKIHUB_TEMPLATE_SNIPPET_RE, note_type_contains_field

    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        ankihub_did = "1"

        import_sample_ankihub_deck(ankihub_did=ankihub_did, mw=mw)

        db = AnkiHubDB()
        mids = db.note_types_for_ankihub_deck(ankihub_did)
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
        mids = db.note_types_for_ankihub_deck(ankihub_did)
        assert len(mids) == 0

        nids = db.notes_for_ankihub_deck(ankihub_did)
        assert len(nids) == 0


def import_sample_ankihub_deck(mw: aqt.AnkiQt, ankihub_did: Optional[str] = None):
    from ankihub.sync import import_ankihub_deck_inner
    from ankihub.utils import all_dids

    if ankihub_did is None:
        ankihub_did = "1"

    # import the apkg to get the note types, then delete the deck
    file = str(ankihub_sample_deck.absolute())
    importer = AnkiPackageImporter(mw.col, file)
    importer.run()
    mw.col.decks.remove([mw.col.decks.id_for_name("Testdeck")])

    dids_before_import = all_dids()
    local_did = import_ankihub_deck_inner(
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
    from ankihub.ankihub_client import FieldUpdate
    from ankihub.sync import prepare_note
    from ankihub.utils import modify_note_type

    anki_session = anki_session_with_addon
    with anki_session_with_addon.profile_loaded():
        mw = anki_session.mw

        # create ankihub_basic note type because prepare_note needs a note type with an ankihub_id field
        basic = mw.col.models.by_name("Basic")
        modify_note_type(basic)
        basic["id"] = 0
        basic["name"] = "Basic (AnkiHub)"
        ankihub_basic_mid = NotetypeId(mw.col.models.add_dict(basic).id)
        ankihub_basic = mw.col.models.get(ankihub_basic_mid)

        # create a new note with non-empty fields and tags
        note = mw.col.new_note(ankihub_basic)
        note["Front"] = "old front"
        note["Back"] = "old back"
        note.tags = ["a", "b"]

        # prepare_note
        prepare_note(
            note=note,
            ankihub_id="1",
            fields=[
                FieldUpdate(name="Front", value="new front", order=0),
                FieldUpdate(name="Back", value="new back", order=1),
            ],
            tags=["c", "d"],
            protected_fields={ankihub_basic["id"]: ["Back"]},
            protected_tags=["a"],
        )

        # assert that the note was modified but the protected fields and tags were not
        assert note["Front"] == "new front"
        assert note["Back"] == "old back"
        assert set(note.tags) == set(["a", "c", "d"])
