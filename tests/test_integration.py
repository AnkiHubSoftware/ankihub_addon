import copy
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock

from anki.decks import DeckId
from anki.models import NotetypeId
from anki.notes import NoteId
from aqt.importing import AnkiPackageImporter
from pytest_anki import AnkiSession

sample_model_id = NotetypeId(1656968697414)
sample_deck = pathlib.Path(__file__).parent / "test_data" / "small.apkg"

ankihub_sample_deck = pathlib.Path(__file__).parent / "test_data" / "small_ankihub.apkg"
ankihub_sample_deck_notes_data = eval(
    (pathlib.Path(__file__).parent / "test_data" / "small_ankihub.txt").read_text()
)


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
    editor.note.id = 1
    editor.note.fields = ["a", "b", "1"]
    editor.note.tags = ["test_tag"]
    field_value = {ANKIHUB_NOTE_TYPE_FIELD_NAME: ""}
    editor.note.__contains__.return_value = True
    editor.note.__getitem__.side_effect = lambda i: field_value[i]
    refresh_ankihub_button(editor)
    assert editor.ankihub_command == AnkiHubCommands.NEW.value

    field_value[ANKIHUB_NOTE_TYPE_FIELD_NAME] = "6f28bc9e-f36d-4e1d-8720-5dd805f12dd0"
    refresh_ankihub_button(editor)
    assert editor.ankihub_command == AnkiHubCommands.CHANGE.value

    # TODO Mock what this is actually expected to return
    expected_response = {}  # type: ignore
    requests_mock.post(
        f"{API_URL_BASE}/notes/{editor.note.id}/suggestion/",
        status_code=201,
        json=expected_response,
    )
    # This test is quite limited since we don't know how to run this test with a
    # "real," editor, instead of the manually instantiated one above. So for
    # now, this test just checks that on_ankihub_button_press runs without
    # raising any errors.
    monkeypatch.setattr("ankihub.gui.editor.SuggestionDialog.exec", Mock())
    on_ankihub_button_press(editor)


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

    requests_mock.get(f"{API_URL_BASE}/decks/1/updates", json={"notes": []})

    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck) as deck_id:

            from ankihub.register_decks import AnkiHubClient, create_collaborative_deck

            deck_name = anki_session.mw.col.decks.name(DeckId(deck_id))
            with monkeypatch.context() as m:
                m.setattr(AnkiHubClient, "upload_deck", Mock())
                create_collaborative_deck(deck_name)

                requests_mock.get(
                    f"{API_URL_BASE}/decks/pre-signed-url",
                    status_code=200,
                    json={"pre_signed_url": "http://fake_url"},
                )
                requests_mock.put(
                    "http://fake_url",
                    status_code=200,
                )
                requests_mock.post(f"{API_URL_BASE}/decks/", status_code=201)

                from ankihub.register_decks import upload_deck

                upload_deck(DeckId(deck_id))


def test_client_login_and_signout(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.addon_ankihub_client import sign_in_hook, sign_out_hook
    from ankihub.config import config
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[sign_in_hook, sign_out_hook])
    credentials_data = {"username": "test", "password": "testpassword"}
    requests_mock.post(f"{API_URL_BASE}/login/", json={"token": "f4k3t0k3n"})
    requests_mock.post(
        f"{API_URL_BASE}/logout/", json={"token": "f4k3t0k3n"}, status_code=204
    )
    client.login(credentials=credentials_data)
    assert client.session.headers["Authorization"] == "Token f4k3t0k3n"
    assert config.private_config.token == "f4k3t0k3n"

    # test signout
    client.signout()
    assert client.session.headers["Authorization"] == ""
    assert config.private_config.token == ""


def test_upload_deck(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
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
    requests_mock.post(
        f"{API_URL_BASE}/decks/",
        status_code=201,
        json={"anki_id": 1, "key": "small.apkg"},
    )

    # test upload deck
    response = client.upload_deck(file=pathlib.Path(sample_deck), anki_deck_id=1)
    assert response.status_code == 201

    # test upload deck unauthenticated
    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=403)
    response = client.upload_deck(pathlib.Path(sample_deck), anki_deck_id=1)
    assert response.status_code == 403


