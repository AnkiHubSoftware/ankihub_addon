import os
import uuid
from typing import Callable, Dict, List

import pytest
from anki.models import NotetypeDict, NotetypeId
from pytest_anki import AnkiSession

from .conftest import Profile

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client import NoteInfo, SuggestionType
from ankihub.main.importing import AnkiHubImporter
from ankihub.main.suggestions import (
    any_suggestible_from_diffs,
    compute_note_diffs,
)
from ankihub.settings import BehaviorOnRemoteNoteDeleted, DeckConfig


@pytest.mark.performance
def test_bulk_suggestion_dialog_open_diff_pipeline(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    anking_notes_data: List[NoteInfo],
    anking_note_types: Dict[NotetypeId, NotetypeDict],
    profile: Profile,
):
    """Measures the per-note work that runs synchronously on the UI thread
    when the user opens the bulk Suggest-a-change dialog at the 500-note cap.
    Exercises `compute_note_diffs` once and feeds its result through the
    bulk-suggestible gate and the media check. The widget's `_populate` is
    explicitly excluded — it's cheap per-note filtering off already-computed
    diffs — and isn't covered by this test.
    """
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw
        notes_amount = 500  # matches the per-bulk-suggestion cap

        ankihub_did = next_deterministic_uuid()
        importer = AnkiHubImporter()
        importer.import_ankihub_deck(
            ankihub_did=ankihub_did,
            notes=anking_notes_data[:notes_amount],
            deck_name="test",
            is_first_import_of_deck=True,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
            note_types=anking_note_types,
            protected_fields={},
            protected_tags=[],
            suspend_new_cards_of_new_notes=False,
            suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
        )

        notes = [mw.col.get_note(nid) for nid in mw.col.find_notes("")]
        assert len(notes) == notes_amount  # sanity check

        # Realistic worst case: every note has a field edit and a tag change,
        # so the gate short-circuits fast but the widget's per-note diff
        # still runs over the full set.
        for note in notes:
            note.fields[0] = note.fields[0] + " edit"
            note.tags = list(note.tags) + ["perf-test-tag"]
            mw.col.update_note(note)

        def dialog_open_diff_pipeline() -> None:
            diffs = compute_note_diffs(notes)
            any_suggestible_from_diffs(notes, diffs, SuggestionType.UPDATED_CONTENT, {})
            any(d.added_new_media for d in diffs.values())
            # Widget _populate is omitted: it's per-note field/tag filtering off the
            # already-computed diffs, which adds negligible cost.

        duration = profile(dialog_open_diff_pipeline)
        print(f"Bulk dialog-open diff pipeline over {len(notes)} notes took {duration:.3f} seconds")
        # Soft regression gate: CI baseline is ~0.2s, so 2.0s leaves ~10×
        # headroom against unrelated runner slowness while still catching a
        # ~5× regression. Bump if CI hardware changes substantially.
        assert duration < 2.0
