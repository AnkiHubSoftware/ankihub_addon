"""Code for creating note suggestions based on changes to notes in the Anki collection relative
to the version stored in the AnkiHub database. Suggestions are sent to AnkiHub.
(The AnkiHub database is the source of truth for the notes in the AnkiHub deck and is updated
when syncing with AnkiHub.)"""

import copy
import re
import shutil
import uuid
from dataclasses import dataclass, field
from enum import Enum
from html import unescape
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
from ..common_utils import AV_TAGS, HTML_MEDIA_TAGS
from ..db import ankihub_db
from ..db.db import execute_list_query_in_chunks
from ..db.models import AnkiHubNote
from ..settings import config
from .exporting import to_note_data
from .media_utils import find_and_replace_text_in_fields_on_all_notes
from .utils import get_anki_nid_to_mid_dict, is_tag_in_list, md5_file_hash

# The Fields-to-Suggest selector and the auto-protect-on-edit hook share this
# flag — auto-protect's silent tagging only makes sense if the dialog surfaces
# those tags back to the user.
AUTO_PROTECT_FEATURE_FLAG = "auto_protect_fields_when_edited"


# string that is contained in the errors returned from the AnkiHub API when
# there are no changes to the note for a change note suggestion
ANKIHUB_NO_CHANGE_ERROR = "Suggestion fields and tags don't have any changes to the original note"

# string that is contained in the errors returned from the AnkiHub API when
# the note does not exist on AnkiHub
ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR = "Note object does not exist"

# string for errors when the first field of a note is empty
ANKIHUB_EMPTY_FIRST_FIELD_ERROR = "The first field is required and cannot be empty"

# string contained in the error returned when a *new note* suggestion is rejected
# because the deck already contains a note with the same anki_id. The note exists on
# AnkiHub but the local AnkiHub DB doesn't know it yet (pre-sync), so the add-on
# submitted a new-note suggestion instead of a change suggestion.
ANKIHUB_DUPLICATE_ANKI_ID_ERROR = "A deck can't contain multiple notes with the same anki_id."

# Matches any HTML tag — used to strip markup when checking a field for visible text.
HTML_TAG_REGEX = re.compile(r"<[^>]+>")


