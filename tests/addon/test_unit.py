import os
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Generator, List

import pytest
from anki.notes import Note, NoteId
from pytest_anki import AnkiSession

from ..factories import NoteInfoFactory

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub import suggestions
from ankihub.db.db import _AnkiHubDB
from ankihub.errors import _normalize_url
from ankihub.exporting import _prepared_field_html
from ankihub.importing import updated_tags
from ankihub.note_conversion import ADDON_INTERNAL_TAGS, TAG_FOR_OPTIONAL_TAGS
from ankihub.register_decks import note_type_name_without_ankihub_modifications
from ankihub.subdecks import SUBDECK_TAG, add_subdeck_tags_to_notes
from ankihub.utils import lowest_level_common_ancestor_deck_name


@pytest.fixture
def ankihub_db() -> Generator[_AnkiHubDB, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db = _AnkiHubDB()
        db_path = Path(temp_dir) / "ankihub.db"
        db.setup_and_migrate(db_path)
        yield db


class TestUploadImagesForSuggestion:
    def test_update_asset_names_on_notes(
        self, anki_session_with_addon_data: AnkiSession
    ):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            note_contents = [
                'Sample Text <div> abc <img src="test.png"> </div>',
                "<span> a</span><img src='other_test.gif' width='250'><div></div>",
                '<span> <p>this note will not have its image replaced </p> <img src="will_not_replace.jpeg"> </span>',
            ]

            notes: List[Note] = []
            mw.col.decks.add_normal_deck_with_name("MediaTestDeck")
            for content in note_contents:
                note = mw.col.new_note(mw.col.models.by_name("Basic"))
                notes.append(note)
                note["Front"] = content
                mw.col.add_note(note, mw.col.decks.by_name("MediaTestDeck")["id"])

            hashed_name_map = {
                "test.png": "fueriwhfvureivhnaowuyiegrofuaywwqg.png",
                "other_test.gif": "fWJKERDVNMOWIKJCIWJefgjnverf.gif",
            }

            suggestions._update_asset_names_on_notes(hashed_name_map)

            notes[0].load()
            notes[1].load()
            notes[2].load()

            assert f'<img src="{hashed_name_map["test.png"]}">' in " ".join(
                notes[0].fields
            )
            assert (
                f"<img src='{hashed_name_map['other_test.gif']}' width='250'>"
                in " ".join(notes[1].fields)
            )
            assert '<img src="will_not_replace.jpeg">' in " ".join(notes[2].fields)


def test_lowest_level_common_ancestor_deck_name():

    deck_names = [
        "A",
        "A::B",
    ]
    assert lowest_level_common_ancestor_deck_name(deck_names) == "A"

    deck_names = [
        "A::B::C",
        "A::B::C::D",
        "A::B",
    ]
    assert lowest_level_common_ancestor_deck_name(deck_names) == "A::B"

    deck_names = ["A::B::C", "A::B::C::D", "A::B", "B"]
    assert lowest_level_common_ancestor_deck_name(deck_names) is None


def test_updated_tags():
    assert set(
        updated_tags(
            cur_tags=[],
            incoming_tags=["A", "B"],
            protected_tags=[],
        )
    ) == set(["A", "B"])

    # dont delete protected tags
    assert set(
        updated_tags(
            cur_tags=["A", "B"],
            incoming_tags=[],
            protected_tags=["A"],
        )
    ) == set(["A"])

    # dont delete tags that contain protected tags
    assert set(
        updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["A"],
        )
    ) == set(["A::B::C"])

    assert set(
        updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["B"],
        )
    ) == set(["A::B::C"])

    assert set(
        updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["C"],
        )
    ) == set(["A::B::C"])

    # keep add-on internal tags
    assert set(
        updated_tags(
            cur_tags=ADDON_INTERNAL_TAGS,
            incoming_tags=[],
            protected_tags=[],
        )
    ) == set(ADDON_INTERNAL_TAGS)

    # keep Anki internal tags
    assert set(
        updated_tags(
            cur_tags=["marked", "leech"],
            incoming_tags=[],
            protected_tags=[],
        )
    ) == set(["marked", "leech"])

    # keep optional tags
    optional_tag = f"{TAG_FOR_OPTIONAL_TAGS}::tag_group::tag"
    assert set(
        updated_tags(
            cur_tags=[optional_tag],
            incoming_tags=[],
            protected_tags=[],
        )
    ) == set([optional_tag])


