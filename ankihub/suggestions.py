"""Code for creating note suggestions based on changes to notes in the Anki collection relative
to the version stored in the AnkiHub database. Suggestions are sent to AnkiHub.
(The AnkiHub database is the source of truth for the notes in the AnkiHub deck and is updated
when syncing with AnkiHub.)"""
import copy
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, cast

import aqt
from anki.notes import Note, NoteId

from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import (
    ChangeNoteSuggestion,
    Field,
    NewNoteSuggestion,
    NoteInfo,
    NoteSuggestion,
    SuggestionType,
    get_media_names_from_notes_data,
    get_media_names_from_suggestions,
)
from .db import ankihub_db
from .exporting import to_note_data
from .media_sync import media_sync
from .media_utils import find_and_replace_text_in_fields_on_all_notes

# string that is contained in the errors returned from the AnkiHub API when
# there are no changes to the note for a change note suggestion
ANKIHUB_NO_CHANGE_ERROR = (
    "Suggestion fields and tags don't have any changes to the original note"
)


def suggest_note_update(
    note: Note, change_type: SuggestionType, comment: str, auto_accept: bool = False
) -> bool:
    """Sends a ChangeNoteSuggestion to AnkiHub if the passed note has changes.
    Returns True if the suggestion was created, False if the note has no changes
    (and therefore no suggestion was created).
    Also renames media files in the Anki collection and the media folder and uploads them to AnkiHub.
    If calling this function from the editor, the note should be reloaded after this function is called,
    because the note's media files will possibly have been renamed.
    """
    suggestion = _change_note_suggestion(note, change_type, comment)
    if suggestion is None:
        return False

    ah_did = ankihub_db.ankihub_did_for_anki_nid(NoteId(suggestion.anki_nid))
    suggestion = cast(
        ChangeNoteSuggestion,
        _rename_and_upload_media_for_suggestion(
            suggestion=suggestion, ankihub_did=ah_did
        ),
    )

    client = AnkiHubClient()
    client.create_change_note_suggestion(
        change_note_suggestion=suggestion,
        auto_accept=auto_accept,
    )

    return True


def suggest_new_note(
    note: Note, comment: str, ankihub_did: uuid.UUID, auto_accept: bool = False
) -> None:
    """Sends a NewNoteSuggestion to AnkiHub.
    Also renames media in the Anki collection and the media folder and uploads them to AnkiHub.
    If calling this function from the editor, the note should be reloaded after this function is called,
    because the note's media will possibly have been renamed."""
    suggestion = _new_note_suggestion(note, ankihub_did, comment)

    suggestion = cast(
        NewNoteSuggestion,
        _rename_and_upload_media_for_suggestion(
            suggestion=suggestion, ankihub_did=ankihub_did
        ),
    )

    client = AnkiHubClient()
    client.create_new_note_suggestion(suggestion, auto_accept=auto_accept)


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
    """
    Sends a NewNoteSuggestion or a ChangeNoteSuggestion to AnkiHub for each note in the list.
    All notes have to be from one AnkiHub deck. If a note does not belong to any
    AnkiHub deck, it will be ignored.
    Note: Notes that don't have any changes when compared to the local
    AnkiHub database will not be sent. This does not necessarily mean
    that the note has no changes when compared to the remote AnkiHub
    database. To create suggestions for notes that differ from the
    remote database but not from the local database, users have to
    sync first (so that the local database is up to date)."""

    change_note_ah_dids = set(
        ankihub_db.ankihub_dids_for_anki_nids([note.id for note in notes])
    )
    new_note_ah_dids = set(
        ankihub_db.ankihub_did_for_note_type(note.mid) for note in notes
    )

    ankihub_dids = list(change_note_ah_dids | new_note_ah_dids)
    ankihub_dids = [did for did in ankihub_dids if did is not None]

    assert len(ankihub_dids) == 1, "All notes must belong to the same AnkiHub deck"
    ankihub_did = ankihub_dids[0]

    (
        new_note_suggestions,
        change_note_suggestions,
        nids_without_changes,
    ) = _suggestions_for_notes(notes, ankihub_did, change_type, comment)

    new_note_suggestions = cast(
        Sequence[NewNoteSuggestion],
        _rename_and_upload_media_for_suggestions(
            suggestions=new_note_suggestions,
            ankihub_did=ankihub_did,
        ),
    )
    change_note_suggestions = cast(
        Sequence[ChangeNoteSuggestion],
        _rename_and_upload_media_for_suggestions(
            suggestions=change_note_suggestions,
            ankihub_did=ankihub_did,
        ),
    )

    client = AnkiHubClient()
    errors_by_nid_int = client.create_suggestions_in_bulk(
        new_note_suggestions=new_note_suggestions,
        change_note_suggestions=change_note_suggestions,
        auto_accept=auto_accept,
    )
    errors_by_nid_from_remote = {
        NoteId(nid): errors for nid, errors in errors_by_nid_int.items()
    }
    errors_by_nid_from_local = {
        nid: ANKIHUB_NO_CHANGE_ERROR for nid in nids_without_changes
    }
    errors_by_nid: Dict[NoteId, Any] = {
        **errors_by_nid_from_remote,
        **errors_by_nid_from_local,
    }

    result = BulkNoteSuggestionsResult(
        errors_by_nid=errors_by_nid,
        change_note_suggestions_count=len(
            [x for x in change_note_suggestions if x.anki_nid not in errors_by_nid]
        ),
        new_note_suggestions_count=len(
            [x for x in new_note_suggestions if x.anki_nid not in errors_by_nid]
        ),
    )
    return result