def _first_if_list(value: Any) -> Any:
    """The backend wraps ValidationError detail values in single-element lists
    (DRF normalization). Unwrap those, tolerating already-scalar values."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def parse_duplicate_anki_id_error(errors: Any) -> Optional[Tuple[Optional[uuid.UUID], bool]]:
    """If `errors` is the duplicate-anki_id error, return
    ``(conflicting_ah_nid_or_None, is_deleted)``; otherwise return ``None``.

    `errors` may be the server's ``validation_errors`` dict, a plain error string
    (local categories), or a dict without the conflicting fields. The conflicting
    fields arrive list-wrapped and stringified, so ``conflicting_note_deleted`` is
    compared against the literal ``"True"`` rather than truthiness.
    """
    if ANKIHUB_DUPLICATE_ANKI_ID_ERROR not in str(errors):
        return None

    conflicting_ah_nid: Optional[uuid.UUID] = None
    is_deleted = False
    if isinstance(errors, dict):
        raw_id = _first_if_list(errors.get("conflicting_ankihub_id"))
        if raw_id:
            try:
                conflicting_ah_nid = uuid.UUID(str(raw_id))
            except (ValueError, TypeError):
                conflicting_ah_nid = None
        is_deleted = str(_first_if_list(errors.get("conflicting_note_deleted"))) == "True"
    return conflicting_ah_nid, is_deleted


class ChangeSuggestionResult(Enum):
    SUCCESS = "success"
    NO_CHANGES = "no changes"
    ANKIHUB_NOT_FOUND = "not found"
    EMPTY_FIRST_FIELD = "empty first field"


class MediaUploadCallback(Protocol):
    def __call__(self, media_names: Set[str], ankihub_did: uuid.UUID) -> None: ...


@dataclass
class PerNoteFilters:
    """Allowlists that select what content goes into one note's suggestion.
    `None` for any list means "no filter for this dimension" (legacy
    behavior: ship everything the diff detected). Globally-protected fields
    are stripped *before* the dialog renders, so they never enter the
    allowlist — server-side enforcement is the only backstop at submit.
    """

    fields_to_include: Optional[Collection[str]] = None
    tags_to_add: Optional[Collection[str]] = None
    tags_to_remove: Optional[Collection[str]] = None


@dataclass
class BulkSuggestionFilters:
    """Same as `PerNoteFilters` but keyed by note type for the bulk-suggest
    path — the dialog groups field selections by mid because the widget
    has one section per note type. Tag filters apply across all notes.
    """

    fields_to_include_by_mid: Optional[Mapping[NotetypeId, Collection[str]]] = None
    tags_to_add: Optional[Collection[str]] = None
    tags_to_remove: Optional[Collection[str]] = None

    def for_mid(self, mid: NotetypeId) -> PerNoteFilters:
        # `fields_to_include_by_mid` non-None but missing this mid means the
        # widget rendered no field section for it (every edit was globally
        # protected). Treat that as an empty allowlist — NOT "no filter" —
        # so the diff can't ship fields the user never saw or selected.
        return PerNoteFilters(
            fields_to_include=(
                self.fields_to_include_by_mid.get(mid, ()) if self.fields_to_include_by_mid is not None else None
            ),
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
    ah_note: Optional[NoteInfo]  # AH-stored version; None for new-note candidates and deleted notes
    # For change-note: field names whose value differs from AH.
    # For new-note: all non-empty field names.
    edited_fields: List[str]
    added_tags: List[str]
    removed_tags: List[str]
    added_new_media: bool


def compute_note_diffs(notes: Sequence[Note]) -> Dict[NoteId, NoteDiff]:
    """Single-pass diff computation against the local AnkiHub DB.

    Caches `note_type_dict` by mid for the batch and projects ah_nid from
    the batched AH-DB rows, avoiding two per-note DB lookups inside
    `to_note_data`.
    """
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
            edited_fields = [f.name for f in cur.fields if f.value]
            added_tags = list(cur.tags or [])
            removed_tags: List[str] = []
        else:
            edited_fields = [f.name for f in _fields_that_changed(prev_fields=ah_note.fields, cur_fields=cur.fields)]
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
            ah_note=ah_note,
            edited_fields=edited_fields,
            added_tags=added_tags,
            removed_tags=removed_tags,
            added_new_media=added_new_media,
        )

    return result


def globally_protected_fields_by_mid(ah_did: uuid.UUID) -> Dict[NotetypeId, Set[str]]:
    """Coerce the cached globally-protected fields into the typed shape that
    `_is_suggestible_from_diff`, `any_suggestible_from_diffs`, and the dialog
    widget consume."""
    return {NotetypeId(mid): set(names) for mid, names in config.globally_protected_fields(ah_did).items()}


def has_empty_first_field(note: Note) -> bool:
    """True when the note's first field has no visible content.

    Strips HTML tags and entities, then checks for remaining text — so plain
    whitespace and markup-only values (e.g. "<br>", "&nbsp;") count as empty,
    while embedded media (images/audio) counts as content. This intentionally
    approximates Anki's own `fields_check` rather than calling it, to avoid a
    per-note backend round-trip (which also does a duplicate DB lookup); the
    server still rejects a truly-empty first field as a backstop.
    """
    if not note.fields:
        return True
    field = note.fields[0]
    if HTML_MEDIA_TAGS.search(field) or AV_TAGS.search(field):
        return False
    return not unescape(HTML_TAG_REGEX.sub("", field)).strip()


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
    if has_empty_first_field(note):
        return False
    denied = set(globally_protected_by_mid.get(NotetypeId(note.mid), ()))
    if not diff.exists_in_ah_db and note.note_type()["flds"][0]["name"] in denied:
        # New-note submission requires the first field server-side. If it's
        # globally protected, the suggestion can never succeed regardless of
        # what other fields or tags contribute — so the dialog shouldn't open.
        return False
    if set(diff.edited_fields) - denied:
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
) -> ChangeSuggestionResult:
    """Sends a ChangeNoteSuggestion to AnkiHub if the passed note has changes.
    Also renames media files in the Anki collection and the media folder and uploads them to AnkiHub.
    Returns a ChangeSuggestionResult enum value.

    `filters` carries the user's optional allowlists for fields and added/removed
    tags. `None` for any list means "no filter for that dimension"; the absent
    `filters` arg is equivalent to no filters at all.
    """

    # DELETE doesn't carry field content, so the empty-first-field requirement
    # doesn't apply — users should be able to delete malformed notes.
    if has_empty_first_field(note) and change_type != SuggestionType.DELETE:
        return ChangeSuggestionResult.EMPTY_FIRST_FIELD

    suggestion = _change_note_suggestion(note, change_type, comment, filters=filters)
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
) -> bool:
    """Sends a NewNoteSuggestion to AnkiHub. Returns True on submit, False if
    the user-selected filters left nothing to submit.

    `filters.tags_to_remove` is ignored — new notes have no AH baseline to
    remove tags from.
    """
    suggestion = _new_note_suggestion(note, ankihub_did, comment, filters=filters)
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
class AlreadyInDeckConflict:
    """A new-note suggestion rejected because the note already exists in the deck
    on AnkiHub (non-deleted). Carries everything needed to resubmit it as a change
    suggestion without a remote fetch: the original (already media-renamed) new-note
    suggestion plus the existing note's ankihub id."""

    new_note_suggestion: NewNoteSuggestion
    conflicting_ah_nid: uuid.UUID


