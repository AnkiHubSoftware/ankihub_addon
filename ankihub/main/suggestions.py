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
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    cast,
)

import aqt
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.utils import ids2str

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import (
    ChangeNoteSuggestion,
    Field,
    NewNoteSuggestion,
    NoteInfo,
    NoteSuggestion,
    SuggestionType,
    get_media_names_from_note_info,
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
ANKIHUB_NO_CHANGE_ERROR = "Suggestion fields and tags don't have any changes to the original note"

# string that is contained in the errors returned from the AnkiHub API when
# the note does not exist on AnkiHub
ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR = "Note object does not exist"

# string for errors when the first field of a note is empty
ANKIHUB_EMPTY_FIRST_FIELD_ERROR = "The first field is required and cannot be empty"


class ChangeSuggestionResult(Enum):
    SUCCESS = "success"
    NO_CHANGES = "no changes"
    ANKIHUB_NOT_FOUND = "not found"
    EMPTY_FIRST_FIELD = "empty first field"


class MediaUploadCallback(Protocol):
    def __call__(self, media_names: Set[str], ankihub_did: uuid.UUID) -> None: ...


def _has_empty_first_field(note: Note) -> bool:
    """Returns True if the first field of the note is empty or whitespace-only."""
    if not note.fields:
        return True
    return not note.fields[0].strip()


@dataclass
class PerNoteFilters:
    """Allowlists that select what content goes into one note's suggestion.
    `None` for any list means "no filter for this dimension" — ship everything the
    diff detected; reached only when a note is submitted without an explicit
    selection. Globally-protected fields are stripped *before* the dialog renders,
    so they never enter the allowlist — server-side enforcement is the backstop.
    """

    fields_to_include: Optional[Collection[str]] = None
    tags_to_add: Optional[Collection[str]] = None
    tags_to_remove: Optional[Collection[str]] = None


@dataclass
class BulkSuggestionFilters:
    """Same as `PerNoteFilters` but keyed by note type for the bulk-suggest
    path — the dialog groups field selections by mid because the widget has one
    section per note type. Tag filters apply across all notes. A mid absent from
    `fields_to_include_by_mid` ships none of its fields (see `for_mid`); the
    dialog supplies the full per-mid selection.
    """

    fields_to_include_by_mid: Mapping[NotetypeId, Collection[str]]
    tags_to_add: Optional[Collection[str]] = None
    tags_to_remove: Optional[Collection[str]] = None

    def for_mid(self, mid: NotetypeId) -> PerNoteFilters:
        # A mid missing from the map — the widget rendered no field section for it
        # (every edit was globally protected), or this is a field-less submit (`{}`).
        # Treat it as an empty allowlist so the diff can't ship fields the user
        # never saw or selected.
        return PerNoteFilters(
            fields_to_include=self.fields_to_include_by_mid.get(mid, ()),
            tags_to_add=self.tags_to_add,
            tags_to_remove=self.tags_to_remove,
        )


@dataclass
class NoteDiff:
    """Per-note diff data: AH-DB membership, edited fields/tags, media changes.

    Computed once by `compute_note_diffs` so callers needing several pieces
    can share the per-note conversion work.
    """

    exists_in_ah_db: bool  # AH DB has a row for this note (deleted or not)
    is_deleted_on_remote: bool  # row exists AND is marked deleted
    local_note: NoteInfo  # local Anki note as NoteInfo (includes empty + protected fields)
    ah_note: Optional[NoteInfo]  # AH-stored version; None for new-note candidates and deleted notes
    changed_fields: List[Field]  # fields a suggestion would carry, before the user's allowlist
    added_tags: List[str]
    removed_tags: List[str]
    added_new_media: bool

    @property
    def changed_field_names(self) -> List[str]:
        """Names of `changed_fields` — what the dialog offers and the suggestibility check reads."""
        return [f.name for f in self.changed_fields]


def compute_note_diffs(notes: Sequence[Note]) -> Dict[NoteId, NoteDiff]:
    """Compute a `NoteDiff` per note in one batched pass over the AnkiHub DB
    (one membership query + one note-data query for the whole list)."""
    anki_nids = [note.id for note in notes]
    ah_db_rows: Dict[NoteId, AnkiHubNote] = {
        NoteId(row.anki_note_id): row
        for row in execute_list_query_in_chunks(
            lambda nids: AnkiHubNote.filter(anki_note_id__in=nids),
            ids=anki_nids,
        )
    }
    ah_notes_by_nid: Dict[NoteId, NoteInfo] = {
        NoteId(n.anki_nid): n for n in ankihub_db.notes_data_for_anki_nids(anki_nids)
    }
    note_type_dict_cache: Dict[NotetypeId, NotetypeDict] = {}

    result: Dict[NoteId, NoteDiff] = {}
    for note in notes:
        nid = NoteId(note.id)
        ah_db_row = ah_db_rows.get(nid)
        ah_note = ah_notes_by_nid.get(nid)
        is_deleted_on_remote = ah_db_row is not None and ah_db_row.was_deleted()

        cur = to_note_data(
            note,
            include_empty_fields=True,
            include_protected_fields=True,
            note_type_dict_cache=note_type_dict_cache,
            ah_nid=ah_db_row.ankihub_note_id if ah_db_row is not None else None,
        )

        if ah_note is None:
            # New-note candidate: no AH baseline, so "what would ship" is every non-empty
            # field (new-note suggestions never carry empty fields) and all current tags.
            changed_fields = [f for f in cur.fields if f.value]
            added_tags = list(cur.tags or [])
            removed_tags: List[str] = []
        else:
            changed_fields = _fields_that_changed(prev_fields=ah_note.fields, cur_fields=cur.fields)
            added_tags, removed_tags = _added_and_removed_tags(
                prev_tags=ah_note.tags or [],
                cur_tags=cur.tags or [],
            )

        anki_note_type = note.note_type()
        media_anki = set(get_media_names_from_note_info(cur, anki_note_type))
        media_ah = set() if ah_note is None else set(get_media_names_from_note_info(ah_note, anki_note_type))
        added_new_media = bool(media_anki - media_ah)

        result[nid] = NoteDiff(
            exists_in_ah_db=ah_db_row is not None,
            is_deleted_on_remote=is_deleted_on_remote,
            local_note=cur,
            ah_note=ah_note,
            changed_fields=changed_fields,
            added_tags=added_tags,
            removed_tags=removed_tags,
            added_new_media=added_new_media,
        )

    return result


def _is_suggestible_from_diff(
    note: Note,
    diff: NoteDiff,
    change_type: Optional[SuggestionType],
    globally_protected_by_mid: Mapping[NotetypeId, Collection[str]],
) -> bool:
    if diff.is_deleted_on_remote:
        return False
    if change_type == SuggestionType.DELETE:
        return diff.exists_in_ah_db
    # The submit path drops these as EMPTY_FIRST_FIELD for non-DELETE
    # change types; mirror that so we don't accept a note we'll just reject.
    if _has_empty_first_field(note):
        return False
    denied = set(globally_protected_by_mid.get(NotetypeId(note.mid), ()))
    if not diff.exists_in_ah_db and note.note_type()["flds"][0]["name"] in denied:
        # New-note submission requires the first field server-side. If it's
        # globally protected, the suggestion can never succeed regardless of
        # what other fields or tags contribute — so the dialog shouldn't open.
        return False
    if set(diff.changed_field_names) - denied:
        return True
    return bool(diff.added_tags or diff.removed_tags)


def any_suggestible_from_diffs(
    notes: Sequence[Note],
    diffs: Mapping[NoteId, NoteDiff],
    change_type: Optional[SuggestionType],
    globally_protected_by_mid: Mapping[NotetypeId, Collection[str]],
) -> bool:
    """True if at least one note is suggestible under the given `change_type`.

    Mirrors `_suggestions_for_notes()`'s classification:

    - DELETE: existing AnkiHub notes that aren't deleted on remote (no
      content check — DELETE doesn't carry field/tag content).
    - Any other change_type: a not-deleted-on-remote note (new-note
      candidate or existing) is suggestible if it has at least one field
      edit outside the globally-protected denylist or any tag change.
    """
    return any(
        _is_suggestible_from_diff(note, diffs[NoteId(note.id)], change_type, globally_protected_by_mid)
        for note in notes
    )


def suggest_note_update(
    note: Note,
    change_type: SuggestionType,
    comment: str,
    media_upload_cb: MediaUploadCallback,
    auto_accept: bool = False,
    filters: Optional[PerNoteFilters] = None,
    diff: Optional[NoteDiff] = None,
) -> ChangeSuggestionResult:
    """Sends a ChangeNoteSuggestion to AnkiHub if the passed note has changes.
    Also renames media files in the Anki collection and the media folder and uploads them to AnkiHub.
    Returns a ChangeSuggestionResult enum value.

    `filters` carries the user's optional allowlists for fields and added/removed
    tags. `None` for any list means "no filter for that dimension"; the absent
    `filters` arg is equivalent to no filters at all. `diff` is the note's
    precomputed `NoteDiff` (the dialog passes its open-time copy); computed when omitted.
    """

    # DELETE doesn't carry field content, so the empty-first-field requirement
    # doesn't apply — users should be able to delete malformed notes.
    if _has_empty_first_field(note) and change_type != SuggestionType.DELETE:
        return ChangeSuggestionResult.EMPTY_FIRST_FIELD

    suggestion = _change_note_suggestion(note, change_type, comment, filters=filters, diff=diff)
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
    filters: Optional[PerNoteFilters] = None,
    diff: Optional[NoteDiff] = None,
) -> bool:
    """Sends a NewNoteSuggestion to AnkiHub. Returns True on submit, False if
    the user-selected filters left nothing to submit.

    `filters.tags_to_remove` is ignored — new notes have no AH baseline to
    remove tags from. `diff` is the note's precomputed `NoteDiff` (the dialog
    passes its open-time copy); computed when omitted.
    """
    suggestion = _new_note_suggestion(note, ankihub_did, comment, filters=filters, diff=diff)
    if suggestion is None:
        return False

    suggestion = cast(
        NewNoteSuggestion,
        _rename_and_upload_media_for_suggestion(
            suggestion=suggestion,
            ankihub_did=ankihub_did,
            media_upload_cb=media_upload_cb,
        ),
    )

    client = AnkiHubClient()
    client.create_new_note_suggestion(new_note_suggestion=suggestion, auto_accept=auto_accept)
    return True


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
    filters: Optional[BulkSuggestionFilters] = None,
    note_diffs: Optional[Mapping[NoteId, NoteDiff]] = None,
) -> BulkNoteSuggestionsResult:
    """
    Sends a NewNoteSuggestion or a ChangeNoteSuggestion to AnkiHub for each note in the list.
    Note: Notes that don't have any changes when compared to the local
    AnkiHub database will not be sent. This does not necessarily mean
    that the note has no changes when compared to the remote AnkiHub
    database. To create suggestions for notes that differ from the
    remote database but not from the local database, users have to
    sync first (so that the local database is up to date).

    `filters=None` means no filter — ship everything each note's diff detected
    (the dialog always passes an explicit per-mid selection instead). `note_diffs`
    is the precomputed `compute_note_diffs` map (the dialog passes its open-time
    copy); computed here when not supplied.
    """
    if note_diffs is None:
        note_diffs = compute_note_diffs(notes)

    (
        new_note_suggestions,
        change_note_suggestions,
        nids_without_changes,
        nids_deleted_on_remote,
        nids_with_empty_first_field,
    ) = _suggestions_for_notes(notes, ankihub_did, change_type, comment, note_diffs, filters=filters)

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
        errors_by_nid_from_remote = {NoteId(nid): errors for nid, errors in errors_by_nid_int.items()}
    else:
        errors_by_nid_from_remote = {}

    errors_by_nid_from_local = {
        **{nid: ANKIHUB_NO_CHANGE_ERROR for nid in nids_without_changes},
        **{nid: ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR for nid in nids_deleted_on_remote},
        **{nid: ANKIHUB_EMPTY_FIRST_FIELD_ERROR for nid in nids_with_empty_first_field},
    }
    errors_by_nid: Dict[NoteId, Any] = {
        **errors_by_nid_from_remote,
        **errors_by_nid_from_local,
    }

    result = BulkNoteSuggestionsResult(
        errors_by_nid=errors_by_nid,
        change_note_suggestions_count=len([x for x in change_note_suggestions if x.anki_nid not in errors_by_nid]),
        new_note_suggestions_count=len([x for x in new_note_suggestions if x.anki_nid not in errors_by_nid]),
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
    mid_to_ah_did = {mid: ankihub_db.ankihub_did_for_note_type(mid) for mid in set(anki_nid_to_mid.values())}
    anki_nid_to_ah_did_for_new = {nid: mid_to_ah_did[mid] for nid, mid in anki_nid_to_mid.items()}

    return {
        **anki_nid_to_ah_did_for_existing,
        **anki_nid_to_ah_did_for_new,
    }


def _suggestions_for_notes(
    notes: List[Note],
    ankihub_did: uuid.UUID,
    change_type: SuggestionType,
    comment: str,
    note_diffs: Mapping[NoteId, NoteDiff],
    filters: Optional[BulkSuggestionFilters] = None,
) -> Tuple[
    Sequence[NewNoteSuggestion],
    Sequence[ChangeNoteSuggestion],
    Sequence[NoteId],
    Sequence[NoteId],
    Sequence[NoteId],
]:
    """
    Splits the list of notes into five categories:
    - notes that should be sent as NewNoteSuggestions
    - notes that should be sent as ChangeNoteSuggestions
    - notes that should not be sent because they don't have any changes
    - notes that should not be sent because they were deleted on AnkiHub
    - notes that should not be sent because they have an empty first field

    Returns a tuple of five sequences:
    - new_note_suggestions
    - change_note_suggestions
    - nids_without_changes
    - nids_deleted_on_remote
    - nids_with_empty_first_field
    """
    notes_for_new_note_suggestions = []
    notes_for_change_note_suggestions = []
    nids_deleted_on_remote = []
    nids_with_empty_first_field: List[NoteId] = []
    for note in notes:
        # DELETE doesn't carry field content; allow notes with an empty first field through.
        if _has_empty_first_field(note) and change_type != SuggestionType.DELETE:
            nids_with_empty_first_field.append(note.id)
            continue

        diff = note_diffs[NoteId(note.id)]
        if diff.exists_in_ah_db:
            if diff.is_deleted_on_remote:
                nids_deleted_on_remote.append(note.id)
            else:
                notes_for_change_note_suggestions.append(note)
        else:
            notes_for_new_note_suggestions.append(note)

    # Cache per-mid projection across the two loops — same mid → same
    # PerNoteFilters, so we only need one dict allocation per distinct mid.
    per_mid_filters: Dict[NotetypeId, PerNoteFilters] = {}

    def _filters_for(note: Note) -> PerNoteFilters:
        if filters is None:
            # No filter — ship everything the diff detected (same as the
            # single-note path's `filters=None`).
            return PerNoteFilters()
        mid = NotetypeId(note.mid)
        if mid not in per_mid_filters:
            per_mid_filters[mid] = filters.for_mid(mid)
        return per_mid_filters[mid]

    nids_without_changes: List[NoteId] = []

    change_note_suggestions: List[ChangeNoteSuggestion] = []
    for note in notes_for_change_note_suggestions:
        change_suggestion = _change_note_suggestion(
            note=note,
            change_type=change_type,
            comment=comment,
            filters=_filters_for(note),
            diff=note_diffs[NoteId(note.id)],
        )
        if change_suggestion is not None:
            change_note_suggestions.append(change_suggestion)
        else:
            nids_without_changes.append(note.id)

    new_note_suggestions: List[NewNoteSuggestion] = []
    for note in notes_for_new_note_suggestions:
        new_suggestion = _new_note_suggestion(
            note=note,
            ah_did=ankihub_did,
            comment=comment,
            filters=_filters_for(note),
            diff=note_diffs[NoteId(note.id)],
        )
        if new_suggestion is not None:
            new_note_suggestions.append(new_suggestion)
        else:
            nids_without_changes.append(note.id)

    return (
        new_note_suggestions,
        change_note_suggestions,
        nids_without_changes,
        nids_deleted_on_remote,
        nids_with_empty_first_field,
    )


def _apply_field_allowlist(fields: List[Field], allowlist: Optional[Collection[str]]) -> List[Field]:
    """Drop fields not in the user's allowlist. `allowlist=None` means "no user filter" —
    ships everything the diff detected (only reached when a note is submitted without an
    explicit selection). Globally-protected fields are excluded by the dialog *before* the
    user picks; server-side enforcement is the backstop.
    """
    if allowlist is None:
        return fields
    allowed = set(allowlist)
    return [f for f in fields if f.name in allowed]


def _apply_tag_allowlist(tags: List[str], allowlist: Optional[Collection[str]]) -> List[str]:
    if allowlist is None:
        return tags
    allowed = set(allowlist)
    return [t for t in tags if t in allowed]


def _new_note_suggestion(
    note: Note,
    ah_did: uuid.UUID,
    comment: str,
    filters: Optional[PerNoteFilters] = None,
    diff: Optional[NoteDiff] = None,
) -> Optional[NewNoteSuggestion]:
    # `tags_to_remove` on `filters` is ignored — new notes have no AH baseline
    # to remove tags from.
    filters = filters or PerNoteFilters()
    diff = diff if diff is not None else compute_note_diffs([note])[NoteId(note.id)]
    # `changed_fields` is already the non-empty fields; tags are all current tags.
    fields = _apply_field_allowlist(diff.changed_fields, filters.fields_to_include)
    tags = _apply_tag_allowlist(diff.added_tags, filters.tags_to_add)

    # The server requires the first field; user-deselect or globally-protected
    # filtering can drop it. Reject here rather than letting the server return
    # EMPTY_FIRST_FIELD on submit.
    first_field_name = note.note_type()["flds"][0]["name"]
    if not any(f.name == first_field_name and f.value for f in fields):
        return None

    if not fields and not tags:
        return None

    return NewNoteSuggestion(
        ah_did=ah_did,
        # New notes have no AnkiHub id yet; mint one.
        ah_nid=uuid.uuid4(),
        anki_nid=note.id,
        fields=fields,
        tags=tags,
        note_type_name=note.note_type()["name"],
        anki_note_type_id=note.mid,
        guid=note.guid,
        comment=comment,
    )


def _change_note_suggestion(
    note: Note,
    change_type: SuggestionType,
    comment: str,
    filters: Optional[PerNoteFilters] = None,
    diff: Optional[NoteDiff] = None,
) -> Optional[ChangeNoteSuggestion]:
    filters = filters or PerNoteFilters()
    diff = diff if diff is not None else compute_note_diffs([note])[NoteId(note.id)]
    assert diff.local_note.ah_nid is not None

    added_tags: List[str] = []
    removed_tags: List[str] = []
    fields_that_changed: List[Field] = []

    # DELETE carries no field/tag content, so it ships an empty suggestion.
    if change_type != SuggestionType.DELETE:
        fields_that_changed = _apply_field_allowlist(diff.changed_fields, filters.fields_to_include)
        added_tags = _apply_tag_allowlist(diff.added_tags, filters.tags_to_add)
        removed_tags = _apply_tag_allowlist(diff.removed_tags, filters.tags_to_remove)

        if not added_tags and not removed_tags and not fields_that_changed:
            return None

    return ChangeNoteSuggestion(
        ah_nid=diff.local_note.ah_nid,
        anki_nid=note.id,
        fields=fields_that_changed,
        added_tags=added_tags,
        removed_tags=removed_tags,
        change_type=change_type,
        comment=comment,
    )


def _added_and_removed_tags(prev_tags: List[str], cur_tags: List[str]) -> Tuple[List[str], List[str]]:
    added_tags = [tag for tag in cur_tags if not is_tag_in_list(tag, prev_tags)]
    removed_tags = [tag for tag in prev_tags if not is_tag_in_list(tag, cur_tags)]
    return added_tags, removed_tags


def _fields_that_changed(prev_fields: List[Field], cur_fields: List[Field]) -> List[Field]:
    result = [
        cur_field for cur_field, prev_field in zip(cur_fields, prev_fields) if cur_field.value != prev_field.value
    ]
    return result


def _rename_and_upload_media_for_suggestion(
    suggestion: NoteSuggestion,
    ankihub_did: uuid.UUID,
    media_upload_cb: MediaUploadCallback,
) -> NoteSuggestion:
    suggestion = _rename_and_upload_media_for_suggestions([suggestion], ankihub_did, media_upload_cb=media_upload_cb)[0]
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
        note_info for suggestion in suggestions if (note_info := ankihub_db.note_data(NoteId(suggestion.anki_nid)))
    ]
    original_media_names: Set[str] = get_media_names_from_notes_data(
        original_notes_data, lambda mid: ankihub_db.note_type_dict(NotetypeId(mid))
    )

    anki_nids = [s.anki_nid for s in suggestions]
    nid_to_note_type = {
        nid: ankihub_db.note_type_dict(NotetypeId(mid))
        for nid, mid in aqt.mw.col.db.all(f"select id, mid from notes where id in {ids2str(anki_nids)}")
    }
    suggestion_media_names: Set[str] = get_media_names_from_suggestions(
        suggestions, lambda s: nid_to_note_type[s.anki_nid]
    )

    # Filter out unchanged media file names so we don't hash and upload media files that aren't part of the suggestion
    media_names_added_to_note = suggestion_media_names.difference(original_media_names)

    # Filter out media names without media files in the Anki collection
    media_dir = Path(aqt.mw.col.media.dir())
    media_names_added_to_note = {name for name in media_names_added_to_note if (media_dir / name).exists()}

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
    added_media_paths = [(media_dir / media_name) for media_name in media_names_added_to_ah_deck]

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
    media_to_hash_dict = {media_name: md5_file_hash(media_dir / media_name) for media_name in media_names}

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


def _replace_media_names_in_suggestion(suggestion: NoteSuggestion, media_name_map: Dict[str, str]):
    suggestion.fields = [_field_with_replaced_media_names(field, media_name_map) for field in suggestion.fields]


def _field_with_replaced_media_names(field: Field, media_name_map: Dict[str, str]) -> Field:
    result = copy.deepcopy(field)
    for old_name, new_name in media_name_map.items():
        result.value = result.value.replace(f'src="{old_name}"', f'src="{new_name}"')
        result.value = result.value.replace(f"src='{old_name}'", f"src='{new_name}'")
        result.value = result.value.replace(f"[sound:{old_name}]", f"[sound:{new_name}]")
    return result


def _update_media_names_on_notes(media_name_map: Dict[str, str]):
    for original_filename, new_filename in media_name_map.items():
        find_and_replace_text_in_fields_on_all_notes(f'src="{original_filename}"', f'src="{new_filename}"')
        find_and_replace_text_in_fields_on_all_notes(f"src='{original_filename}'", f"src='{new_filename}'")
        find_and_replace_text_in_fields_on_all_notes(f"[sound:{original_filename}]", f"[sound:{new_filename}]")
