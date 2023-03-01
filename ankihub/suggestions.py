import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from anki.notes import Note, NoteId
import aqt

from ankihub.utils import extract_local_image_paths_from_html

from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import (
    ChangeNoteSuggestion,
    NewNoteSuggestion,
    NoteSuggestion,
    SuggestionType,
)
from .db import ankihub_db
from .exporting import to_note_data
from .settings import config

# string that is contained in the errors returned from the AnkiHub API when
# there are no changes to the note for a change note suggestion
ANKIHUB_NO_CHANGE_ERROR = (
    "Suggestion fields and tags don't have any changes to the original note"
)


def suggest_note_update(
    note: Note, change_type: SuggestionType, comment: str, auto_accept: bool = False
) -> bool:
    """Returns True if the suggestion was created, False if the note has no changes
    (and therefore no suggestion was created)"""
    client = AnkiHubClient()
    suggestion = change_note_suggestion(note, change_type, comment)
    if suggestion is None:
        return False

    client.create_change_note_suggestion(
        change_note_suggestion=suggestion,
        auto_accept=auto_accept,
    )
    ah_did = ankihub_db.ankihub_did_for_anki_nid(NoteId(suggestion.anki_nid))
    upload_images_for_suggestion(suggestion, ah_did)

    return True


def upload_images_for_suggestion(suggestion: NoteSuggestion, ah_did: uuid.UUID) -> None:
    client = AnkiHubClient()

    if not (
        client.get_waffle_status()["flags"]
        .get("image_support_enabled", {})
        .get("is_active", False)
    ):
        return

    # TODO: This should be executed in background

    # Find images in suggestion fields and upload them to s3
    image_paths = get_images_from_suggestion(suggestion=suggestion)

    # TODO: User user_id instead of username, because username is subject to change
    username = config.user()
    bucket_path = f"deck_images/{ah_did}/suggestions/{username}"
    client.upload_images(image_paths, bucket_path)


def get_images_from_suggestion(suggestion: NoteSuggestion) -> List[Path]:
    result = []
    for field_content in [f.value for f in suggestion.fields]:
        image_names = extract_local_image_paths_from_html(field_content)
        image_paths = [
            Path(aqt.mw.col.media.dir()) / image_name for image_name in image_names
        ]
        result.extend(image_paths)

    return result


def suggest_new_note(
    note: Note, comment: str, ankihub_deck_uuid: uuid.UUID, auto_accept: bool = False
) -> None:
    client = AnkiHubClient()
    new_note_suggestion_created = new_note_suggestion(note, ankihub_deck_uuid, comment)
    client.create_new_note_suggestion(
        new_note_suggestion_created, auto_accept=auto_accept
    )
    upload_images_for_suggestion(
        new_note_suggestion_created, new_note_suggestion_created.ankihub_deck_uuid
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
    # Note: Notes that don't have any changes when compared to the local
    # AnkiHub database will not be sent. This does not necessarily mean
    # that the note has no changes when compared to the remote AnkiHub
    # database. To create suggestions for notes that differ from the
    # remote database but not from the local database, users have to
    # sync first (so that the local database is up to date).

    ankihub_did_for_mid = {
        mid: ankihub_did
        for mid in set(note.mid for note in notes)
        if (ankihub_did := ankihub_db.ankihub_did_for_note_type(mid))
    }

    notes_that_exist_on_remote = []
    notes_that_dont_exist_on_remote = []
    ankihub_notes = [note for note in notes if ankihub_did_for_mid.get(note.mid)]
    for note in ankihub_notes:
        if ankihub_db.ankihub_nid_for_anki_nid(note.id):
            notes_that_exist_on_remote.append(note)
        else:
            notes_that_dont_exist_on_remote.append(note)

    # Create change note suggestions for notes that exist on remote
    change_note_suggestions_or_none_by_nid = {
        note.id: change_note_suggestion(
            note=note,
            change_type=change_type,
            comment=comment,
        )
        for note in notes_that_exist_on_remote
    }
    change_note_suggestions = [
        suggestion
        for suggestion in change_note_suggestions_or_none_by_nid.values()
        if suggestion is not None
    ]
    # nids of notes that exist on remote but have no changes
    nids_without_changes = [
        nid
        for nid, suggestion in change_note_suggestions_or_none_by_nid.items()
        if not suggestion
    ]

    # Create new note suggestions for notes that don't exist on remote
    new_note_suggestions = [
        new_note_suggestion(
            note=note,
            ankihub_deck_uuid=ankihub_did_for_mid[note.mid],
            comment=comment,
        )
        for note in notes_that_dont_exist_on_remote
    ]

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


def change_note_suggestion(
    note: Note, change_type: SuggestionType, comment: str
) -> Optional[ChangeNoteSuggestion]:
    note_data = to_note_data(note, diff=True)
    assert note_data.ankihub_note_uuid is not None

    if not note_data.fields and note_data.tags is None:
        return None

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
        guid=note.guid,
        comment=comment,
    )