@dataclass
class BulkNoteSuggestionsResult:
    errors_by_nid: Dict[NoteId, List[str]]
    # nids rejected with the duplicate-anki_id error that can be resubmitted as
    # change suggestions ("Notes already in this deck"). Soft-deleted conflicts are
    # routed to the deleted-on-AnkiHub category instead and are not included here.
    already_in_deck_by_nid: Dict[NoteId, AlreadyInDeckConflict] = field(default_factory=dict)
    new_note_suggestions_count: int = 0
    change_note_suggestions_count: int = 0


def suggest_notes_in_bulk(
    ankihub_did: uuid.UUID,
    notes: List[Note],
    auto_accept: bool,
    change_type: SuggestionType,
    comment: str,
    media_upload_cb: MediaUploadCallback,
    filters: Optional[BulkSuggestionFilters] = None,
) -> BulkNoteSuggestionsResult:
    """
    Sends a NewNoteSuggestion or a ChangeNoteSuggestion to AnkiHub for each note in the list.
    Note: Notes that don't have any changes when compared to the local
    AnkiHub database will not be sent. This does not necessarily mean
    that the note has no changes when compared to the remote AnkiHub
    database. To create suggestions for notes that differ from the
    remote database but not from the local database, users have to
    sync first (so that the local database is up to date).
    """
    (
        new_note_suggestions,
        change_note_suggestions,
        nids_without_changes,
        nids_deleted_on_remote,
        nids_with_empty_first_field,
    ) = _suggestions_for_notes(notes, ankihub_did, change_type, comment, filters=filters)

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

    # Pull out new-note suggestions rejected because the note already exists in the
    # deck on AnkiHub. Non-deleted conflicts become resubmittable "already in this
    # deck" entries; soft-deleted conflicts are re-pointed to the deleted-on-AnkiHub
    # category (a change suggestion would 404 against a tombstoned note).
    new_note_suggestion_by_nid = {NoteId(ns.anki_nid): ns for ns in new_note_suggestions}
    already_in_deck_by_nid: Dict[NoteId, AlreadyInDeckConflict] = {}
    for nid, errors in list(errors_by_nid.items()):
        parsed = parse_duplicate_anki_id_error(errors)
        if parsed is None:
            continue
        conflicting_ah_nid, is_deleted = parsed
        if is_deleted:
            errors_by_nid[nid] = ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR
            continue
        new_note_suggestion = new_note_suggestion_by_nid.get(nid)
        if conflicting_ah_nid is not None and new_note_suggestion is not None:
            already_in_deck_by_nid[nid] = AlreadyInDeckConflict(
                new_note_suggestion=new_note_suggestion,
                conflicting_ah_nid=conflicting_ah_nid,
            )
        # Missing conflicting id (e.g. older server) → leave the error in place so it
        # surfaces under "Other errors"; no resubmit affordance is offered.

    result = BulkNoteSuggestionsResult(
        errors_by_nid=errors_by_nid,
        change_note_suggestions_count=len([x for x in change_note_suggestions if x.anki_nid not in errors_by_nid]),
        new_note_suggestions_count=len([x for x in new_note_suggestions if x.anki_nid not in errors_by_nid]),
        already_in_deck_by_nid=already_in_deck_by_nid,
    )
    return result


def _new_note_to_change_suggestion(
    new_note_suggestion: NewNoteSuggestion,
    conflicting_ah_nid: uuid.UUID,
    change_type: SuggestionType,
) -> ChangeNoteSuggestion:
    """Convert a new-note suggestion that was rejected as a duplicate into a change
    suggestion for the existing note (`conflicting_ah_nid`). The user's selected
    fields/tags are reused as-is; the server diffs them against its copy of the note.
    No remote fetch or local AnkiHub-DB write is needed."""
    return ChangeNoteSuggestion(
        ah_nid=conflicting_ah_nid,
        anki_nid=new_note_suggestion.anki_nid,
        fields=new_note_suggestion.fields,
        comment=new_note_suggestion.comment,
        added_tags=list(new_note_suggestion.tags or []),
        removed_tags=[],
        change_type=change_type,
    )


