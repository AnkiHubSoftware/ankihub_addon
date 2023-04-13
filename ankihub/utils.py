import re
import time
from pprint import pformat
from textwrap import dedent
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aqt
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import ChangeNotetypeRequest, NoteType, NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.utils import checksum, ids2str

from . import LOGGER, settings
from .settings import (
    ANKI_MINOR,
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    ANKIHUB_NOTE_TYPE_MODIFICATION_STRING,
    ANKIHUB_TEMPLATE_END_COMMENT,
    url_view_note,
)


# decks
def create_deck_with_id(deck_name: str, deck_id: DeckId) -> None:

    source_did = aqt.mw.col.decks.add_normal_deck_with_name(
        get_unique_deck_name(deck_name)
    ).id
    aqt.mw.col.db.execute(f"UPDATE decks SET id={deck_id} WHERE id={source_did};")
    aqt.mw.col.db.execute(f"UPDATE cards SET did={deck_id} WHERE did={source_did};")

    LOGGER.info(f"Created deck {deck_name=} {deck_id=}")


def all_dids() -> Set[DeckId]:
    return {DeckId(x.id) for x in aqt.mw.col.decks.all_names_and_ids()}


def lowest_level_common_ancestor_did(dids: Iterable[DeckId]) -> Optional[DeckId]:
    return aqt.mw.col.decks.id_for_name(
        lowest_level_common_ancestor_deck_name(
            [aqt.mw.col.decks.name(did) for did in dids]
        )
    )


def lowest_level_common_ancestor_deck_name(deck_names: Iterable[str]) -> Optional[str]:
    lowest_level_deck_name = max(deck_names, key=lambda name: name.count("::"))
    parts = lowest_level_deck_name.split("::")
    result = lowest_level_deck_name
    for i in range(1, len(parts) + 1):
        cur_deck_name = "::".join(parts[:i])
        if any(not name.startswith(cur_deck_name) for name in deck_names):
            result = "::".join(parts[: i - 1])
            break

    if result == "":
        return None
    else:
        return result


def get_unique_deck_name(deck_name: str) -> str:
    if not aqt.mw.col.decks.by_name(deck_name):
        return deck_name

    suffix = " (AnkiHub)"
    if suffix not in deck_name:
        deck_name += suffix
    else:
        deck_name += f" {checksum(str(time.time()))[:5]}"
    return deck_name


def highest_level_did(dids: Iterable[DeckId]) -> DeckId:
    return min(dids, key=lambda did: aqt.mw.col.decks.name(did).count("::"))


# notes
def create_note_with_id(note: Note, anki_id: NoteId, anki_did: DeckId) -> Note:
    """Create a new note, add it to the appropriate deck and override the note id with
    the note id of the original note creator."""
    LOGGER.debug(f"Trying to create note: {anki_id=}")

    aqt.mw.col.add_note(note, DeckId(anki_did))

    # Swap out the note id that Anki assigns to the new note with our own id.
    aqt.mw.col.db.execute(f"UPDATE notes SET id={anki_id} WHERE id={note.id};")
    aqt.mw.col.db.execute(f"UPDATE cards SET nid={anki_id} WHERE nid={note.id};")

    note.id = anki_id
    return note


def note_types_with_ankihub_id_field() -> List[NotetypeId]:
    return [
        mid
        for mid in aqt.mw.col.models.ids()
        if has_ankihub_id_field(aqt.mw.col.models.get(mid))
    ]


def has_ankihub_id_field(model: NotetypeDict) -> bool:
    return any(field["name"] == ANKIHUB_NOTE_TYPE_FIELD_NAME for field in model["flds"])


def nids_in_deck_but_not_in_subdeck(deck_name: str) -> Sequence[NoteId]:
    """Return note IDs of notes that are in the deck but not also in a subdeck of the deck.
    For example if a notes is in the deck "A" but not in "A::B" or "A::C" then it is returned.
    """
    return aqt.mw.col.find_notes(f'deck:"{deck_name}" -deck:"{deck_name}::*"')


# note types
def create_note_type_with_id(note_type: NotetypeDict, mid: NotetypeId) -> None:
    note_type["id"] = 0
    changes = aqt.mw.col.models.add_dict(note_type)

    # Swap out the note type id that Anki assigns to the new note type with our own id.
    # TODO check if seperate statements are necessary
    aqt.mw.col.db.execute(f"UPDATE notetypes SET id={mid} WHERE id={changes.id};")
    aqt.mw.col.db.execute(f"UPDATE templates SET ntid={mid} WHERE ntid={changes.id};")
    aqt.mw.col.db.execute(f"UPDATE fields SET ntid={mid} WHERE ntid={changes.id};")
    aqt.mw.col.models._clear_cache()  # TODO check if this is necessary

    LOGGER.info(f"Created note type: {mid}")
    LOGGER.info(f"Note type:\n {pformat(note_type)}")


