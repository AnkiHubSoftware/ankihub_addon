import uuid
from concurrent.futures import Future
from typing import List

import aqt
from anki.utils import ids2str
from aqt.utils import showInfo

from .... import LOGGER
from ....db import NOTE_NOT_DELETED_CONDITION, ankihub_db, flat
from ....db.models import AnkiHubNote
from ....main.reset_local_changes import reset_local_changes_to_notes
from ....settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, config
from ...utils import ask_user


def check_anki_db():
    LOGGER.info("Checking Anki database...")

    _check_missing_ankihub_nids()

    _check_ankihub_update_tags()


def _check_missing_ankihub_nids() -> None:
    # This is a fix for a bug in another add-on that removed the AnkiHub ID field from note types.
    # To restore the missing ankihub_ids this function resets local changes to the affected decks
    # when the user confirms the dialog.

    if not (ah_dids_with_missing_ah_nids := _decks_with_missing_ankihub_nids()):
        LOGGER.info("No decks with missing ankihub_ids found.")
        return

    LOGGER.info(
        "Decks with missing ankihub_ids found.",
        ah_dids_with_missing_ah_nids=ah_dids_with_missing_ah_nids,
    )

    deck_names = sorted(
        [config.deck_config(deck_id).name for deck_id in ah_dids_with_missing_ah_nids],
        key=str.lower,
    )

    if ask_user(
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
        aqt.mw.taskman.with_progress(
            lambda: _reset_decks(ah_dids_with_missing_ah_nids),
            on_done=_on_done,
            label="Resetting local changes...",
        )


def _on_done(future: Future):
    future.result()

    LOGGER.info("Done resetting local changes.")
    showInfo("Missing values have been restored.")


def _decks_with_missing_ankihub_nids() -> List[uuid.UUID]:
    result = []
    ah_dids = ankihub_db.ankihub_dids()
    for ah_did in ah_dids:

        # add ah_did to results if for any note type of the deck the AnkiHub ID field does not exist
        mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
        for mid in mids:
            note_type = aqt.mw.col.models.get(mid)

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
            # add ah_did to results if for any (not deleted) note of the deck the AnkiHub ID field is empty
            nids = (
                AnkiHubNote.select(AnkiHubNote.anki_note_id)
                .filter(
                    NOTE_NOT_DELETED_CONDITION,
                    ankihub_deck_id=ah_did,
                )
                .objects(flat)
            )
            field_seperator = "\x1f"  # see anki.utils.split_fields

            # Check if any note has an empty AnkiHub ID field by checking if the last field is empty.
            # This is much faster than loading all notes and checking the AnkiHub ID field. The speed
            # matters because this function is called on every startup.
            note_with_empty_last_field_exists = bool(
                aqt.mw.col.db.scalar(
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


def _reset_decks(ah_dids: List[uuid.UUID]):
    for ah_did in ah_dids:
        nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
        reset_local_changes_to_notes(nids, ah_did=ah_did)


def _check_ankihub_update_tags() -> None:
    """Check if the user has notes with AnkiHub_Update tags and ask if they want to remove them.
    AnkiHub_Update tags were used in a previous version of the add-on and are not longer needed.
    The purpose of this function is also to inform the user about the change and tell them how to
    see which notes were updated and for what reason.
    """

    nids = aqt.mw.col.find_notes("tag:AnkiHub_Update::*")
    if not nids:
        LOGGER.info("No notes with AnkiHub_Update tag found.")
        return

    LOGGER.info("Notes with AnkiHub_Update tag found.")

    if not ask_user(
        "The AnkiHub add-on has improved the way you can see which notes were updated (and for what reason)! "
        "<br><br>"
        "Previously, you could see this by looking at the <b>AnkiHub_Update</b> tags that were added to notes "
        "when they were updated. "
        "<br><br>"
        "Now, you can see this information in the left sidebar of the Anki browser when you click on the<br>"
        "<b>AnkiHub -> Updated Today</b> category. "
        "<br><br>"
        "The <b>AnkiHub_Update</b> tags can be safely removed from all notes. "
        "Do you want to remove the <b>AnkiHub_Update</b> tags from all notes now?",
        title="AnkiHub",
    ):
        LOGGER.info("User chose not to remove AnkiHub_Update tags.")
        return

    def on_done(future: Future):
        future.result()

        showInfo("AnkiHub_Update tags removed from all notes.")
        LOGGER.info("AnkiHub_Update tags removed from all notes.")

    aqt.mw.taskman.with_progress(
        task=_remove_ankihub_update_tags,
        on_done=on_done,
        label="Removing AnkiHub_Update tags...",
    )


def _remove_ankihub_update_tags():
    tags_to_remove = [
        tag
        for tag in aqt.mw.col.tags.all()
        if tag.lower().startswith("ankihub_update::")
    ]
    tags_to_remove_str = " ".join(tags_to_remove)
    aqt.mw.col.tags.remove(tags_to_remove_str)
