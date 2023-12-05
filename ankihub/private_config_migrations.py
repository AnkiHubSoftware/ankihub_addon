from typing import Dict

from . import LOGGER


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


def _remove_orphaned_deck_extensions(private_config_dict: Dict):
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