def note_type_contains_field(
    note_type: NoteType, field=settings.ANKIHUB_NOTE_TYPE_FIELD_NAME
) -> bool:
    """Check that a field is defined in the note type."""
    fields: List[Dict] = note_type["flds"]
    field_names = [field["name"] for field in fields]
    return field in field_names


def get_note_types_in_deck(did: DeckId) -> List[NotetypeId]:
    """Returns list of note model ids in the given deck."""
    dids = [did]
    dids += [child[1] for child in aqt.mw.col.decks.children(did)]
    dids_str = ids2str(dids)
    # odid is the original did for cards in filtered decks
    query = (
        "SELECT DISTINCT mid FROM cards "
        "INNER JOIN notes ON cards.nid = notes.id "
        f"WHERE did in {dids_str} or odid in {dids_str}"
    )
    return aqt.mw.col.db.list(query)


def reset_note_types_of_notes(nid_mid_pairs: List[Tuple[NoteId, NotetypeId]]) -> None:

    note_type_conflicts: Set[Tuple[NoteId, NotetypeId, NotetypeId]] = set()
    for nid, mid in nid_mid_pairs:
        try:
            note = aqt.mw.col.get_note(nid)
        except NotFoundError:
            # we don't care about missing notes here
            continue

        if note.mid != mid:
            note_type_conflicts.add((note.id, mid, note.mid))

    for anki_nid, target_note_type_id, _ in note_type_conflicts:
        LOGGER.info(
            f"Note types differ: anki_nid: {anki_nid} target_note_type_id {target_note_type_id}",
        )
        change_note_type_of_note(anki_nid, target_note_type_id)
        LOGGER.info(
            f"Changed note type: anki_nid {anki_nid} target_note_type_id {target_note_type_id}",
        )

    LOGGER.info("Reset note types of notes.")


def change_note_type_of_note(nid: int, mid: int) -> None:

    current_schema: int = aqt.mw.col.db.scalar("select scm from col")
    note = aqt.mw.col.get_note(NoteId(nid))
    target_note_type = aqt.mw.col.models.get(NotetypeId(mid))
    request = ChangeNotetypeRequest(
        note_ids=[note.id],
        old_notetype_id=note.mid,
        new_notetype_id=NotetypeId(mid),
        current_schema=current_schema,
        new_fields=list(range(0, len(target_note_type["flds"]))),
    )
    aqt.mw.col.models.change_notetype_of_notes(request)


# ... note type modifications

ANKIHUB_TEMPLATE_SNIPPET_RE = (
    f"<!-- BEGIN {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
    r"[\w\W]*"
    f"<!-- END {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
)


def modify_note_type(note_type: NotetypeDict) -> None:
    """Adds the AnkiHub ID Field to the Note Type and modifies the card templates."""
    LOGGER.info(f"Modifying note type {note_type['name']}")

    modify_fields(note_type)

    templates = note_type["tmpls"]
    for template in templates:
        modify_template(template)


def modify_note_type_templates(note_type_ids: Iterable[NotetypeId]) -> None:
    for mid in note_type_ids:
        note_type = aqt.mw.col.models.get(mid)
        for template in note_type["tmpls"]:
            modify_template(template)
        aqt.mw.col.models.update_dict(note_type)


def modify_fields(note_type: Dict) -> None:
    fields = note_type["flds"]
    field_names = [field["name"] for field in fields]
    if settings.ANKIHUB_NOTE_TYPE_FIELD_NAME in field_names:
        LOGGER.info(f"{settings.ANKIHUB_NOTE_TYPE_FIELD_NAME} already exists.")
        return
    ankihub_field = aqt.mw.col.models.new_field(ANKIHUB_NOTE_TYPE_FIELD_NAME)
    # Put AnkiHub field last
    ankihub_field["ord"] = len(fields)
    note_type["flds"].append(ankihub_field)


def modify_template(template: Dict) -> None:
    # the order is important here, the end comment must be added last
    add_ankihub_snippet_to_template(template)
    add_ankihub_end_comment_to_template(template)