def test_get_deck_updates(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

    # test get deck updates
    deck_id = 1
    date_object = datetime.now(tz=timezone.utc) - timedelta(days=30)

    timestamp = date_object.timestamp()
    expected_data = {
        "total": 1,
        "current_page": 1,
        "has_next": False,
        "since": timestamp,
        "notes": [
            {
                "deck_id": deck_id,
                "note_id": 1,
                "anki_id": 1,
                "tags": ["New Tag"],
                "fields": [{"name": "Text", "order": 0, "value": "Fake value"}],
            }
        ],
        "protected_fields": {"Basic": ["Back"]},
        "protected_tags": ["Test"],
    }

    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/updates", json=expected_data)
    for response in client.get_deck_updates(
        ankihub_deck_uuid=str(deck_id), since=timestamp  # type: ignore
    ):
        assert response.json() == expected_data

    # test get deck updates unauthenticated
    deck_id = 1
    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/updates", status_code=403)
    for response in client.get_deck_updates(
        ankihub_deck_uuid=str(deck_id), since=timestamp  # type: ignore
    ):
        assert response.status_code == 403


def test_get_deck_by_id(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

    # test get deck by id
    deck_id = 1
    date_time_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    expected_data = {
        "id": deck_id,
        "name": "test",
        "owner": 1,
        "anki_id": 1,
        "csv_last_upload": date_time_str,
        "csv_notes_url": "http://fake-csv-url.com/test.csv",
    }

    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/", json=expected_data)
    response = client.get_deck_by_id(ankihub_deck_uuid=str(deck_id))  # type: ignore
    assert response.json() == expected_data

    # test get deck by id unauthenticated
    deck_id = DeckId(1)
    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/", status_code=403)
    response = client.get_deck_by_id(ankihub_deck_uuid=str(deck_id))  # type: ignore
    assert response.status_code == 403


def test_get_note_by_ankihub_id(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

    # test get not by ankihub id
    note_ankihub_id = "1"
    expected_data = {
        "deck_id": 1,
        "note_id": 1,
        "anki_id": 1,
        "tags": ["New Tag"],
        "fields": [{"name": "Text", "order": 0, "value": "Fake value"}],
    }
    requests_mock.get(f"{API_URL_BASE}/notes/{note_ankihub_id}", json=expected_data)
    response = client.get_note_by_ankihub_id(ankihub_note_uuid=note_ankihub_id)  # type: ignore
    assert response.json() == expected_data

    # test get note by ankihub id unauthenticated
    note_ankihub_id = "1"
    requests_mock.get(f"{API_URL_BASE}/notes/{note_ankihub_id}", status_code=403)
    response = client.get_note_by_ankihub_id(ankihub_note_uuid=note_ankihub_id)  # type: ignore
    assert response.status_code == 403


def test_create_change_note_suggestion(
    anki_session_with_addon: AnkiSession, requests_mock
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE, ChangeTypes

    client = AnkiHubClient(hooks=[])
    # test create change note suggestion
    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=201)
    response = client.create_change_note_suggestion(
        ankihub_note_uuid=str(1),  # type: ignore
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CONTENT,
        comment="",
    )
    assert response.status_code == 201

    # test create change note suggestion unauthenticated
    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=403)
    response = client.create_change_note_suggestion(
        ankihub_note_uuid=str(1),  # type: ignore
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CONTENT,
        comment="",
    )
    assert response.status_code == 403


def test_create_new_note_suggestion(
    anki_session_with_addon: AnkiSession, requests_mock
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE, ChangeTypes

    client = AnkiHubClient(hooks=[])
    # test create new note suggestion
    deck_id = str(uuid.uuid4())
    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=201
    )
    response = client.create_new_note_suggestion(
        ankihub_deck_uuid=deck_id,  # type: ignore
        anki_note_id=1,
        ankihub_note_uuid=str(1),  # type: ignore
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CARD_TO_ADD,
        note_type_name="Basic",
        anki_note_type_id=1,
        comment="",
    )
    assert response.status_code == 201

    # test create new note suggestion unauthenticated
    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=403
    )
    response = client.create_new_note_suggestion(
        ankihub_deck_uuid=deck_id,  # type: ignore
        ankihub_note_uuid=str(1),  # type: ignore
        anki_note_id=1,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CARD_TO_ADD,
        note_type_name="Basic",
        anki_note_type_id=1,
        comment="",
    )
    assert response.status_code == 403


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

        dids_before_import = all_dids()
        local_did = import_ankihub_deck_inner(
            notes_data=ankihub_sample_deck_notes_data,
            deck_name="test",
            remote_note_types=dict(),
            protected_fields={},
            protected_tags=[],
        )
        new_decks = all_dids() - dids_before_import

        assert (
            len(new_decks) == 1
        )  # we have no mechanism for importing subdecks from a csv yet, so ti will be just onen deck
        assert local_did == list(new_decks)[0]


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

        dids_before_import = all_dids()
        local_did = import_ankihub_deck_inner(
            notes_data=ankihub_sample_deck_notes_data,
            deck_name="test",
            remote_note_types=dict(),
            protected_fields={},
            protected_tags=[],
        )
        new_decks = all_dids() - dids_before_import

        assert not new_decks
        assert local_did == existing_did


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

        dids_before_import = all_dids()
        local_did = import_ankihub_deck_inner(
            notes_data=ankihub_sample_deck_notes_data,
            deck_name="test",
            remote_note_types=dict(),
            protected_fields={},
            protected_tags=[],
        )
        new_decks = all_dids() - dids_before_import

        # if the existing cards are in multiple seperate decks a new deck is created deck
        assert len(new_decks) == 1
        assert local_did == list(new_decks)[0]


def test_update_ankihub_deck(anki_session_with_addon: AnkiSession):
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

        first_local_did = import_ankihub_deck_inner(
            notes_data=ankihub_sample_deck_notes_data,
            deck_name="test",
            remote_note_types=dict(),
            protected_fields={},
            protected_tags=[],
        )

        dids_before_import = all_dids()
        second_local_did = import_ankihub_deck_inner(
            notes_data=ankihub_sample_deck_notes_data,
            deck_name="test",
            remote_note_types=dict(),
            protected_fields={},
            protected_tags=[],
            local_did=first_local_did,
        )
        new_decks = all_dids() - dids_before_import

        assert len(new_decks) == 0
        assert first_local_did == second_local_did


def test_update_ankihub_deck_when_deck_was_deleted(
    anki_session_with_addon: AnkiSession,
):
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

        first_local_did = import_ankihub_deck_inner(
            notes_data=ankihub_sample_deck_notes_data,
            deck_name="test",
            remote_note_types=dict(),
            protected_fields={},
            protected_tags=[],
        )

        # move cards to other deck and delete the deck
        other_deck = mw.col.decks.add_normal_deck_with_name("other deck").id
        cids = mw.col.find_cards("deck:Testdeck")
        mw.col.set_deck(cids, other_deck)
        mw.col.decks.remove([first_local_did])

        dids_before_import = all_dids()
        second_local_did = import_ankihub_deck_inner(
            notes_data=ankihub_sample_deck_notes_data,
            deck_name="test",
            remote_note_types=dict(),
            protected_fields={},
            protected_tags=[],
            local_did=first_local_did,
        )
        new_decks = all_dids() - dids_before_import

        # deck with first_local_did should be recreated
        assert len(new_decks) == 1
        assert list(new_decks)[0] == first_local_did
        assert second_local_did == first_local_did


def test_prepare_note(anki_session_with_addon: AnkiSession):
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
                {"name": "Front", "value": "new front"},
                {"name": "Back", "value": "new back"},
            ],
            tags=["c", "d"],
            protected_fields={ankihub_basic["id"]: ["Back"]},
            protected_tags=["a"],
        )

        # assert that the note was modified but the protected fields and tags were not
        assert note["Front"] == "new front"
        assert note["Back"] == "old back"
        assert set(note.tags) == set(["a", "c", "d"])
