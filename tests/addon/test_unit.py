import uuid
from typing import Callable

from anki.notes import NoteId
from pytest_anki import AnkiSession

from ..factories import NoteInfoFactory


def test_lowest_level_common_ancestor_deck_name():
    from ankihub.utils import lowest_level_common_ancestor_deck_name

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
    from ankihub.importing import updated_tags
    from ankihub.note_conversion import ADDON_INTERNAL_TAGS, TAG_FOR_OPTIONAL_TAGS

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
    from ankihub.error_reporting import normalize_url

    url = "https://app.ankihub.net/api/decks/fc39e7e7-9705-4102-a6ec-90d128c64ed3/updates?since=2022-08-01T1?6%3A32%3A2"
    assert normalize_url(url) == "https://app.ankihub.net/api/decks/<id>/updates"

    url = "https://app.ankihub.net/api/note-types/2385223452/"
    assert normalize_url(url) == "https://app.ankihub.net/api/note-types/<id>/"


def test_prepared_field_html():
    from ankihub.exporting import _prepared_field_html

    assert _prepared_field_html('<img src="foo.jpg">') == '<img src="foo.jpg">'

    assert (
        _prepared_field_html('<img src="foo.jpg" data-editor-shrink="true">')
        == '<img src="foo.jpg">'
    )


def test_remove_note_type_name_modifications():
    from ankihub.register_decks import note_type_name_without_ankihub_modifications

    name = "Basic (deck_name / user_name)"
    assert note_type_name_without_ankihub_modifications(name) == "Basic"

    name = "Basic (deck_name / user_name) (deck_name2 / user_name2)"
    assert note_type_name_without_ankihub_modifications(name) == "Basic"

    name = "Basic (deck_name/user_name)"
    assert note_type_name_without_ankihub_modifications(name) == name


def test_add_subdeck_tags_to_notes(anki_session_with_addon: AnkiSession):
    from ankihub.subdecks import SUBDECK_TAG, add_subdeck_tags_to_notes

    with anki_session_with_addon.profile_loaded():
        mw = anki_session_with_addon.mw

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
    anki_session_with_addon: AnkiSession,
):
    from ankihub.subdecks import SUBDECK_TAG, add_subdeck_tags_to_notes

    with anki_session_with_addon.profile_loaded():
        mw = anki_session_with_addon.mw

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


class TestAnkiNidConflicts:
    def test_conflict_between_two_decks(
        self,
        anki_session_with_addon: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        from ankihub.db import ankihub_db

        with anki_session_with_addon.profile_loaded():
            # save two notes for one deck
            ah_did_1 = next_deterministic_uuid()
            note_info_1 = NoteInfoFactory(
                ankihub_note_uuid=next_deterministic_uuid(), anki_nid=1
            )
            note_info_2 = NoteInfoFactory(
                ankihub_note_uuid=next_deterministic_uuid(), anki_nid=2
            )
            ankihub_db.insert_or_update_notes_data(
                ankihub_did=ah_did_1, notes_data=[note_info_1, note_info_2]
            )

            # save one note for another deck with the same nid as the first note in the first deck
            ah_did_2 = next_deterministic_uuid()
            note_info_3 = NoteInfoFactory(
                ankihub_note_uuid=next_deterministic_uuid(), anki_nid=1
            )

            ankihub_db.insert_or_update_notes_data(
                ankihub_did=ah_did_2, notes_data=[note_info_3]
            )
            assert not ankihub_db.is_active(note_info_3.ankihub_note_uuid)

            # reactivate the deactivated note in the second deck to create a conflict
            ankihub_db.activate_all_notes_in_deck(ah_did_2)

            # check that the two decks are detected as conflicting
            assert set(ankihub_db.all_conflicting_decks()) == set([ah_did_1, ah_did_2])

            # check that the two decks are detected as conflicting
            assert ankihub_db.conflicting_decks(ah_did_1) == [ah_did_2]
            assert ankihub_db.conflicting_decks(ah_did_2) == [ah_did_1]

            # check that the first note in the first deck is detected as conflicting
            (
                conflicting_ah_did,
                conflicting_anki_nids,
            ) = ankihub_db.next_conflict(ah_did_1)
            assert conflicting_ah_did == ah_did_2
            assert conflicting_anki_nids == [1]

            (
                conflicting_ah_did,
                conflicting_anki_nids,
            ) = ankihub_db.next_conflict(ah_did_2)
            assert conflicting_ah_did == ah_did_1
            assert conflicting_anki_nids == [1]

            # deactivate conflicting note for the first deck
            ankihub_db.deactivate_notes_for_deck(
                ankihub_did=ah_did_1, anki_nids=[NoteId(1)]
            )

            # check that the two decks are not longer detected as conflicting
            assert not ankihub_db.all_conflicting_decks()
            assert ankihub_db.conflicting_decks(ah_did_1) == []
            assert ankihub_db.conflicting_decks(ah_did_2) == []
            assert not ankihub_db.next_conflict(ah_did_1)
            assert not ankihub_db.next_conflict(ah_did_2)
