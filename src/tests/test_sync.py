import copy
import pathlib
from typing import Callable

from anki.collection import Collection


sample_deck = pathlib.Path(__file__).parent / "data" / "sample_deck.apkg"


def test_get_note_types_in_deck(anki_session) -> None:
    from ankihub.sync import get_note_types_in_deck

    with anki_session.profile_loaded():
        with anki_session.deck_installed(str(sample_deck)) as deck_id:
            mids = get_note_types_in_deck(deck_id)
            assert len(mids) == 1
            assert mids == [1623659872805]


def test_note_type_preparations(
    col: Collection, add_cloze_note_types: Callable
) -> None:
    from ankihub.sync import has_ankihub_field, prepare_note_types
    from ankihub.consts import FIELD_NAME

    mid = add_cloze_note_types(["AnkiHub1"]).pop()
    note_type = col.models.get(mid)
    assert not has_ankihub_field(note_type)

    prev_templs = copy.deepcopy(note_type["tmpls"])
    prepare_note_types([note_type])

    # prepare_note_types added the field
    note_type = col.models.get(mid)
    assert has_ankihub_field(note_type)
    assert any(field["name"] == FIELD_NAME for field in note_type["flds"])

    # prepare_note_types modified the template
    templs = note_type["tmpls"]
    assert len(prev_templs) == len(templs)
    for i, templ in enumerate(templs):
        assert templ != prev_templs[i]["afmt"]


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
