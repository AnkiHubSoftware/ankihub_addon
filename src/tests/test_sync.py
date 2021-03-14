import copy
from typing import Callable, Generator, List, Iterable

import pytest

from anki.collection import Collection
from anki.notes import Note


@pytest.fixture
def add_deck(col: Collection) -> Generator[Callable, None, None]:
    """The deck with all its cards are removed afterwards"""
    dids = []

    def add_deck() -> int:
        did = col.decks.id("AnkiHub Deck")
        dids.append(did)
        return did

    yield add_deck

    for did in dids:
        col.decks.rem(did)


@pytest.fixture
def add_filtered_deck(col: Collection) -> Generator[Callable, None, None]:
    dids = []

    def _add_filtered_deck(search: str) -> int:
        did = col.decks.new_filtered("AnkiHub Filtered")
        dids.append(did)
        deck = col.decks.get(did)
        deck["terms"] = [[search, 100, 1]]
        col.decks.save(deck)
        return did

    yield _add_filtered_deck

    for did in dids:
        col.decks.rem(did)


@pytest.fixture
def add_cloze_note_types(col: Collection) -> Generator[Callable, None, None]:
    """The note type with all its cards are removed afterwards."""

    notes = []

    def _add_cloze_note_types(names: Iterable[str]) -> List[int]:
        mids = []
        for name in names:
            cloze_note_type = col.models.byName("Cloze")
            ah_note_type = col.models.copy(cloze_note_type)
            ah_note_type["name"] = name
            extra_field = col.models.new_field("Extra")
            col.models.add_field(ah_note_type, extra_field)
            col.models.add(ah_note_type)
            mid = col.models.id_for_name(name)
            mids.append(mid)
            notes.append(ah_note_type)
        return mids

    yield _add_cloze_note_types

    for note in notes:
        col.models.rem(note)


def add_cloze_notes(
    col: Collection, mid: int, did: int, card_count: int = 10
) -> List[Note]:
    "This function assumes note type is cloze type, and has field 'Text' and 'Extra'."

    def create_note(text: str, extra: str) -> Note:
        note_type = col.models.get(mid)
        note = Note(col, note_type)
        note["Text"] = text
        note["Extra"] = extra
        return note

    notes = []
    for i in range(card_count):
        note = create_note("cloze {{c1::abc}}" + str(i), "extra text")
        col.add_note(note, did)
        notes.append(note)
    return notes


def test_get_note_types_in_deck(
    col: Collection,
    add_deck: Callable,
    add_cloze_note_types: Callable,
    add_filtered_deck: Callable,
) -> None:
    from ankihub.sync import get_note_types_in_deck

    did = add_deck()
    names = ["AnkiHub1", "AnkiHub2", "AnkiHub3"]
    mid1, mid2, mid3 = add_cloze_note_types(names)
    notes1 = add_cloze_notes(col, mid1, did, card_count=1)
    add_cloze_notes(col, mid2, did)
    add_cloze_notes(col, mid3, 1)  # default deck

    mids = get_note_types_in_deck(did)
    assert mid1 in mids
    assert mid2 in mids
    assert len(mids) == 2

    # Test for notes in filtered deck
    nid = notes1[0].id
    add_filtered_deck(f"nid:{nid}")
    mids = get_note_types_in_deck(did)
    assert mid1 in mids
    assert mid2 in mids
    assert len(mids) == 2


def test_note_type_preparations(
    col: Collection, add_cloze_note_types: Callable
) -> None:
    from ankihub.sync import has_ankihub_field, prepare_note_types
    from ankihub.consts import FIELD_NAME

    mid = add_cloze_note_types(["AnkiHub1"]).pop()
    note_type = col.models.get(mid)
    assert has_ankihub_field(note_type) == False

    prev_templs = copy.deepcopy(note_type["tmpls"])
    prepare_note_types([note_type])

    # prepare_note_types added the field
    note_type = col.models.get(mid)
    assert has_ankihub_field(note_type) == True
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
