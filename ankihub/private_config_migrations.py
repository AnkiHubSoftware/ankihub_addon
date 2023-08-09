from typing import Dict

from . import LOGGER


def migrate_private_config(private_config_dict: Dict):
    """Migrate the private config of the add-on to the new format.
    This function should be updated when the private config of the add-on is changed and
    old config options need to be migrated to the new format.
    """

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
