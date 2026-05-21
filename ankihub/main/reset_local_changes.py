"""Code for resetting notes to the state they have in the AnkiHub database.
(This is the state that the notes had on AnkiHub when the add-on synced with AnkiHub last time.)
"""

import uuid
from typing import Sequence

import aqt
from anki.errors import NotFoundError
from anki.notes import NoteId

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..db import ankihub_db
from ..settings import config
from .importing import AnkiHubImporter
from .note_conversion import is_protect_tag


def reset_local_changes_to_notes(
    nids: Sequence[NoteId],
    ah_did: uuid.UUID,
) -> None:
    # all notes have to be from the ankihub deck with the given uuid

    deck_config = config.deck_config(ah_did)

    client = AnkiHubClient()
    protected_fields = client.get_protected_fields(ah_did=ah_did)
    protected_tags = client.get_protected_tags(ah_did=ah_did)

    # Personal-protect tags (AnkiHub_Protect::*) are part of local state — added
    # by the user or the auto-protect-on-edit hook to keep specific fields from
    # being overwritten on sync. "Reset local changes" wipes local state, so strip
    # them first; otherwise the importer's `_prepare_fields` honours them and
    # refuses to reset the very fields the user edited.
    _strip_personal_protect_tags(nids)

    notes_data = ankihub_db.notes_data_for_anki_nids(nids)
    note_types = {
        mid: ankihub_db.note_type_dict(note_type_id=mid) for mid in ankihub_db.note_types_for_ankihub_deck(ah_did)
    }

    importer = AnkiHubImporter()
    importer.import_ankihub_deck(
        ankihub_did=ah_did,
        notes=notes_data,
        note_types=note_types,
        deck_name=deck_config.name,
        is_first_import_of_deck=False,
        behavior_on_remote_note_deleted=deck_config.behavior_on_remote_note_deleted,
        anki_did=deck_config.anki_id,
        protected_fields=protected_fields,
        protected_tags=protected_tags,
        # we don't move existing notes between decks here, users might not want that
        subdecks_for_new_notes_only=deck_config.subdecks_enabled,
        suspend_new_cards_of_new_notes=deck_config.suspend_new_cards_of_new_notes,
        suspend_new_cards_of_existing_notes=deck_config.suspend_new_cards_of_existing_notes,
        # TODO Warn user if full sync is required when resetting local changes
        raise_if_full_sync_required=False,
    )

    # this way the notes won't be marked as "changed after sync" anymore
    ankihub_db.reset_mod_values_in_anki_db(list(nids))


def _strip_personal_protect_tags(nids: Sequence[NoteId]) -> None:
    # Some nids may refer to locally-deleted notes — the importer recreates them
    # later in the reset flow, so silently skip them here.
    changed = []
    for nid in nids:
        try:
            note = aqt.mw.col.get_note(nid)
        except NotFoundError:
            continue
        new_tags = [t for t in note.tags if not is_protect_tag(t)]
        if new_tags != note.tags:
            note.tags = new_tags
            changed.append(note)
    if changed:
        aqt.mw.col.update_notes(changed)
