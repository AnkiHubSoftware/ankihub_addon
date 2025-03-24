"""Code for creating note suggestions based on changes to notes in the Anki collection relative
to the version stored in the AnkiHub database. Suggestions are sent to AnkiHub.
(The AnkiHub database is the source of truth for the notes in the AnkiHub deck and is updated
when syncing with AnkiHub.)"""

import copy
import shutil
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Collection,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    cast,
)

import aqt
from anki.notes import Note, NoteId

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import (
    ChangeNoteSuggestion,
    Field,
    NewNoteSuggestion,
    NoteInfo,
    NoteSuggestion,
    SuggestionType,
    get_media_names_from_notes_data,
    get_media_names_from_suggestions,
)
from ..ankihub_client.ankihub_client import AnkiHubHTTPError
from ..db import ankihub_db
from ..db.db import execute_list_query_in_chunks
from ..db.models import AnkiHubNote
from .exporting import to_note_data
from .media_utils import find_and_replace_text_in_fields_on_all_notes
from .utils import get_anki_nid_to_mid_dict, is_tag_in_list, md5_file_hash

# string that is contained in the errors returned from the AnkiHub API when
# there are no changes to the note for a change note suggestion
ANKIHUB_NO_CHANGE_ERROR = (
    "Suggestion fields and tags don't have any changes to the original note"
)

# string that is contained in the errors returned from the AnkiHub API when
# the note does not exist on AnkiHub
ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR = "Note object does not exist"


class ChangeSuggestionResult(Enum):
    SUCCESS = "success"
    NO_CHANGES = "no changes"
    ANKIHUB_NOT_FOUND = "not found"


class MediaUploadCallback(Protocol):
    def __call__(self, media_names: Set[str], ankihub_did: uuid.UUID) -> None:
        ...


def suggest_note_update(
    note: Note,
    change_type: SuggestionType,
    comment: str,
    media_upload_cb: MediaUploadCallback,
    auto_accept: bool = False,
) -> ChangeSuggestionResult:
    """Sends a ChangeNoteSuggestion to AnkiHub if the passed note has changes.
    Also renames media files in the Anki collection and the media folder and uploads them to AnkiHub.
    Returns a ChangeSuggestionResult enum value.
    """

    suggestion = _change_note_suggestion(note, change_type, comment)
    if suggestion is None:
        return ChangeSuggestionResult.NO_CHANGES

    ah_did = ankihub_db.ankihub_did_for_anki_nid(NoteId(suggestion.anki_nid))
    suggestion = cast(
        ChangeNoteSuggestion,
        _rename_and_upload_media_for_suggestion(
            suggestion=suggestion, ankihub_did=ah_did, media_upload_cb=media_upload_cb
        ),
    )

    client = AnkiHubClient()
    try:
        client.create_change_note_suggestion(
            change_note_suggestion=suggestion,
            auto_accept=auto_accept,
        )
    except AnkiHubHTTPError as e:
        if e.response.status_code == 404:
            return ChangeSuggestionResult.ANKIHUB_NOT_FOUND
        raise e

    return ChangeSuggestionResult.SUCCESS


def suggest_new_note(
    note: Note,
    comment: str,
    ankihub_did: uuid.UUID,
    media_upload_cb: MediaUploadCallback,
    auto_accept: bool = False,
) -> None:
    """Sends a NewNoteSuggestion to AnkiHub.
    Also renames media in the Anki collection and the media folder and uploads them to AnkiHub.
    """
    suggestion = _new_note_suggestion(note, ankihub_did, comment)

    suggestion = cast(
        NewNoteSuggestion,
        _rename_and_upload_media_for_suggestion(
            suggestion=suggestion,
            ankihub_did=ankihub_did,
            media_upload_cb=media_upload_cb,
        ),
    )

    client = AnkiHubClient()
    client.create_new_note_suggestion(
        new_note_suggestion=suggestion, auto_accept=auto_accept
    )


@dataclass
class BulkNoteSuggestionsResult:
    errors_by_nid: Dict[NoteId, List[str]]
    new_note_suggestions_count: int
    change_note_suggestions_count: int


