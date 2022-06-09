from pprint import pformat
from typing import Dict, List, Set, Tuple
from urllib.error import HTTPError

import anki
import aqt
from anki import notetypes_pb2, utils
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import NoteType, NotetypeId
from anki.notes import Note, NoteId
from aqt import mw
from aqt.utils import askUser
from requests.exceptions import ConnectionError

from . import LOGGER, constants
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .config import config


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
    dids_str = utils.ids2str(dids)
    # odid is the original did for cards in filtered decks
    query = (
        "SELECT DISTINCT mid FROM cards "
        "INNER JOIN notes ON cards.nid = notes.id "
        f"WHERE did in {dids_str} or odid in {dids_str}"
    )
    return mw.col.db.list(query)


def hide_ankihub_field_in_editor(
    js: str, note: anki.notes.Note, _: aqt.editor.Editor
) -> str:
    if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
        return js
    extra = (
        'require("svelte/internal").tick().then(() => '
        "{{ require('anki/NoteEditor').instances[0].fields["
        "require('anki/NoteEditor').instances[0].fields.length -1"
        "].element.then((element) "
        "=> {{ element.hidden = true; }}); }});"
    )
    js += extra
    return js


def create_note_with_id(note_type_id: int, anki_id: int) -> Note:
    """Create a new note, add it to the appropriate deck and override the note id with
    the note id of the original note creator."""
    LOGGER.debug(f"Trying to create note: {note_type_id=} {anki_id}.")

    note_type = mw.col.models.get(NotetypeId(note_type_id))
    note = Note(col=mw.col, model=note_type)
    # TODO Add to an appropriate deck.
    mw.col.add_note(note, DeckId(1))
    # Swap out the note id that Anki assigns to the new note with our own id.
    sql = (
        f"UPDATE notes SET id={anki_id} WHERE id={note.id};"
        f"UPDATE cards SET nid={anki_id} WHERE nid={note.id};"
    )
    mw.col.db.execute(sql)
    LOGGER.debug(f"Created note: {anki_id}")
    return note


def update_note(
    note: Note, anki_id: int, ankihub_id: int, fields: List[Dict], tags: List[str]
):
    note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_id)
    note.tags = [str(tag) for tag in tags]
    # TODO Make sure we don't update protected fields.
    for field in fields:
        note[field["name"]] = field["value"]
    LOGGER.debug(f"Updated note {anki_id}")


def update_or_create_note(
    anki_id: int,
    ankihub_id: int,
    fields: List[Dict],
    tags: List[str],
    note_type_id: int,
) -> Note:
    try:
        note = mw.col.get_note(id=NoteId(anki_id))
        fields.append(
            {
                "name": constants.ANKIHUB_NOTE_TYPE_FIELD_NAME,
                # Put the AnkiHub field last
                "order": len(fields),
                "value": ankihub_id,
            }
        )
        update_note(note, anki_id, ankihub_id, fields, tags)
        mw.col.update_note(note)
    except NotFoundError:
        note = create_note_with_id(note_type_id, anki_id)
        LOGGER.debug(f"Created note {anki_id}")

        update_note(note, anki_id, ankihub_id, fields, tags)
    return note


def sync_with_ankihub():
    LOGGER.debug("Trying to sync with AnkiHub.")
    client = AnkiHubClient()
    decks = config.private_config.decks
    for deck in decks:
        collected_notes = []
        for response in client.get_deck_updates(
            deck, since=config.private_config.last_sync
        ):
            if response.status_code != 200:
                return

            data = response.json()
            notes = data["notes"]
            if notes:
                collected_notes += notes

        if collected_notes:

            mw._create_backup_with_progress(user_initiated=False)
            change_note_types_of_notes_in_collection_if_necessary(collected_notes)

            for note in collected_notes:
                (
                    deck_id,
                    ankihub_id,
                    tags,
                    anki_id,
                    fields,
                    note_type,
                    note_type_id,
                ) = note.values()
                LOGGER.debug(f"Trying to update or create note:\n {pformat(note)}")
                update_or_create_note(anki_id, ankihub_id, fields, tags, note_type)
                # Should last sync be tracked separately for each deck?
                mw.reset()
                config.save_last_sync(time=data["latest_update"])


def sync_on_profile_open():
    if config.private_config.token:
        try:
            # Don't raise exception when automatically attempting to sync with AnkiHub
            # with no Internet connection.
            sync_with_ankihub()
        except (ConnectionError, HTTPError):
            pass
        sync_with_ankihub()


def change_note_types_of_notes_in_collection_if_necessary(
    notes_data: List[Dict],
) -> None:

    # tuples of (note id, target note type id, local note type id)
    note_type_conflicts: Set[Tuple[NoteId, NotetypeId, NotetypeId]] = set()
    for note_dict in notes_data:
        (
            anki_nid_str,
            deck_id,
            fields,
            ankihub_id,
            last_sync,
            note_type,
            note_type_id_str,
            tags,
        ) = note_dict.values()
        anki_nid = NoteId(int(anki_nid_str))
        note_type_id = NotetypeId(int(note_type_id_str))

        try:
            note = mw.col.get_note(anki_nid)
        except Exception:
            continue

        if note.mid != note_type_id:
            note_type_conflicts.add((note.id, note_type_id, note.mid))

    if not note_type_conflicts:
        return

    LOGGER.debug(
        f"Note types of local notes differ from remote note types: {note_type_conflicts}",
    )

    if not askUser(
        "Note types of some AnkiHub managed notes were changed. If you continue, they will be changed back.\n"
        "When you press Yes, Anki will ask you to confirm a full sync with AnkiWeb on the next sync.\n"
        "Continue synchronization with AnkiHub?"
    ):
        return

    if not mw.confirm_schema_modification():
        return

    # TODO get missing note types from AnkiHub

    current_schema: int = mw.col.db.scalar("select scm from col")
    for anki_nid, target_note_type_id, _ in note_type_conflicts:
        note = mw.col.get_note(anki_nid)
        note_type = mw.col.models.get(target_note_type_id)
        fields = note_type["flds"]
        request = notetypes_pb2.ChangeNotetypeRequest(
            note_ids=[note.id],
            old_notetype_id=note.mid,
            new_notetype_id=target_note_type_id,
            current_schema=current_schema,
            new_fields=list(range(0, len(fields))),
        )
        mw.col.models.change_notetype_of_notes(request)

    # TODO make sure all managed local note types have the same fields as the remote note types
