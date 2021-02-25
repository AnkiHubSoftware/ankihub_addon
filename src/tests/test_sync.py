from contextlib import contextmanager
from typing import Generator, Tuple

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


def add_cloze_notes(col: Collection, mid: int, did: int):
    "This function assumes note type is cloze type, and has field 'Text' and 'Extra'."

    def create_note(text, extra):
        note_type = col.models.get(mid)
        note = Note(col, note_type)
        note["Text"] = text
        note["Extra"] = extra
        return note

    for i in range(10):
        note = create_note("cloze {{c1::abc}}" + str(i), "extra text")
        col.add_note(note, did)


def test_get_note_types_in_deck(col: Collection):
    from ankihub.sync import get_note_types_in_deck

    with add_deck(col) as did:
        with add_note_type(col, "AnkiHub1") as mid1:
            with add_note_type(col, "AnkiHub2") as mid2:
                with add_note_type(col, "AnkiHub3") as mid3:
                    add_cloze_notes(col, mid1, did)
                    add_cloze_notes(col, mid2, did)
                    add_cloze_notes(col, mid3, 1)  # default deck

                    mids = get_note_types_in_deck(did)
                    assert mid1 in mids
                    assert mid2 in mids
                    assert len(mids) == 2
