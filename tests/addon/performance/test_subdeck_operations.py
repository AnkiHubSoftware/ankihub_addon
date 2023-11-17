import os

import pytest
from anki.models import NotetypeDict
from pytest_anki import AnkiSession

from tests.fixtures import ImportAHNotes, InstallAHDeck

from ...factories import NoteInfoFactory
from .conftest import Profile

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.main.subdecks import (
    SUBDECK_TAG,
    build_subdecks_and_move_cards_to_them,
    flatten_deck,
)


@pytest.mark.performance
class TestBuildSubdecksAndMoveCardsToThem:
    def test_basic(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_notes: ImportAHNotes,
        install_ah_deck: InstallAHDeck,
        profile: Profile,
        ankihub_basic_note_type: NotetypeDict,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()

            # Create 1000 notes with subdeck tags
            note_infos = NoteInfoFactory.create_batch(
                size=1000,
                tags=[f"{SUBDECK_TAG}::test::test"],
                mid=ankihub_basic_note_type["id"],
            )
            import_ah_notes(note_infos=note_infos, ah_did=ah_did)
            nids = [note_info.anki_nid for note_info in note_infos]

            # Profile the operation
            duration_seconds = profile(
                lambda: build_subdecks_and_move_cards_to_them(
                    ankihub_did=ah_did, nids=nids
                )
            )
            print(
                f"Moving {len(nids)} cards to their subdecks took {duration_seconds} seconds"
            )
            assert duration_seconds < 0.1


@pytest.mark.performance
class TestFlattenDeck:
    def test_basic(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_notes: ImportAHNotes,
        install_ah_deck: InstallAHDeck,
        profile: Profile,
        ankihub_basic_note_type: NotetypeDict,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()

            # Create 1000 notes with subdeck tags
            note_infos = NoteInfoFactory.create_batch(
                size=1000,
                tags=[f"{SUBDECK_TAG}::test::test"],
                mid=ankihub_basic_note_type["id"],
            )
            import_ah_notes(note_infos=note_infos, ah_did=ah_did)
            nids = [note_info.anki_nid for note_info in note_infos]

            # Move the notes to their subdecks
            build_subdecks_and_move_cards_to_them(ankihub_did=ah_did, nids=nids)

            # Profile the operation
            duration_seconds = profile(lambda: flatten_deck(ankihub_did=ah_did))
            print(
                f"Flattening deck with {len(nids)} cards took {duration_seconds} seconds"
            )
            assert duration_seconds < 0.1
