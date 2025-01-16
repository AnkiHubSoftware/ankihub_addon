import aqt


def migrate_public_config() -> None:
    """Migrate the public config of the add-on to the new format.
    This function should be updated when the public config of the add-on is changed and
    old config options need to be migrated to the new format.
    """
    addon_config = aqt.mw.addonManager.getConfig(__name__)

    # Migrate the "sync_on_startup" config option to the "auto_sync" config option.
    if (sync_on_startup := addon_config.get("sync_on_startup")) is not None:
        if sync_on_startup:
            # Users who had the "sync_on_startup" config option set to True probably
            # dont mind syncing on every AnkiWeb sync.
            addon_config["auto_sync"] = "on_ankiweb_sync"
        else:
            # Users who had the "sync_on_startup" config option set to False could be
            # unhappy if the add-on syncs with AnkiHub after the add-on update that introduced
            # the "auto_sync" config option.
            addon_config["auto_sync"] = "never"
        addon_config.pop("sync_on_startup")
        aqt.mw.addonManager.writeConfig(__name__, addon_config)

    # Remove the "ankihub_url" config option to remove unnecessary clutter.
    if "ankihub_url" in addon_config:
        addon_config.pop("ankihub_url")
        aqt.mw.addonManager.writeConfig(__name__, addon_config)

    if "boards_and_beyond" in addon_config:
        addon_config.pop("boards_and_beyond")
        aqt.mw.addonManager.writeConfig(__name__, addon_config)

    if "first_aid_forward" in addon_config:
        addon_config.pop("first_aid_forward")
        aqt.mw.addonManager.writeConfig(__name__, addon_config)