def _suggestions_for_notes(
    notes: List[Note], ankihub_did: uuid.UUID, change_type: SuggestionType, comment: str
) -> Tuple[
    Sequence[NewNoteSuggestion], Sequence[ChangeNoteSuggestion], Sequence[NoteId]
]:
    """
    Splits the list of notes into three categories:
    - notes that should be sent as NewNoteSuggestions
    - notes that should be sent as ChangeNoteSuggestions
    - notes that should not be sent because they don't have any changes
    Returns a tuple of three sequences:
    - new_note_suggestions
    - change_note_suggestions
    - nids_without_changes
    """
    anki_nids_to_ankihub_nids = ankihub_db.anki_nids_to_ankihub_nids(
        [note.id for note in notes]
    )
    note_by_anki_id = {note.id: note for note in notes}

    notes_that_exist_on_remote = [
        note_by_anki_id[anki_nid]
        for anki_nid, ah_nid in anki_nids_to_ankihub_nids.items()
        if ah_nid
    ]

    notes_that_dont_exist_on_remote = [
        note_by_anki_id[anki_nid]
        for anki_nid, ah_nid in anki_nids_to_ankihub_nids.items()
        if ah_nid is None
    ]

    # Create change note suggestions for notes that exist on remote
    change_note_suggestions_or_none_by_nid = {
        note.id: _change_note_suggestion(
            note=note,
            change_type=change_type,
            comment=comment,
        )
        for note in notes_that_exist_on_remote
    }
    change_note_suggestions = [
        suggestion
        for suggestion in change_note_suggestions_or_none_by_nid.values()
        if suggestion
    ]
    # nids of notes that exist on remote but have no changes
    nids_without_changes = [
        nid
        for nid, suggestion in change_note_suggestions_or_none_by_nid.items()
        if not suggestion
    ]

    # Create new note suggestions for notes that don't exist on remote
    new_note_suggestions = [
        _new_note_suggestion(
            note=note,
            ankihub_deck_uuid=ankihub_did,
            comment=comment,
        )
        for note in notes_that_dont_exist_on_remote
    ]

    return new_note_suggestions, change_note_suggestions, nids_without_changes


def _new_note_suggestion(
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
        guid=note.guid,
        comment=comment,
    )


def _change_note_suggestion(
    note: Note, change_type: SuggestionType, comment: str
) -> Optional[ChangeNoteSuggestion]:
    note_from_anki_db = to_note_data(note)
    assert isinstance(note_from_anki_db, NoteInfo)
    assert note_from_anki_db.ankihub_note_uuid is not None
    assert note_from_anki_db.tags is not None

    note_from_ah_db = ankihub_db.note_data(note.id)

    added_tags, removed_tags = _added_and_removed_tags(
        prev_tags=note_from_ah_db.tags, cur_tags=note_from_anki_db.tags
    )

    fields_that_changed = _fields_that_changed(
        prev_fields=note_from_ah_db.fields, cur_fields=note_from_anki_db.fields
    )

    if not added_tags and not removed_tags and not fields_that_changed:
        return None

    return ChangeNoteSuggestion(
        ankihub_note_uuid=note_from_anki_db.ankihub_note_uuid,
        anki_nid=note.id,
        fields=fields_that_changed,
        added_tags=added_tags,
        removed_tags=removed_tags,
        change_type=change_type,
        comment=comment,
    )