def add_ankihub_snippet_to_template(template: Dict) -> None:
    ankihub_snippet = dedent(
        f"""
        <!-- BEGIN {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->
        {{{{#{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}
        <a class='ankihub-view-note' href='{url_view_note()}{{{{{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}'>
            View Note on AnkiHub
        </a>

        <style>
        .ankihub-view-note {{
            display: none;
        }}

        .mobile .ankihub-view-note {{
            display: block;
        }}

        .ankihub-view-note {{
            text-decoration: none;
            position: fixed;
            left: 50%;
            margin-right: -50%;
            transform: translate(-50%, -50%);
            bottom: 0;
            padding: 0.5rem;
            border-radius: 50px;
            background-color: #cde3f8;
            font-size: 12px;
            color: black;
        }}

        .ankihub-view-note:hover,
        .ankihub-view-note:active {{
            background-color: #ebf3fa;
        }}
        </style>

        {{{{/{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}
        <!-- END {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->
        """
    ).strip("\n")

    snippet_pattern = (
        f"<!-- BEGIN {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
        r"[\w\W]*"
        f"<!-- END {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
    )

    if not re.search(snippet_pattern, template["afmt"]):
        template["afmt"] = template["afmt"].rstrip("\n ") + "\n\n" + ankihub_snippet
    else:
        # update existing snippet to make sure it is up to date
        template["afmt"] = re.sub(
            snippet_pattern,
            ankihub_snippet,
            template["afmt"],
        )


def add_ankihub_end_comment_to_template(template: Dict) -> None:
    for key in ["qfmt", "afmt"]:
        cur_side = template[key]
        if re.search(ANKIHUB_TEMPLATE_END_COMMENT, cur_side):
            continue

        template[key] = (
            template[key].rstrip("\n ") + "\n\n" + ANKIHUB_TEMPLATE_END_COMMENT + "\n\n"
        )
        LOGGER.info(
            f"Added ANKIHUB_TEMPLATE_END_COMMENT to template {template['name']} on side {key}"
        )


# ... undo modifications
def undo_note_type_modfications(note_type_ids: Iterable[NotetypeId]) -> None:
    for mid in note_type_ids:
        note_type = aqt.mw.col.models.get(mid)
        if note_type is None:
            continue

        undo_note_type_modification(note_type)
        aqt.mw.col.models.update_dict(note_type)


def undo_note_type_modification(note_type: Dict) -> None:
    """Removes the AnkiHub Field from the Note Type and modifies the template to
    remove the field.
    """
    LOGGER.info(f"Undoing modification of note type {note_type['name']}")

    undo_fields_modification(note_type)

    templates = note_type["tmpls"]
    for template in templates:
        undo_template_modification(template)


def undo_fields_modification(note_type: Dict) -> None:
    fields = note_type["flds"]
    field_names = [field["name"] for field in fields]
    if settings.ANKIHUB_NOTE_TYPE_FIELD_NAME not in field_names:
        return
    fields.pop(field_names.index(settings.ANKIHUB_NOTE_TYPE_FIELD_NAME))


def undo_template_modification(template: Dict) -> None:
    template["afmt"] = re.sub(
        r"\n{0,2}" + ANKIHUB_TEMPLATE_SNIPPET_RE,
        "",
        template["afmt"],
    )


# backup
def create_backup() -> None:
    # has to be called from a background thread
    LOGGER.info("Starting backup...")
    try:
        created: Optional[bool] = None
        if ANKI_MINOR >= 50:
            _create_backup_with_retry_anki_50()
        else:
            aqt.mw.col.close(downgrade=False)
            aqt.mw.backup()  # type: ignore
            aqt.mw.col.reopen(after_full_sync=False)
            # here we don't know if the backup was created
        # if there were no changes, no backup is created
        LOGGER.info(f"Backup successful. {created=}")
    except Exception as exc:
        LOGGER.info("Backup failed")
        raise exc


def _create_backup_with_retry_anki_50() -> bool:
    """Create a backup and retry once if it raises an exception."""
    # The backup can fail with DBError("Cannot start transaction within a transaction")
    # when aqt.mw.autosave() is called while the backup is running.
    # This can happen for example when an aqt.operations.CollectionOp is executed in another thread. After the
    # CollectionOp is finished, autosave is called, which calls mw.db.begin().
    # When the backup is finished, it also calls mw.db.begin() which raises an exception
    # if begin() was already called and no mw.db.commit() or mw.db.rollback() was called in between.
    # See https://ankihub.sentry.io/issues/3801328076/?project=6546414.
    # There are other ways to handle this problem, but this seems like a safe one in the sense that it doesn't
    # rely on the implementation of the backup function staying the same.
    try:
        created = _create_backup_anki_50()
    except Exception as exc:
        LOGGER.info(f"Backup failed on the first attempt. {exc=}")

        # retry once
        LOGGER.info("Retrying backup...")
        try:
            created = _create_backup_anki_50()
        except Exception as exc:
            LOGGER.info("Backup failed second time")
            raise exc

    LOGGER.info(f"Backup successful. {created=}")
    return created


def _create_backup_anki_50() -> bool:
    """Create a backup and return whether it was created or not.
    Works for Anki 2.1.50 and newer."""
    created = aqt.mw.col.create_backup(
        backup_folder=aqt.mw.pm.backupFolder(),
        force=True,
        wait_for_completion=True,
    )
    return created


def truncated_list(values: List[Any], limit: int) -> List[Any]:
    assert limit > 0
    return values[:limit] + ["..."] if len(values) > limit else values
