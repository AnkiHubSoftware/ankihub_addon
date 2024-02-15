import uuid
from typing import TYPE_CHECKING, Dict, List, Tuple

import aqt
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QEvent,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    qconnect,
)

from . import LOGGER

if TYPE_CHECKING:
    from .settings import BehaviorOnRemoteNoteDeleted  # pragma: no cover


def migrate_private_config(private_config_dict: Dict) -> None:
    """
    Migrate the private config.

    - This function handles changes to the private config format. Update it
      whenever the format of the private config changes.
    - It handles some invalid states. For instance, if an API previously
      skipped certain entries, this function can reset the last update
      timestamps to ensure that all entries are fetched again.

    Note: The migrations that use api_version_on_last_sync rely on the fact
    that the client must be restarted to utilize a new API version. Since
    migrations are executed on client start, this ensures that necessary
    migrations are always applied before the client interacts with an updated
    API.
    """
    _maybe_rename_ankihub_deck_uuid_to_ah_did(private_config_dict)
    _maybe_reset_media_update_timestamps(private_config_dict)
    _maybe_set_suspend_new_cards_of_new_notes_to_true_for_anking_deck(
        private_config_dict
    )
    _remove_orphaned_deck_extensions(private_config_dict)
    _maybe_prompt_user_for_behavior_on_remote_note_deleted(private_config_dict)


def _maybe_reset_media_update_timestamps(private_config_dict: Dict) -> None:
    # This is needed because the api view which returns media updates previously skipped
    # some entries and resetting the timestamps will ensure that all media updates are
    # fetched again.
    # The endpoint was fixed in API version 15.0.
    if _is_api_version_on_last_sync_below_threshold(private_config_dict, 15.0):
        LOGGER.info(
            "Resetting media update timestamps because api version is below 15.0."
        )
        for deck in private_config_dict.get("decks", {}).values():
            deck["latest_media_update"] = None


def _maybe_rename_ankihub_deck_uuid_to_ah_did(private_config_dict: Dict) -> None:
    # Rename the "ankihub_deck_uuid" key to "ah_did" in the deck extensions config.
    old_field_name = "ankihub_deck_uuid"
    new_field_name = "ah_did"
    deck_extension_dict: Dict = private_config_dict["deck_extensions"]
    for deck_extension in deck_extension_dict.values():
        if old_field_name in deck_extension:
            deck_extension[new_field_name] = deck_extension.pop(old_field_name)
            LOGGER.info(
                f"Renamed {old_field_name} to {new_field_name} in deck extension config."
            )


def _maybe_set_suspend_new_cards_of_new_notes_to_true_for_anking_deck(
    private_config_dict: Dict,
) -> None:
    """Set suspend_new_cards_of_new_notes to True in the DeckConfig of the AnKing deck if the field
    doesn't exist yet."""
    from .settings import ANKING_DECK_ID

    field_name = "suspend_new_cards_of_new_notes"
    decks = private_config_dict["decks"]
    for ah_did, deck in decks.items():
        if ah_did == ANKING_DECK_ID and deck.get(field_name) is None:
            deck[field_name] = True
            LOGGER.info(
                f"Set {field_name} to True for the previously installed AnKing deck."
            )


def _is_api_version_on_last_sync_below_threshold(
    private_config_dict: Dict, version_threshold: float
) -> bool:
    """Check if the stored API version is below the given threshold."""
    api_version = private_config_dict.get("api_version_on_last_sync")
    if api_version is None:
        return True

    return api_version < version_threshold


def _remove_orphaned_deck_extensions(private_config_dict: Dict) -> None:
    """Remove deck extension configs for which the corresponding deck isn't in the config anymore."""
    decks = private_config_dict["decks"]
    deck_extensions = private_config_dict["deck_extensions"]
    for deck_extension_id, deck_extension in list(deck_extensions.items()):
        if deck_extension["ah_did"] not in decks:
            del deck_extensions[deck_extension_id]
            LOGGER.info(
                f"Removed deck extension config with {deck_extension_id=} for deck {deck_extension['ah_did']} "
                "because the deck isn't in the config anymore."
            )


