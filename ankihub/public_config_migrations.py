import aqt


def migrate_public_config() -> None:
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
