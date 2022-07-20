import re
import time
from pprint import pformat
from typing import Dict, Iterable, List, Optional, Set, Tuple

from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import ChangeNotetypeRequest, NoteType, NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.utils import checksum, ids2str
from aqt import mw
from aqt.utils import tr

from . import LOGGER, constants, report_exception
from .constants import (
    ANKI_MINOR,
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    ANKIHUB_NOTE_TYPE_MODIFICATION_STRING,
    URL_VIEW_NOTE,
)


# decks
def create_deck_with_id(deck_name: str, deck_id: DeckId) -> None:

    source_did = mw.col.decks.add_normal_deck_with_name(
        get_unique_deck_name(deck_name)
    ).id
    mw.col.db.execute(f"UPDATE decks SET id={deck_id} WHERE id={source_did};")
    mw.col.db.execute(f"UPDATE cards SET did={deck_id} WHERE did={source_did};")

    LOGGER.debug(f"Created deck {deck_name=} {deck_id=}")


def all_dids() -> Set[DeckId]:
    return {DeckId(x.id) for x in mw.col.decks.all_names_and_ids()}


def lowest_level_common_ancestor_did(dids: Iterable[DeckId]) -> Optional[DeckId]:
    return mw.col.decks.id_for_name(
        lowest_level_common_ancestor_deck_name([mw.col.decks.name(did) for did in dids])
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
    if not mw.col.decks.by_name(deck_name):
        return deck_name

    suffix = " (AnkiHub)"
    if suffix not in deck_name:
        deck_name += suffix
    else:
        deck_name += f" {checksum(str(time.time()))[:5]}"
    return deck_name


def highest_level_did(dids: Iterable[DeckId]) -> DeckId:
    return min(dids, key=lambda did: mw.col.decks.name(did).count("::"))


# notes
def create_note_with_id(note: Note, anki_id: NoteId, anki_did: DeckId) -> Note:
    """Create a new note, add it to the appropriate deck and override the note id with
    the note id of the original note creator."""
    LOGGER.debug(f"Trying to create note: {anki_id=}")

    mw.col.add_note(note, DeckId(anki_did))

    # Swap out the note id that Anki assigns to the new note with our own id.
    mw.col.db.execute(f"UPDATE notes SET id={anki_id} WHERE id={note.id};")
    mw.col.db.execute(f"UPDATE cards SET nid={anki_id} WHERE nid={note.id};")

    note = mw.col.get_note(anki_id)
    return note


# note types
def create_note_type_with_id(note_type: NotetypeDict, mid: NotetypeId) -> None:
    note_type["id"] = 0
    changes = mw.col.models.add_dict(note_type)

    # Swap out the note type id that Anki assigns to the new note type with our own id.
    # TODO check if seperate statements are necessary
    mw.col.db.execute(f"UPDATE notetypes SET id={mid} WHERE id={changes.id};")
    mw.col.db.execute(f"UPDATE templates SET ntid={mid} WHERE ntid={changes.id};")
    mw.col.db.execute(f"UPDATE fields SET ntid={mid} WHERE ntid={changes.id};")
    mw.col.models._clear_cache()  # TODO check if this is necessary

    LOGGER.debug(f"Created note type: {mid}")
    LOGGER.debug(f"Note type:\n {pformat(note_type)}")


def note_type_contains_field(
    note_type: NoteType, field=constants.ANKIHUB_NOTE_TYPE_FIELD_NAME
) -> bool:
    """Check that a field is defined in the note type."""
    fields: List[Dict] = note_type["flds"]
    field_names = [field["name"] for field in fields]
    return field in field_names


def get_note_types_in_deck(did: DeckId) -> List[NotetypeId]:
    """Returns list of note model ids in the given deck."""
    dids = [did]
    dids += [child[1] for child in mw.col.decks.children(did)]
    dids_str = ids2str(dids)
    # odid is the original did for cards in filtered decks
    query = (
        "SELECT DISTINCT mid FROM cards "
        "INNER JOIN notes ON cards.nid = notes.id "
        f"WHERE did in {dids_str} or odid in {dids_str}"
    )
    return mw.col.db.list(query)


def reset_note_types_of_notes(nid_mid_pairs: List[Tuple[NoteId, NotetypeId]]) -> None:

    note_type_conflicts: Set[Tuple[NoteId, NotetypeId, NotetypeId]] = set()
    for nid, mid in nid_mid_pairs:
        try:
            note = mw.col.get_note(nid)
        except NotFoundError:
            # we don't care about missing notes here
            continue

        if note.mid != mid:
            note_type_conflicts.add((note.id, mid, note.mid))

    for anki_nid, target_note_type_id, _ in note_type_conflicts:
        LOGGER.debug(
            f"Note types differ: anki_nid: {anki_nid} target_note_type_id {target_note_type_id}",
        )
        change_note_type_of_note(anki_nid, target_note_type_id)
        LOGGER.debug(
            f"Changed note type: anki_nid {anki_nid} target_note_type_id {target_note_type_id}",
        )

    LOGGER.debug("Reset note types of notes.")


def change_note_type_of_note(nid: int, mid: int) -> None:

    current_schema: int = mw.col.db.scalar("select scm from col")
    note = mw.col.get_note(NoteId(nid))
    target_note_type = mw.col.models.get(NotetypeId(mid))
    request = ChangeNotetypeRequest(
        note_ids=[note.id],
        old_notetype_id=note.mid,
        new_notetype_id=NotetypeId(mid),
        current_schema=current_schema,
        new_fields=list(range(0, len(target_note_type["flds"]))),
    )
    mw.col.models.change_notetype_of_notes(request)


# ... note type modifications
def modify_note_types(note_type_ids: Iterable[NotetypeId]) -> None:
    for mid in note_type_ids:
        note_type = mw.col.models.get(mid)
        modify_note_type(note_type)
        mw.col.models.update_dict(note_type)


def modify_note_type(note_type: NotetypeDict) -> None:
    """Adds the AnkiHub Field to the Note Type and modifies the template to
    display the field.
    """
    "Adds ankihub field. Adds link to ankihub in card template."
    LOGGER.debug(f"Modifying note type {note_type['name']}")

    modify_fields(note_type)

    templates = note_type["tmpls"]
    for template in templates:
        modify_template(template)


def modify_note_type_templates(note_type_ids: Iterable[NotetypeId]) -> None:

    for mid in note_type_ids:
        note_type = mw.col.models.get(mid)
        for template in note_type["tmpls"]:
            modify_template(template)
        mw.col.models.update_dict(note_type)


def modify_fields(note_type: Dict) -> None:
    fields = note_type["flds"]
    field_names = [field["name"] for field in fields]
    if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME in field_names:
        LOGGER.debug(f"{constants.ANKIHUB_NOTE_TYPE_FIELD_NAME} already exists.")
        return
    ankihub_field = mw.col.models.new_field(ANKIHUB_NOTE_TYPE_FIELD_NAME)
    # Put AnkiHub field last
    ankihub_field["ord"] = len(fields)
    note_type["flds"].append(ankihub_field)


def modify_template(template: Dict) -> None:
    ankihub_snippet = (
        f"<!-- BEGIN {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
        "<br><br>"
        f"\n{{{{#{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}\n"
        "<a class='ankihub' "
        f"href='{URL_VIEW_NOTE}{{{{{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}'>"
        "\nView Note on AnkiHub\n"
        "</a>"
        f"\n{{{{/{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}\n"
        "<br>"
        f"<!-- END {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
    )

    snippet_pattern = (
        f"<!-- BEGIN {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
        r"[\w\W]*"
        f"<!-- END {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
    )

    if re.search(snippet_pattern, template["afmt"]):
        LOGGER.debug("Template modification was already present, updated it")
        template["afmt"] = re.sub(
            snippet_pattern,
            ankihub_snippet,
            template["afmt"],
        )
    else:
        template["afmt"] += "\n\n" + ankihub_snippet


# backup
def create_backup_with_progress() -> None:
    # has to be called from a background thread
    # if there is already a progress bar present this will not create a new one / modify the existing one

    LOGGER.debug("Starting backup...")
    try:
        label = tr.profiles_creating_backup()
    except:  # < 2.1.50
        label = "Creating Backup..."
    mw.progress.start(label=label)
    try:
        if ANKI_MINOR >= 50:
            mw.col.create_backup(
                backup_folder=mw.pm.backupFolder(),
                force=True,
                wait_for_completion=True,
            )
        else:
            mw.col.close(downgrade=False)
            mw.backup()  # type: ignore
            mw.col.reopen(after_full_sync=False)
        LOGGER.debug("Backup successful.")
    except Exception as exc:
        LOGGER.debug("Backup failed.")
        report_exception()
        raise exc
    finally:
        mw.progress.finish()
