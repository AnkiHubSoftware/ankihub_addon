import copy
import pathlib
from typing import Callable
from pytest_anki import AnkiSession

from anki.collection import Collection

ANKING_MODEL_ID = 1566160514431

anking_deck = str(pathlib.Path(__file__).parent / "data" / "anking.apkg")


def test_get_note_types_in_deck(anki_session: AnkiSession) -> None:
    from ankihub.sync import get_note_types_in_deck

    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck) as deck_id:
            mids = get_note_types_in_deck(deck_id)
            # TODO test for multiple mids in deck
            assert len(mids) == 1
            assert mids == [ANKING_MODEL_ID]


def test_note_type_preparations(anki_session: AnkiSession) -> None:
    from ankihub.sync import has_ankihub_field, prepare_note_type
    from ankihub.consts import ANKIHUB_NOTE_TYPE_FIELD_NAME

    with anki_session.profile_loaded():
        mid = ANKING_MODEL_ID
        with anki_session.deck_installed(anking_deck) as deck_id:
            note_type = anki_session.mw.col.models.get(mid)
            assert not has_ankihub_field(note_type)
            previous_note_template = copy.deepcopy(note_type["tmpls"])
            prepare_note_type(note_type)
            note_type = anki_session.mw.col.models.get(mid)
            assert has_ankihub_field(note_type)
            modified_template = note_type["tmpls"]
            assert len(previous_note_template) == len(modified_template)
            # TODO Make an assertion about the actual diff
            assert previous_note_template != modified_template


def test_get_unprepared_note_types(
    col: Collection, add_cloze_note_types: Callable
) -> None:
    from ankihub.sync import get_unprepared_note_types, prepare_note_types

    names = ["AnkiHub1", "AnkiHub2"]
    mid1, mid2 = add_cloze_note_types(names)
    note_types = get_unprepared_note_types([mid1, mid2])
    assert len(note_types) == 2
    assert note_types[0]["id"] == mid1
    assert note_types[1]["id"] == mid2

    # It shouldn't return mid1 note type after preparing
    prepare_note_types([note_types[0]])
    note_types = get_unprepared_note_types([mid1, mid2])
    assert len(note_types) == 1
    assert note_types[0]["id"] == mid2
