from contextlib import contextmanager
from typing import Generator, List

from anki.collection import Collection
from anki.models import NoteType
from anki.notes import Note


@contextmanager
def add_deck(col: Collection) -> Generator[int, None, None]:
    "The deck with all its cards are removed afterwards"
    did = col.decks.id("AnkiHub Deck")
    try:
        yield did
    finally:
        col.decks.rem(did)


@contextmanager
def add_filtered_deck(col: Collection, search: str) -> Generator[int, None, None]:
    did = col.decks.new_filtered("AnkiHub Filtered")
    deck = col.decks.get(did)
    deck["terms"] = [[search, 100, 1]]
    col.decks.save(deck)
    try:
        yield did
    finally:
        col.decks.rem(did)


@contextmanager
def add_note_type(col: Collection, name="AnkiHub") -> Generator[int, None, None]:
    "The note type with all its cards are removed afterwards"
    cloze_note_type = col.models.byName("Cloze")
    ah_note_type = col.models.copy(cloze_note_type)
    ah_note_type["name"] = name
    extra_field = col.models.new_field("Extra")
    col.models.add_field(ah_note_type, extra_field)
    col.models.add(ah_note_type)
    mid = col.models.id_for_name(name)
    try:
        yield mid
    finally:
        col.models.rem(ah_note_type)


def add_cloze_notes(col: Collection, mid: int, did: int, card_count: int = 10) -> List[Note]:
    "This function assumes note type is cloze type, and has field 'Text' and 'Extra'."

    def create_note(text, extra):
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


def test_get_note_types_in_deck(col: Collection):
    from ankihub.sync import get_note_types_in_deck
    with add_deck(col) as did:
        with add_note_type(col, "AnkiHub1") as mid1:
            with add_note_type(col, "AnkiHub2") as mid2:
                with add_note_type(col, "AnkiHub3") as mid3:
                    notes1 = add_cloze_notes(col, mid1, did, card_count=1)
                    add_cloze_notes(col, mid2, did)
                    add_cloze_notes(col, mid3, 1)  # default deck

                    mids = get_note_types_in_deck(did)
                    assert mid1 in mids
                    assert mid2 in mids
                    assert len(mids) == 2

                    # Test for notes in filtered deck
                    nid = notes1[0].id
                    with add_filtered_deck(col, f"nid:{nid}") as fdid:
                        mids = get_note_types_in_deck(did)
                        assert mid1 in mids
                        assert mid2 in mids
                        assert len(mids) == 2
