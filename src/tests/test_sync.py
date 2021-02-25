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

