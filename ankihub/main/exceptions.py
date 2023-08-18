from textwrap import dedent
from typing import List

from anki.models import NotetypeId


class AnkiHubMainException(Exception):
    """Base class for exceptions in this module."""

    pass


class NotetypeFieldsMismatchError(AnkiHubMainException):
    """Raised when the fields of a notetype in the Anki database do not match
    the fields of the same notetype on AnkiHub."""

    def __init__(
        self,
        note_type_name: str,
        note_type_id: NotetypeId,
        ankihub_field_names: List[str],
        anki_field_names: List[str],
    ) -> None:
        self.note_type_name = note_type_name
        self.note_type_id = note_type_id
        self.ankihub_field_names = ankihub_field_names
        self.anki_field_names = anki_field_names

    def __str__(self):
        return dedent(
            f"""
            The fields of the note type {self.note_type_name} (id: {self.note_type_id}) in the Anki database
            do not match the fields of the same note type on AnkiHub.
            AnkiHub fields: {self.ankihub_field_names}
            Anki fields: {self.anki_field_names}
            """
        ).strip()
