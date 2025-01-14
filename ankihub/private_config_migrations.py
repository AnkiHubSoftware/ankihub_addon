from typing import Dict

import aqt

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
    _maybe_prompt_user_for_behavior_on_remote_note_deleted(private_config_dict)
    _move_credentials_to_profile_config(private_config_dict)


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
    deck_extension_dict: Dict = private_config_dict.get("deck_extensions", {})
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
    from .settings import config

    field_name = "suspend_new_cards_of_new_notes"
    decks = private_config_dict["decks"]
    for ah_did, deck in decks.items():
        if ah_did == config.anking_deck_id and deck.get(field_name) is None:
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
    deck_extensions: Dict = private_config_dict.get("deck_extensions", {})
    for deck_extension_id, deck_extension in list(deck_extensions.items()):
        if deck_extension["ah_did"] not in decks:
            del deck_extensions[deck_extension_id]
            LOGGER.info(
                "Removed deck extension config because the deck isn't in the config anymore.",
                ah_did=deck_extension["ah_did"],
                deck_extension_id=deck_extension_id,
            )


def _maybe_prompt_user_for_behavior_on_remote_note_deleted(
    private_config_dict: Dict,
) -> None:
    """Prompt the user to configure the behavior on remote note deleted for each deck if it's not set yet."""

    from .settings import BehaviorOnRemoteNoteDeleted

    field_name = "behavior_on_remote_note_deleted"

    decks_dict = private_config_dict["decks"]
    if all(deck.get(field_name) is not None for deck in decks_dict.values()):
        return

    for ah_did_str in decks_dict.keys():
        decks_dict[ah_did_str][
            field_name
        ] = BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS.value

    LOGGER.info(
        f"Set {field_name} to {BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS.value} for all decks."
    )


def _move_credentials_to_profile_config(
    private_config_dict: Dict,
) -> None:
    """
    Move login credentials to Anki's profile config.
    Also move the ankiHubToken/ankiHubUsername profile config keys to thirdPartyAnkiHubToken/thirdPartyAnkiHubUsername
    as the keys used by Anki have changed.
    """

    token = private_config_dict.pop("token", None) or aqt.mw.pm.profile.pop(
        "ankiHubToken", None
    )
    username = private_config_dict.pop("user", None) or aqt.mw.pm.profile.pop(
        "ankiHubUsername", None
    )
    if token:
        # aqt.mw.pm.set_ankihub_token(token)
        aqt.mw.pm.profile["thirdPartyAnkiHubToken"] = token
    if username:
        # aqt.mw.pm.set_ankihub_username(username)
        aqt.mw.pm.profile["thirdPartyAnkiHubUsername"] = username
