"""Code to be run on Anki start up."""
import time
from pprint import pformat

import aqt
from anki.errors import CardTypeError
from aqt.gui_hooks import profile_did_open, profile_will_close

from . import LOGGER
from .db import ankihub_db
from .debug import setup as setup_debug
from .feature_flags import setup_feature_flags
from .gui import browser, deckbrowser, editor, progress, reviewer, taskman
from .gui.addons import setup_addons
from .gui.auto_sync import setup_auto_sync
from .gui.config_dialog import setup_config_dialog_manager
from .gui.errors import setup_error_handler
from .gui.media_sync import media_sync
from .gui.menu import menu_state, refresh_ankihub_menu, setup_ankihub_menu
from .main.note_deletion import handle_notes_deleted_from_webapp
from .main.utils import modify_note_type_templates
from .settings import (
    ADDON_VERSION,
    ANKI_VERSION,
    ankihub_db_path,
    config,
    setup_logger,
    setup_profile_data_folder,
)

# The general setup should be only once, because it sets up menu items, hooks, etc.
# We don't want to set them up multiple times when the profile is opened multiple times,
# because that would cause multiple menu items, hooks, etc.
ATTEMPTED_GENERAL_SETUP = False


def run():
    """Call this function in __init__.py when Anki starts."""

    config.setup_public_config_and_urls()

    setup_logger()
    LOGGER.info("Set up logger.")

    LOGGER.info(f"{ADDON_VERSION=}")
    LOGGER.info(f"{ANKI_VERSION=}")
    LOGGER.info(f"AnkiHub app url: {config.app_url}")
    LOGGER.info(f"S3 bucket url: {config.s3_bucket_url}")

    profile_did_open.append(_on_profile_did_open)
    profile_will_close.append(_on_profile_will_close)


def _on_profile_did_open():
    if not _profile_setup():
        return

    _after_profile_setup()

    global ATTEMPTED_GENERAL_SETUP
    if not ATTEMPTED_GENERAL_SETUP:
        ATTEMPTED_GENERAL_SETUP = True
        _general_setup()

    media_sync.allow_background_threads()


def _on_profile_will_close():
    media_sync.stop_background_threads()


def _profile_setup() -> bool:
    """Set up profile data folder, config, and AnkiHub DB for the current profile.
    Returns whether the profile setup was successful.
    """
    if not setup_profile_data_folder():
        return False
    LOGGER.info(f"Set up profile data folder for the current profile: {aqt.mw.pm.name}")

    config.setup_private_config()
    LOGGER.info("Set up config for the current profile.")

    ankihub_db.setup_and_migrate(ankihub_db_path())
    LOGGER.info("Set up and migrated AnkiHub DB for the current profile.")

    if menu_state.ankihub_menu:
        refresh_ankihub_menu()
        LOGGER.info("Refreshed AnkiHub menu.")

    return True


def _after_profile_setup():
    _log_enabled_addons()

    # This deletes broken notetypes with no fields or templates created by a previous version of the add-on.
    _delete_broken_note_types()

    # This adjusts note type templates of note types used by AnkiHub notes when the profile is opened.
    # If this wouldn't be called here the templates would only be adjusted when syncing with AnkiHub.
    # We want the modifications to be present even if the user doesn't sync with AnkiHub, so we call
    # this here.
    _adjust_ankihub_note_type_templates()

    # This deletes notes that were deleted from the web app. This is not a general solution,
    # just a temporary fix for notes that were already manually deleted on the webapp.
    # Later we should handle note deletion in the sync process.
    handle_notes_deleted_from_webapp()


def _general_setup():
    """Set up things that don't depend on the profile and should only be run once, even if the
    profile changes."""

    setup_error_handler()
    LOGGER.info("Set up error handler.")

    setup_feature_flags()
    LOGGER.info("Set up feature flags.")

    aqt.mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    setup_debug()
    LOGGER.info("Set up debug.")

    setup_addons()
    LOGGER.info("Set up addons.")

    setup_config_dialog_manager()
    LOGGER.info("Set up config.")

    setup_ankihub_menu()
    LOGGER.info("Set up AnkiHub menu.")

    editor.setup()
    LOGGER.info("Set up editor.")

    browser.setup()
    LOGGER.info("Set up browser.")

    reviewer.setup()
    LOGGER.info("Set up reviewer.")

    progress.setup()
    LOGGER.info("Set up progress manager.")

    taskman.setup()
    LOGGER.info("Set up task manager.")

    deckbrowser.setup()
    LOGGER.info("Set up deck browser")

    _trigger_addon_update_check()
    LOGGER.info("Triggered add-on update check.")

    from . import media_export  # noqa: F401

    LOGGER.info("Loaded media_export.")

    setup_auto_sync()
    LOGGER.info("Set up auto sync.")


def _log_enabled_addons():
    enabled_addons = [x for x in aqt.mw.addonManager.all_addon_meta() if x.enabled]
    LOGGER.info(f"enabled addons:\n{pformat(enabled_addons)}")


def _trigger_addon_update_check():
    # This sets the last_addon_update_check time to 25 hours before now and Anki usually checks
    # for add-on updates every 24 hours, so this will trigger an add-on update check on Anki startup.
    # See https://github.com/ankitects/anki/blob/21812556a6a29c7da34561e58824219783a867e7/qt/aqt/main.py#L896-L916
    aqt.mw.pm.set_last_addon_update_check(int(time.time()) - (60 * 60 * 25))


def _adjust_ankihub_note_type_templates():
    mids = ankihub_db.ankihub_note_type_ids()

    # Filter out note types that don't exist in the Anki database to avoid errors.
    mids_filtered = [mid for mid in mids if aqt.mw.col.models.get(mid)]

    # we don't want the setup to fail if there is a problem with the note type templates
    # the CardTypeError can happen when the template has a problem (for example a missing field)
    try:
        modify_note_type_templates(mids_filtered)
    except CardTypeError:  # noqa: E722
        LOGGER.exception("Failed to adjust AnkiHub note type templates.")


def _delete_broken_note_types() -> None:
    aqt.mw.col.db.execute(
        "DELETE FROM notetypes as nt WHERE\
 NOT EXISTS(SELECT 1 FROM templates where ntid = nt.id) OR\
 NOT EXISTS(SELECT 1 FROM fields where ntid = nt.id)"
    )
    aqt.mw.col.save()
