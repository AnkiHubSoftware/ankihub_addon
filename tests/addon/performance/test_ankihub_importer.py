import os
from typing import Dict, List, Protocol

import aqt
import pytest
from anki.models import NotetypeDict, NotetypeId
from anki.notes import NoteId
from pytest_anki import AnkiSession

from ankihub.db.db import ankihub_db

from .conftest import Profile

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client import NoteInfo
from ankihub.main.importing import AnkiHubImporter
from ankihub.main.utils import change_note_types_of_notes
from ankihub.settings import BehaviorOnRemoteNoteDeleted, DeckConfig


class ImportAnkingNotes(Protocol):
    def __call__(
        self,
        anking_notes_data: List[NoteInfo],
    ) -> None: ...


@pytest.fixture
def import_anking_notes(
    next_deterministic_uuid,
    anking_note_types: Dict[NotetypeId, NotetypeDict],
) -> ImportAnkingNotes:
    """Imports the given AnKing NoteInfos using the AnkHubImporter."""

    ah_did = next_deterministic_uuid()

    def import_anking_notes_inner(
        anking_notes_data: List[NoteInfo],
    ) -> None:
        first_import = ah_did not in ankihub_db.ankihub_dids()
        importer = AnkiHubImporter()
        importer.import_ankihub_deck(
            ankihub_did=ah_did,
            notes=anking_notes_data,
            deck_name="test",
            is_first_import_of_deck=first_import,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
            note_types=anking_note_types,
            protected_fields={},
            protected_tags=[],
            suspend_new_cards_of_new_notes=False,
            suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            raise_if_full_sync_required=False,
        )

    return import_anking_notes_inner


@pytest.mark.performance
def test_anking_deck_first_time_import(
    anki_session_with_addon_data: AnkiSession,
    anking_notes_data: List[NoteInfo],
    import_anking_notes: ImportAnkingNotes,
    profile: Profile,
):
    """Test that importing a portion of the AnKing deck for the first time takes less than a threshold duration."""
    with anki_session_with_addon_data.profile_loaded():
        notes_data = anking_notes_data[:100]
        duration_seconds = profile(lambda: import_anking_notes(notes_data))
        print(f"Importing {len(notes_data)} notes took {duration_seconds} seconds")
        assert duration_seconds < 0.5


@pytest.mark.performance
def test_anking_deck_update(
    anki_session_with_addon_data: AnkiSession,
    anking_notes_data: List[NoteInfo],
    import_anking_notes: ImportAnkingNotes,
    profile: Profile,
):
    """Test that updating a portion of the AnKing deck takes less than a threshold duration."""
    with anki_session_with_addon_data.profile_loaded():
        notes_data = anking_notes_data[:100]
        import_anking_notes(notes_data)

        # Change the note type of notes so that the import has to change it back.
        # This way we will also test the performance of changing note types.
        current_note_type = aqt.mw.col.models.get(NotetypeId(notes_data[0].mid))
        new_note_type = aqt.mw.col.models.copy(current_note_type)

        nid_mid_pairs = [(NoteId(note.anki_nid), new_note_type["id"]) for note in notes_data]
        change_note_types_of_notes(nid_mid_pairs=nid_mid_pairs)

        # Change the fields and tags of notes so that the import has to update them.
        for note in notes_data:
            note.fields[0].value = "updated"
            note.tags = ["updated"]

        duration_seconds = profile(lambda: import_anking_notes(notes_data))
        print(f"Importing {len(notes_data)} notes took {duration_seconds} seconds")
        assert duration_seconds < 0.5
