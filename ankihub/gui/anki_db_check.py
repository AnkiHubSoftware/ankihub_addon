import uuid
from concurrent.futures import Future
from typing import List

from anki.utils import ids2str
from aqt import mw
from aqt.utils import askUser, showInfo

from .. import LOGGER
from ..db import ankihub_db
from ..reset_changes import reset_local_changes_to_notes
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, config


def check_anki_db():
    # This is a fix for a bug in another add-on that removed the AnkiHub ID field from note types.
    # To restore the missing ankihub_ids this function resets local changes to the affected decks
    # when the user confirms the dialog.

    if not (ah_dids_with_missing_ah_nids := _decks_with_missing_ankihub_nids()):
        LOGGER.debug("No decks with missing ankihub_ids found.")
        return

    LOGGER.debug(
        f"Decks with missing ankihub_ids found: {ah_dids_with_missing_ah_nids}"
    )

    deck_names = sorted(
        [
            config.private_config.decks[deck_id]["name"]
            for deck_id in ah_dids_with_missing_ah_nids
        ],
        key=str.lower,
    )

    if askUser(
        text=(
            "AnkiHub has detected that the following deck(s) have missing values:<br>"
            f"{'<br>'.join('<b>' + deck_name + '</b>' for deck_name in deck_names)}<br><br>"
            "The add-on needs to reset local changes to these decks. This may take a while.<br><br>"
            "Protected fields and tags will not be affected.<br><br>"
            "A full sync with AnkiWeb might be necessary after the reset, so it's recommended "
            "to sync changes from other devices before doing this.<br><br>"
            "Do you want to fix this now?"
        ),
        title="AnkiHub Database Check",
    ):
        mw.taskman.with_progress(
            lambda: _reset_decks(ah_dids_with_missing_ah_nids),
            on_done=on_done,
            label="Resetting local changes...",
        )


def on_done(future: Future):
    future.result()

    LOGGER.debug("Done resetting local changes.")
    showInfo("Missing values have been restored.")


def _decks_with_missing_ankihub_nids():
    result = []
    ah_dids = ankihub_db.ankihub_dids()
    for ah_did in ah_dids:

        # add ah_did to results if for any note type of the deck the AnkiHub ID field does not exist
        mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
        for mid in mids:
            note_type = mw.col.models.get(mid)

            if note_type is None:
                continue

            ankihub_id_field = next(
                (
                    field_dict
                    for field_dict in note_type["flds"]
                    if field_dict["name"] == ANKIHUB_NOTE_TYPE_FIELD_NAME
                ),
                None,
            )
            if ankihub_id_field is None:
                result.append(ah_did)
                break
        else:
            # add ah_did to results if for any note of the deck the AnkiHub ID field is empty
            nids = ankihub_db.notes_for_ankihub_deck(ah_did)
            field_seperator = "\x1f"  # see anki.utils.split_fields

            # Check if any note has an empty AnkiHub ID field by checking if the last field is empty.
            # This is much faster than loading all notes and checking the AnkiHub ID field. The speed
            # matters because this function is called on every startup.
            note_with_empty_last_field_exists = bool(
                mw.col.db.scalar(
                    "SELECT EXISTS("
                    "   SELECT 1 FROM notes "
                    f"  WHERE id in {ids2str(nids)} AND SUBSTR(flds, -1) == '{field_seperator}'"
                    ")",
                )
            )
            if note_with_empty_last_field_exists:
                result.append(ah_did)
                break

    return result


def _reset_decks(ah_dids: List[str]):
    for ah_did in ah_dids:
        nids = ankihub_db.notes_for_ankihub_deck(ah_did)
        reset_local_changes_to_notes(nids, ankihub_deck_uuid=uuid.UUID(ah_did))