def test_normalize_url():
    url = "https://app.ankihub.net/api/decks/fc39e7e7-9705-4102-a6ec-90d128c64ed3/updates?since=2022-08-01T1?6%3A32%3A2"
    assert _normalize_url(url) == "https://app.ankihub.net/api/decks/<id>/updates"

    url = "https://app.ankihub.net/api/note-types/2385223452/"
    assert _normalize_url(url) == "https://app.ankihub.net/api/note-types/<id>/"


def test_prepared_field_html():
    assert _prepared_field_html('<img src="foo.jpg">') == '<img src="foo.jpg">'

    assert (
        _prepared_field_html('<img src="foo.jpg" data-editor-shrink="true">')
        == '<img src="foo.jpg">'
    )


def test_remove_note_type_name_modifications():
    name = "Basic (deck_name / user_name)"
    assert note_type_name_without_ankihub_modifications(name) == "Basic"

    name = "Basic (deck_name / user_name) (deck_name2 / user_name2)"
    assert note_type_name_without_ankihub_modifications(name) == "Basic"

    name = "Basic (deck_name/user_name)"
    assert note_type_name_without_ankihub_modifications(name) == name


def test_add_subdeck_tags_to_notes(anki_session_with_addon_data: AnkiSession):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        mw.col.decks.add_normal_deck_with_name("A::B::C")

        note1 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note1["Front"] = "note1"
        mw.col.add_note(note1, mw.col.decks.by_name("A")["id"])

        note2 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note2["Front"] = "note2"
        mw.col.add_note(note2, mw.col.decks.by_name("A::B")["id"])

        note3 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note3["Front"] = "note3"
        mw.col.add_note(note3, mw.col.decks.by_name("A::B::C")["id"])

        add_subdeck_tags_to_notes("A", ankihub_deck_name="Test")

        note1.load()
        assert note1.tags == [f"{SUBDECK_TAG}::Test"]

        note2.load()
        assert note2.tags == [f"{SUBDECK_TAG}::Test::B"]

        note3.load()
        assert note3.tags == [f"{SUBDECK_TAG}::Test::B::C"]


def test_add_subdeck_tags_to_notes_with_spaces_in_deck_name(
    anki_session_with_addon_data: AnkiSession,
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        mw.col.decks.add_normal_deck_with_name(" a a :: b b :: c c ")

        note1 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note1["Front"] = "note1"
        mw.col.add_note(note1, mw.col.decks.by_name(" a a ")["id"])

        note2 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note2["Front"] = "note2"
        mw.col.add_note(note2, mw.col.decks.by_name(" a a :: b b ")["id"])

        note3 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note3["Front"] = "note3"
        mw.col.add_note(note3, mw.col.decks.by_name(" a a :: b b :: c c ")["id"])

        add_subdeck_tags_to_notes(" a a ", ankihub_deck_name="AA")

        note1.load()
        assert note1.tags == [f"{SUBDECK_TAG}::AA"]

        note2.load()
        assert note2.tags == [f"{SUBDECK_TAG}::AA::b_b"]

        note3.load()
        assert note3.tags == [f"{SUBDECK_TAG}::AA::b_b::c_c"]


class TestAnkiHubDB:
    def test_anki_nids_to_ankihub_nids(
        self,
        ankihub_db: _AnkiHubDB,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        existing_anki_nid = 1
        non_existing_anki_nid = 2

        note = NoteInfoFactory.create(
            anki_nid=existing_anki_nid,
        )

        # Add a note to the DB.
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did,
            notes_data=[note],
        )

        # Retrieve a dict of anki_nid -> ankihub_note_uuid for two anki_nids.
        ah_nids_for_anki_nids = ankihub_db.anki_nids_to_ankihub_nids(
            anki_nids=[NoteId(existing_anki_nid), NoteId(non_existing_anki_nid)]
        )

        assert ah_nids_for_anki_nids == {
            existing_anki_nid: note.ankihub_note_uuid,
            non_existing_anki_nid: None,
        }
