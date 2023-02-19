import uuid

from aqt.utils import askUserDialog

from . import LOGGER
from .db import ankihub_db
from .settings import config


def resolve_conflicts_for_deck(ankihub_did: uuid.UUID) -> bool:
    """Resolves conflicts for a deck.
    Returns True if the conflicts were resolved, False if the user cancelled."""

    LOGGER.info(f"Resolving conflicts for deck {ankihub_did=}")

    while conflict := ankihub_db.next_conflict(ankihub_did):
        conflicting_ah_did, conflicting_anki_nids = conflict
        LOGGER.info(
            f"Resolving conflict for deck with {ankihub_did=} with deck {conflicting_ah_did=}."
        )

        dialog = askUserDialog(
            text=(
                f"AnkiHub has detected that that there is a conflict between the following decks:<br>"
                f"<b>{config.deck_config(ankihub_did).name}</b>"
                f"<b>{config.deck_config(conflicting_ah_did).name}</b><br><br>"
                f"There are {len(conflicting_anki_nids)} note ids that are in both decks.<br><br>"
                "Which deck do you want to sync these notes with?"
            ),
            title="AnkiHub - Deck Conflict",
            buttons=["Cancel", "Deck 1", "Deck 2"],
        )
        answer = dialog.run()
        if answer == "Cancel":
            LOGGER.info("User cancelled conflict resolution.")
            return False
        elif answer == "Deck 1":
            LOGGER.info("User chose to sync notes with deck 1.")
            ankihub_db.deactivate_notes_for_deck(
                ankihub_did=ankihub_did, anki_nids=conflicting_anki_nids
            )
        elif answer == "Deck 2":
            LOGGER.info("User chose to sync notes with deck 2.")
            ankihub_db.deactivate_notes_for_deck(
                ankihub_did=ankihub_did, anki_nids=conflicting_anki_nids
            )
        else:
            raise ValueError(f"Unexpected answer: {answer}")

    LOGGER.info(f"Done resolving conflicts for {ankihub_did=}.")
    return True
