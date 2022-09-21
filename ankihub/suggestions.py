import re
import uuid
from typing import Dict, List

from anki.notes import Note, NoteId

from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import (
    ChangeNoteSuggestion,
    Field,
    NewNoteSuggestion,
    SuggestionType,
)
from .db import AnkiHubDB
from .sync import is_internal_tag
from .utils import ankihub_uuid_of_note


def suggest_note_update(
    note: Note, change_type: SuggestionType, comment: str, auto_accept: bool = False
):
    client = AnkiHubClient()
    client.create_change_note_suggestion(
        change_note_suggestion(note, change_type, comment),
        auto_accept=auto_accept,
    )


def suggest_new_note(
    note: Note, comment: str, ankihub_deck_uuid: uuid.UUID, auto_accept: bool = False
):
    client = AnkiHubClient()
    client.create_new_note_suggestion(
        new_note_suggestion(note, ankihub_deck_uuid, comment), auto_accept=auto_accept
    )


def suggest_notes_in_bulk(
    notes: List[Note],
    auto_accept: bool,
    change_type: SuggestionType,
    comment: str,
) -> Dict[NoteId, Dict[str, List[str]]]:
    # returns a dict of errors by anki_note_id

    ankihub_did_for_mid = {
        mid: ankihub_did
        for mid in set(note.mid for note in notes)
        if (ankihub_did := AnkiHubDB().ankihub_did_for_note_type(mid))
    }

    notes_that_exist_on_remote = []
    notes_that_dont_exist_on_remote = []
    ankihub_notes = [note for note in notes if ankihub_did_for_mid.get(note.mid)]
    for note in ankihub_notes:
        if ankihub_uuid_of_note(note, ignore_invalid=True):
            notes_that_exist_on_remote.append(note)
        else:
            notes_that_dont_exist_on_remote.append(note)

    change_suggestions = [
        change_note_suggestion(
            note=note,
            change_type=change_type,
            comment=comment,
        )
        for note in notes_that_exist_on_remote
    ]

    new_note_suggestions = [
        new_note_suggestion(
            note=note,
            ankihub_deck_uuid=uuid.UUID(ankihub_did_for_mid[note.mid]),
            comment=comment,
        )
        for note in notes_that_dont_exist_on_remote
    ]

    all_suggestions = change_suggestions + new_note_suggestions
    client = AnkiHubClient()
    errors_by_nid_int = client.create_suggestions_in_bulk(
        all_suggestions, auto_accept=auto_accept
    )
    errors_by_nid = {NoteId(nid): errors for nid, errors in errors_by_nid_int.items()}
    return errors_by_nid


def change_note_suggestion(
    note: Note, change_type: SuggestionType, comment: str
) -> ChangeNoteSuggestion:
    ankihub_note_uuid = ankihub_uuid_of_note(note, ignore_invalid=False)
    tags = _prepare_tags(note.tags)
    fields = _prepare_fields(note)

    return ChangeNoteSuggestion(
        ankihub_note_uuid=ankihub_note_uuid,
        anki_note_id=note.id,
        fields=fields,
        tags=tags,
        change_type=change_type,
        comment=comment,
    )


def new_note_suggestion(
    note: Note, ankihub_deck_uuid: uuid.UUID, comment: str
) -> NewNoteSuggestion:
    ankihub_note_uuid = ankihub_uuid_of_note(note, ignore_invalid=True)
    if not ankihub_note_uuid:
        ankihub_note_uuid = uuid.uuid4()

    tags = _prepare_tags(note.tags)
    fields = _prepare_fields(note)

    return NewNoteSuggestion(
        ankihub_deck_uuid=ankihub_deck_uuid,
        ankihub_note_uuid=ankihub_note_uuid,
        anki_note_id=note.id,
        fields=fields,
        tags=tags,
        note_type_name=note.note_type()["name"],
        anki_note_type_id=note.note_type()["id"],
        comment=comment,
    )


def _prepare_fields(note: Note) -> List[Field]:

    # Exclude the AnkiHub ID field since we don't want to expose this as an
    # editable field in AnkiHub suggestion forms.
    field_vals = list(note.fields[:-1])
    fields_metadata = note.note_type()["flds"][:-1]

    prepared_field_vals = [_prepared_field_html(field) for field in field_vals]
    fields = [
        Field(name=field["name"], order=field["ord"], value=val)
        for field, val in zip(fields_metadata, prepared_field_vals)
    ]
    return fields


def _prepared_field_html(html: str) -> str:
    # Since Anki 2.1.54 data-editor-shrink attribute="True" is added to img tags when you double click them.
    # We don't want this attribute to appear in suggestions.
    result = re.sub(r" ?data-editor-shrink=['\"]true['\"]", "", html)
    return result


def _prepare_tags(tags: List[str]):
    # removing empty tags is necessary because notes have empty tags in the editor sometimes
    return [tag for tag in tags if tag.strip() and not is_internal_tag(tag)]