def suggest_notes_in_bulk(
    ankihub_did: uuid.UUID,
    notes: List[Note],
    auto_accept: bool,
    change_type: SuggestionType,
    comment: str,
    media_upload_cb: MediaUploadCallback,
) -> BulkNoteSuggestionsResult:
    """
    Sends a NewNoteSuggestion or a ChangeNoteSuggestion to AnkiHub for each note in the list.
    Note: Notes that don't have any changes when compared to the local
    AnkiHub database will not be sent. This does not necessarily mean
    that the note has no changes when compared to the remote AnkiHub
    database. To create suggestions for notes that differ from the
    remote database but not from the local database, users have to
    sync first (so that the local database is up to date)."""
    (
        new_note_suggestions,
        change_note_suggestions,
        nids_without_changes,
        nids_deleted_on_remote,
    ) = _suggestions_for_notes(notes, ankihub_did, change_type, comment)

    new_note_suggestions = cast(
        Sequence[NewNoteSuggestion],
        _rename_and_upload_media_for_suggestions(
            suggestions=new_note_suggestions,
            ankihub_did=ankihub_did,
            media_upload_cb=media_upload_cb,
        ),
    )
    change_note_suggestions = cast(
        Sequence[ChangeNoteSuggestion],
        _rename_and_upload_media_for_suggestions(
            suggestions=change_note_suggestions,
            ankihub_did=ankihub_did,
            media_upload_cb=media_upload_cb,
        ),
    )

    if new_note_suggestions or change_note_suggestions:
        client = AnkiHubClient()
        errors_by_nid_int = client.create_suggestions_in_bulk(
            new_note_suggestions=new_note_suggestions,
            change_note_suggestions=change_note_suggestions,
            auto_accept=auto_accept,
        )
        errors_by_nid_from_remote = {
            NoteId(nid): errors for nid, errors in errors_by_nid_int.items()
        }
    else:
        errors_by_nid_from_remote = {}

    errors_by_nid_from_local = {
        **{nid: ANKIHUB_NO_CHANGE_ERROR for nid in nids_without_changes},
        **{nid: ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR for nid in nids_deleted_on_remote},
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


def get_anki_nid_to_ah_dids_dict(
    anki_nids: Collection[NoteId],
) -> Dict[NoteId, uuid.UUID]:
    """Returns a dictionary that maps anki note ids to AnkiHub deck ids they could be suggested to.
    Whether a note could be suggested to a deck is determined in this manner:
    - If the note is on AnkiHub already, the deck id can be looked up in database by the note id.
    - Otherwise the note type is used to determine the deck id.
    """
    # Get deck ids for existing AnkiHub notes
    anki_nid_to_ah_did_for_existing = ankihub_db.anki_nid_to_ah_did_dict(anki_nids)

    # Get deck ids for notes that are not on AnkiHub yet by looking at their note type
    nids_without_ah_note = set(anki_nids) - anki_nid_to_ah_did_for_existing.keys()
    anki_nid_to_mid = get_anki_nid_to_mid_dict(nids_without_ah_note)
    mid_to_ah_did = {
        mid: ankihub_db.ankihub_did_for_note_type(mid)
        for mid in set(anki_nid_to_mid.values())
    }
    anki_nid_to_ah_did_for_new = {
        nid: mid_to_ah_did[mid] for nid, mid in anki_nid_to_mid.items()
    }

    return {
        **anki_nid_to_ah_did_for_existing,
        **anki_nid_to_ah_did_for_new,
    }


def _suggestions_for_notes(
    notes: List[Note], ankihub_did: uuid.UUID, change_type: SuggestionType, comment: str
) -> Tuple[
    Sequence[NewNoteSuggestion],
    Sequence[ChangeNoteSuggestion],
    Sequence[NoteId],
    Sequence[NoteId],
]:
    """
    Splits the list of notes into four categories:
    - notes that should be sent as NewNoteSuggestions
    - notes that should be sent as ChangeNoteSuggestions
    - notes that should not be sent because they don't have any changes
    - notes that should not be sent because they were deleted on AnkiHub

    Returns a tuple of four sequences:
    - new_note_suggestions
    - change_note_suggestions
    - nids_without_changes
    - nids_deleted_on_remote
    """
    anki_nids = [note.id for note in notes]

    ah_db_notes = execute_list_query_in_chunks(
        lambda anki_nids: AnkiHubNote.filter(anki_note_id__in=anki_nids),
        ids=anki_nids,
    )
    ah_db_note_by_anki_nid = {
        NoteId(ah_db_note.anki_note_id): ah_db_note for ah_db_note in ah_db_notes
    }

    notes_for_new_note_suggestions = []
    notes_for_change_note_suggestions = []
    nids_deleted_on_remote = []
    for note in notes:
        if ah_db_note := ah_db_note_by_anki_nid.get(note.id):
            ah_db_note = cast(AnkiHubNote, ah_db_note)
            if ah_db_note.was_deleted():
                nids_deleted_on_remote.append(note.id)
            else:
                notes_for_change_note_suggestions.append(note)
        else:
            notes_for_new_note_suggestions.append(note)

    change_note_suggestion_or_none_by_anki_nid = {
        note.id: _change_note_suggestion(
            note=note,
            change_type=change_type,
            comment=comment,
        )
        for note in notes_for_change_note_suggestions
    }
    change_note_suggestions = []
    nids_without_changes = []
    for nid, suggestion in change_note_suggestion_or_none_by_anki_nid.items():
        if suggestion:
            change_note_suggestions.append(suggestion)
        else:
            nids_without_changes.append(nid)

    new_note_suggestions = [
        _new_note_suggestion(
            note=note,
            ah_did=ankihub_did,
            comment=comment,
        )
        for note in notes_for_new_note_suggestions
    ]

    return (
        new_note_suggestions,
        change_note_suggestions,
        nids_without_changes,
        nids_deleted_on_remote,
    )


def _new_note_suggestion(
    note: Note, ah_did: uuid.UUID, comment: str
) -> NewNoteSuggestion:
    note_data = to_note_data(note, set_new_id=True)

    return NewNoteSuggestion(
        ah_did=ah_did,
        ah_nid=note_data.ah_nid,
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
    note_from_anki_db = to_note_data(note, include_empty_fields=True)
    assert isinstance(note_from_anki_db, NoteInfo)
    assert note_from_anki_db.ah_nid is not None
    assert note_from_anki_db.tags is not None

    added_tags: List[str] = []
    removed_tags: List[str] = []
    fields_that_changed: List[Field] = []

    if change_type != SuggestionType.DELETE:
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
        ah_nid=note_from_anki_db.ah_nid,
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
    added_tags = [tag for tag in cur_tags if not is_tag_in_list(tag, prev_tags)]
    removed_tags = [tag for tag in prev_tags if not is_tag_in_list(tag, cur_tags)]
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
    suggestion: NoteSuggestion,
    ankihub_did: uuid.UUID,
    media_upload_cb: MediaUploadCallback,
) -> NoteSuggestion:
    suggestion = _rename_and_upload_media_for_suggestions(
        [suggestion], ankihub_did, media_upload_cb=media_upload_cb
    )[0]
    return suggestion


def _rename_and_upload_media_for_suggestions(
    suggestions: Sequence[NoteSuggestion],
    ankihub_did: uuid.UUID,
    media_upload_cb: MediaUploadCallback,
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
    media_names_added_to_note = suggestion_media_names.difference(original_media_names)

    # Filter out media names without media files in the Anki collection
    media_dir = Path(aqt.mw.col.media.dir())
    media_names_added_to_note = {
        name for name in media_names_added_to_note if (media_dir / name).exists()
    }

    # Filter out media file names that already exist for the deck
    media_names_added_to_ah_deck = {
        name
        for name, exists in ankihub_db.media_names_exist_for_ankihub_deck(
            ah_did=ankihub_did, media_names=media_names_added_to_note
        ).items()
        if not exists
    }

    media_names_added_to_ah_deck = _handle_media_with_matching_hashes(
        ah_did=ankihub_did,
        suggestions=suggestions,
        media_names=media_names_added_to_ah_deck,
    )

    if not media_names_added_to_ah_deck:
        # No media files added, nothing to do here. Return
        # the original suggestions object
        return suggestions

    media_dir = Path(aqt.mw.col.media.dir())
    added_media_paths = [
        (media_dir / media_name) for media_name in media_names_added_to_ah_deck
    ]

    media_name_map = client.generate_media_files_with_hashed_names(added_media_paths)

    media_upload_cb(media_names=set(media_name_map.values()), ankihub_did=ankihub_did)

    if media_name_map:
        suggestions = copy.deepcopy(suggestions)
        for suggestion in suggestions:
            _replace_media_names_in_suggestion(suggestion, media_name_map)

        _update_media_names_on_notes(media_name_map)

    return suggestions


def _handle_media_with_matching_hashes(
    ah_did: uuid.UUID,
    suggestions: Sequence[NoteSuggestion],
    media_names: Set[str],
) -> Set[str]:
    """If a media file with the same hash already exist for the deck, we shouldn't upload the media file,
    just change the media file name on the notes and suggestions to the name of the existing media file.
    If the file with the matching hash is not in the Anki collection,
    we create it by copying the referenced media file to prevent broken media references.
    """
    media_dir = Path(aqt.mw.col.media.dir())
    media_to_hash_dict = {
        media_name: md5_file_hash(media_dir / media_name) for media_name in media_names
    }

    media_with_same_hash_dict = ankihub_db.media_names_with_matching_hashes(
        ah_did=ah_did, media_to_hash=media_to_hash_dict
    )

    # Change media names in suggestions and notes to the names of the existing media files
    # with the same hash
    for suggestion in suggestions:
        _replace_media_names_in_suggestion(suggestion, media_with_same_hash_dict)

    _update_media_names_on_notes(media_with_same_hash_dict)

    # If the file with the matching hash is not in the Anki collection,
    # we create it by copying the referenced media file.
    # The file can exist for the deck but not in the Anki collection of the user.
    # Without this step, the media reference could be broken for the user.
    for media_name, existing_media_name in media_with_same_hash_dict.items():
        if not (Path(aqt.mw.col.media.dir()) / existing_media_name).exists():
            shutil.copy(
                Path(aqt.mw.col.media.dir()) / media_name,
                Path(aqt.mw.col.media.dir()) / existing_media_name,
            )

    # Remove media names that have matching media from the list of media names to upload
    result = media_names.difference(media_with_same_hash_dict.keys())
    return result


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