def resubmit_new_note_as_change_suggestion(
    note: Note,
    ah_did: uuid.UUID,
    conflicting_ah_nid: uuid.UUID,
    change_type: SuggestionType,
    comment: str,
    auto_accept: bool = False,
    filters: Optional[PerNoteFilters] = None,
) -> ChangeSuggestionResult:
    """Resubmit a single note (whose new-note suggestion hit the duplicate-anki_id
    error) as a change suggestion for the existing note. Media was already renamed and
    uploaded by the failed new-note submit, so it is not re-uploaded here."""
    new_note_suggestion = _new_note_suggestion(note, ah_did, comment, filters=filters)
    if new_note_suggestion is None:
        return ChangeSuggestionResult.NO_CHANGES

    change_note_suggestion = _new_note_to_change_suggestion(new_note_suggestion, conflicting_ah_nid, change_type)
    client = AnkiHubClient()
    try:
        client.create_change_note_suggestion(
            change_note_suggestion=change_note_suggestion,
            auto_accept=auto_accept,
        )
    except AnkiHubHTTPError as e:
        if e.response.status_code == 404:
            return ChangeSuggestionResult.ANKIHUB_NOT_FOUND
        raise e

    return ChangeSuggestionResult.SUCCESS


def resubmit_new_notes_as_change_suggestions_in_bulk(
    conflicts: Mapping[NoteId, AlreadyInDeckConflict],
    change_type: SuggestionType,
    auto_accept: bool = False,
) -> Dict[NoteId, Any]:
    """Resubmit the "already in this deck" notes as change suggestions in a single
    bulk call. Returns the per-nid validation errors for the ones that failed again
    (a nid absent from the result succeeded). Media was already uploaded by the
    original new-note submit, so it is not re-uploaded here."""
    if not conflicts:
        return {}

    change_note_suggestions = [
        _new_note_to_change_suggestion(c.new_note_suggestion, c.conflicting_ah_nid, change_type)
        for c in conflicts.values()
    ]
    client = AnkiHubClient()
    errors_by_nid_int = client.create_suggestions_in_bulk(
        change_note_suggestions=change_note_suggestions,
        auto_accept=auto_accept,
    )
    return {NoteId(nid): errors for nid, errors in errors_by_nid_int.items()}


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
    anki_nids = [note.id for note in notes]

    ah_db_notes = execute_list_query_in_chunks(
        lambda anki_nids: AnkiHubNote.filter(anki_note_id__in=anki_nids),
        ids=anki_nids,
    )
    ah_db_note_by_anki_nid = {NoteId(ah_db_note.anki_note_id): ah_db_note for ah_db_note in ah_db_notes}

    notes_for_new_note_suggestions = []
    notes_for_change_note_suggestions = []
    nids_deleted_on_remote = []
    nids_with_empty_first_field: List[NoteId] = []
    for note in notes:
        # DELETE doesn't carry field content; allow notes with an empty first field through.
        if has_empty_first_field(note) and change_type != SuggestionType.DELETE:
            nids_with_empty_first_field.append(note.id)
            continue

        if ah_db_note := ah_db_note_by_anki_nid.get(note.id):
            ah_db_note = cast(AnkiHubNote, ah_db_note)
            if ah_db_note.was_deleted():
                nids_deleted_on_remote.append(note.id)
            else:
                notes_for_change_note_suggestions.append(note)
        else:
            notes_for_new_note_suggestions.append(note)

    filters = filters or BulkSuggestionFilters()
    # Cache per-mid projection across the two loops — same mid → same
    # PerNoteFilters, so we only need one dict allocation per distinct mid.
    per_mid_filters: Dict[NotetypeId, PerNoteFilters] = {}

    def _filters_for(note: Note) -> PerNoteFilters:
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
    """Drop fields not in the user's allowlist. `allowlist=None` means "no user filter"
    (legacy behavior — ships everything the diff detected). Globally-protected fields are
    excluded by the dialog *before* the user picks; server-side enforcement is the backstop.
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
) -> Optional[NewNoteSuggestion]:
    # `tags_to_remove` on `filters` is ignored — new notes have no AH baseline
    # to remove tags from.
    filters = filters or PerNoteFilters()
    note_data = to_note_data(
        note, set_new_id=True, include_protected_fields=config.get_feature_flags().get(AUTO_PROTECT_FEATURE_FLAG, False)
    )
    fields = _apply_field_allowlist(list(note_data.fields), filters.fields_to_include)
    tags = _apply_tag_allowlist(list(note_data.tags or []), filters.tags_to_add)

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
        ah_nid=note_data.ah_nid,
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
) -> Optional[ChangeNoteSuggestion]:
    filters = filters or PerNoteFilters()
    note_from_anki_db = to_note_data(
        note,
        include_empty_fields=True,
        include_protected_fields=config.get_feature_flags().get(AUTO_PROTECT_FEATURE_FLAG, False),
    )
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

        fields_that_changed = _apply_field_allowlist(fields_that_changed, filters.fields_to_include)
        added_tags = _apply_tag_allowlist(added_tags, filters.tags_to_add)
        removed_tags = _apply_tag_allowlist(removed_tags, filters.tags_to_remove)

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
