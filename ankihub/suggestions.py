import uuid
from dataclasses import dataclass
from typing import Dict, List

from anki.notes import Note, NoteId

from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import ChangeNoteSuggestion, NewNoteSuggestion, SuggestionType
from .db import ankihub_db
from .exporting import to_note_data
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


@dataclass
class BulkNoteSuggestionsResult:
    errors_by_nid: Dict[NoteId, Dict[str, List[str]]]  # dict of errors by anki_nid
    new_note_suggestions_count: int
    change_note_suggestions_count: int


def suggest_notes_in_bulk(
    notes: List[Note],
    auto_accept: bool,
    change_type: SuggestionType,
    comment: str,
) -> BulkNoteSuggestionsResult:

    ankihub_did_for_mid = {
        mid: ankihub_did
        for mid in set(note.mid for note in notes)
        if (ankihub_did := ankihub_db.ankihub_did_for_note_type(mid))
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
    result = BulkNoteSuggestionsResult(
        errors_by_nid=errors_by_nid,
        change_note_suggestions_count=len(
            [x for x in change_suggestions if x.anki_nid not in errors_by_nid]
        ),
        new_note_suggestions_count=len(
            [x for x in new_note_suggestions if x.anki_nid not in errors_by_nid]
        ),
    )
    return result


def change_note_suggestion(
    note: Note, change_type: SuggestionType, comment: str
) -> ChangeNoteSuggestion:
    note_data = to_note_data(note)

    return ChangeNoteSuggestion(
        ankihub_note_uuid=note_data.ankihub_note_uuid,
        anki_nid=note.id,
        fields=note_data.fields,
        tags=note_data.tags,
        change_type=change_type,
        comment=comment,
    )


def new_note_suggestion(
    note: Note, ankihub_deck_uuid: uuid.UUID, comment: str
) -> NewNoteSuggestion:
    note_data = to_note_data(note, set_new_id=True)

    return NewNoteSuggestion(
        ankihub_deck_uuid=ankihub_deck_uuid,
        ankihub_note_uuid=note_data.ankihub_note_uuid,
        anki_nid=note.id,
        fields=note_data.fields,
        tags=note_data.tags,
        note_type_name=note.note_type()["name"],
        anki_note_type_id=note.mid,
        comment=comment,
    )