def _maybe_prompt_user_for_behavior_on_remote_note_deleted(
    private_config_dict: Dict,
) -> None:
    """Prompt the user to configure the behavior on remote note deleted for each deck if it's not set yet."""
    field_name = "behavior_on_remote_note_deleted"

    decks_dict = private_config_dict["decks"]
    if all(deck.get(field_name) is not None for deck in decks_dict.values()):
        return

    LOGGER.info("Prompting user to configure behavior on remote note deleted.")

    deck_id_and_name_tuples = [
        (uuid.UUID(ah_did_str), deck["name"]) for ah_did_str, deck in decks_dict.items()
    ]
    dialog = ConfigureDeletedNotesDialog(
        parent=aqt.mw, deck_id_and_name_tuples=deck_id_and_name_tuples
    )
    dialog.exec()

    for (
        deck_id,
        behavior_on_remote_note_deleted,
    ) in dialog.deck_id_to_behavior_on_remote_note_deleted_dict().items():
        decks_dict[str(deck_id)][field_name] = behavior_on_remote_note_deleted.value

    LOGGER.info("Configured behavior on remote note deleted.")


class ConfigureDeletedNotesDialog(QDialog):
    """Dialog to configure the behavior when a remote note is deleted for each deck.
    Shows a list of decks and a checkbox for each deck to configure the behavior.
    The dialog can't be closed using the close button in the title bar. It can only be closed by
    clicking the OK button. The reason for this is that we want to ensure that the user
    configures the behavior for each deck before continuing.
    """

    def __init__(
        self, parent, deck_id_and_name_tuples: List[Tuple[uuid.UUID, str]]
    ) -> None:
        super().__init__(parent)

        self._deck_id_and_name_tuples = deck_id_and_name_tuples
        self._setup_ui()

    def deck_id_to_behavior_on_remote_note_deleted_dict(
        self,
    ) -> Dict[uuid.UUID, "BehaviorOnRemoteNoteDeleted"]:
        from .settings import BehaviorOnRemoteNoteDeleted

        result: Dict[uuid.UUID, BehaviorOnRemoteNoteDeleted] = {}
        for i, (deck_id, _) in enumerate(self._deck_id_and_name_tuples, 1):
            checkbox_layout = self.grid_layout.itemAtPosition(i, 1).layout()
            delete_checkbox: QCheckBox = checkbox_layout.itemAt(1).widget()
            result[deck_id] = (
                BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS
                if delete_checkbox.isChecked()
                else BehaviorOnRemoteNoteDeleted.NEVER_DELETE
            )
        return result

    def _setup_ui(self) -> None:
        self.setWindowTitle("Configure deleted notes")

        self.top_label = QLabel(
            "When AnkiHub deletes notes that I have no review history with, they<br>"
            "should also be removed locally from these decks..."
        )

        self.grid_layout = self._setup_grid_layout()

        self.bottom_label = QLabel(
            "You can adjust this setting later in the <b>Deck Management</b> menu."
        )

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(self.button_box.accepted, self.accept)

        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(20, 20, 20, 20)

        self.main_layout.addWidget(self.top_label)
        self.main_layout.addSpacing(10)
        self.main_layout.addLayout(self.grid_layout)
        self.main_layout.addSpacing(10)
        self.main_layout.addWidget(self.bottom_label)
        self.main_layout.addSpacing(25)
        self.main_layout.addWidget(self.button_box)

        self.setLayout(self.main_layout)

        # Fix the size of the dialog to prevent it from being resized.
        self.adjustSize()
        self.setFixedSize(self.size())

    def _setup_grid_layout(self) -> QGridLayout:
        self.grid_layout = QGridLayout()

        # Add headers to the grid layout
        self.deck_name_label = QLabel("<b>Deck name:</b>")
        self.delete_label = QLabel("<b>Delete:</b>")

        self.grid_layout.addWidget(self.deck_name_label, 0, 0)
        self.grid_layout.addWidget(self.delete_label, 0, 1)

        # Setup a row for each deck
        for i, (_, deck_name) in enumerate(self._deck_id_and_name_tuples):
            deck_label = QLabel(deck_name)
            deck_label.setWordWrap(True)

            deck_checkbox = QCheckBox()
            checkbox_layout = QHBoxLayout()
            checkbox_layout.addSpacing(20)
            checkbox_layout.addWidget(deck_checkbox)

            # Add deck label and checkbox to the row
            # Offset by 1 due to header row.
            self.grid_layout.addWidget(deck_label, i + 1, 0)
            self.grid_layout.addLayout(checkbox_layout, i + 1, 1)
            self.grid_layout.setRowMinimumHeight(i + 1, 40)

        self.grid_layout.setColumnStretch(0, 3)
        self.grid_layout.setColumnStretch(1, 1)
        return self.grid_layout

    def closeEvent(self, event: QEvent) -> None:
        # Thsi prevents the dialog from being closed using the close button in the title bar.
        event.ignore()