def _added_and_removed_tags(
    prev_tags: List[str], cur_tags: List[str]
) -> Tuple[List[str], List[str]]:
    added_tags = [tag for tag in cur_tags if tag not in prev_tags]
    removed_tags = [tag for tag in prev_tags if tag not in cur_tags]
    return added_tags, removed_tags


def _fields_that_changed(
    prev_fields: List[Field], cur_fields: List[Field]
) -> List[Field]:
    result = [
        cur_field
        for cur_field, prev_field in zip(cur_fields, prev_fields)
        if cur_field.value != prev_field.value
    ]
    return result


def _rename_and_upload_media_for_suggestion(
    suggestion: NoteSuggestion, ankihub_did: uuid.UUID
) -> NoteSuggestion:
    suggestion = _rename_and_upload_media_for_suggestions([suggestion], ankihub_did)[0]
    return suggestion


def _rename_and_upload_media_for_suggestions(
    suggestions: Sequence[NoteSuggestion],
    ankihub_did: uuid.UUID,
) -> Sequence[NoteSuggestion]:
    """Renames media files referenced on the suggestions in the Anki collection and the media folder and
    uploads them to AnkiHub in another thread.
    Returns suggestion with updated media names."""

    client = AnkiHubClient()
    original_notes_data = [
        note_info
        for suggestion in suggestions
        if (note_info := ankihub_db.note_data(NoteId(suggestion.anki_nid)))
    ]
    original_media_names: Set[str] = get_media_names_from_notes_data(
        original_notes_data
    )
    suggestion_media_names: Set[str] = get_media_names_from_suggestions(suggestions)

    # Filter out unchanged media file names so we don't hash and upload media files that aren't part of the suggestion
    added_media_names = suggestion_media_names.difference(original_media_names)

    if not added_media_names:
        # No media files added, nothing to do here. Return
        # the original suggestions object
        return suggestions

    added_media_paths = [
        Path(aqt.mw.col.media.dir()) / media_name for media_name in added_media_names
    ]

    media_name_map = client.generate_media_files_with_hashed_names(added_media_paths)

    media_sync.start_media_upload(
        media_names=media_name_map.values(), ankihub_did=ankihub_did
    )

    if media_name_map:
        suggestions = copy.deepcopy(suggestions)
        for suggestion in suggestions:
            _replace_media_names_in_suggestion(suggestion, media_name_map)

        _update_media_names_on_notes(media_name_map)

    return suggestions


def _replace_media_names_in_suggestion(
    suggestion: NoteSuggestion, media_name_map: Dict[str, str]
):
    suggestion.fields = [
        _field_with_replaced_media_names(field, media_name_map)
        for field in suggestion.fields
    ]


def _field_with_replaced_media_names(
    field: Field, media_name_map: Dict[str, str]
) -> Field:
    result = copy.deepcopy(field)
    for old_name, new_name in media_name_map.items():
        result.value = result.value.replace(f'src="{old_name}"', f'src="{new_name}"')
        result.value = result.value.replace(f"src='{old_name}'", f"src='{new_name}'")
        result.value = result.value.replace(
            f"[sound:{old_name}]", f"[sound:{new_name}]"
        )
    return result


def _update_media_names_on_notes(media_name_map: Dict[str, str]):
    for original_filename, new_filename in media_name_map.items():
        find_and_replace_text_in_fields_on_all_notes(
            f'src="{original_filename}"', f'src="{new_filename}"'
        )
        find_and_replace_text_in_fields_on_all_notes(
            f"src='{original_filename}'", f"src='{new_filename}'"
        )
        find_and_replace_text_in_fields_on_all_notes(
            f"[sound:{original_filename}]", f"[sound:{new_filename}]"
        )
