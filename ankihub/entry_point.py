"""Code to be run on Anki start up."""
import time
from pprint import pformat
from typing import Callable

from aqt import mw
from aqt.gui_hooks import profile_did_open, sync_did_finish

from . import LOGGER
from .addons import setup_addons
from .db import ankihub_db
from .errors import setup_error_handler
from .gui import browser, editor
from .gui.anki_db_check import check_anki_db
from .gui.db_check import check_ankihub_db
from .gui.menu import refresh_ankihub_menu, setup_ankihub_menu
from .progress import setup_progress_manager
from .settings import ANKI_VERSION, config, setup_profile_data_folder
from .sync import sync_with_progress
from .utils import modify_note_type_templates

# some code needs to be run only once even if the Anki profile changes
ATTEMPTED_GENERAL_SETUP_BEFORE = False


def run():
    """Call this function in __init__.py when Anki starts."""
    profile_did_open.append(on_profile_did_open)


def on_profile_did_open():
    if not profile_setup():
        return

    after_profile_setup()

    global ATTEMPTED_GENERAL_SETUP_BEFORE
    if ATTEMPTED_GENERAL_SETUP_BEFORE:
        return

    # The variable is set to True here and not at the end of the function because
    # the non_profile_setup should not be run if it has failed before.
    # If it were to run again, there could be duplicated menus and other unwanted side effects.
    ATTEMPTED_GENERAL_SETUP_BEFORE = True

    general_setup()


def profile_setup() -> bool:
    """Set up profile data folder, config, and AnkiHub DB for the current profile.
    Returns whether the profile setup was successful.
    """
    if not setup_profile_data_folder():
        return False
    LOGGER.info(f"Set up profile data folder for the current profile: {mw.pm.name}")

    config.setup()
    LOGGER.info("Setup config for the current profile.")

    ankihub_db.setup_and_migrate()
    LOGGER.info("Set up and migrated AnkiHub DB for the current profile.")

    from .gui.menu import ankihub_menu

    if ankihub_menu:
        refresh_ankihub_menu()
        LOGGER.info("Refreshed AnkiHub menu.")

    return True


def after_profile_setup():
    log_enabled_addons()

    # This adjusts note type templates of note types used by AnkiHub notes when the profile is opened.
    # If this wouldn't be called here the templates would only be adjusted when syncing with AnkiHub.
    # We want the modifications to be present even if the user doesn't sync with AnkiHub, so we call
    # this here.
    adjust_ankihub_note_type_templates()


def general_setup():
    """Set up things that don't depend on the profile and should only be run once, even if the
    profile changes."""

    LOGGER.info(f"{ANKI_VERSION=}")

    mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    do_after_ankiweb_sync(callback=on_startup_ankiweb_sync_done)
    LOGGER.info("Registered on_after_ankiweb_sync")

    setup_ankihub_menu()
    LOGGER.info("Set up AnkiHub menu.")

    editor.setup()
    LOGGER.info("Set up editor.")

    browser.setup()
    LOGGER.info("Set up browser.")

    setup_addons()
    LOGGER.info("Set up addons.")

    setup_error_handler()
    LOGGER.info("Set up error handler.")

    setup_progress_manager()
    LOGGER.info("Set up progress manager.")

    trigger_addon_update_check()
    LOGGER.info("Triggered add-on update check.")

    from . import media_export  # noqa: F401

    LOGGER.info("Loaded media_export.")


def on_startup_syncs_done() -> None:
    # Called after AnkiWeb sync and AnkiHub sync are done after starting Anki.
    check_ankihub_db(on_success=check_anki_db)


def on_startup_ankiweb_sync_done() -> None:
    # Syncing with AnkiHub during sync with AnkiWeb causes an error,
    # this is why we have to wait until the AnkiWeb sync is done if there is one.
    # The database check should be done after the AnkiHub sync to not open all dialogs at once
    # and the AnkiHub sync could also make the database check obsolete.
    if config.public_config.get("sync_on_startup", True):
        sync_with_progress(on_done=on_startup_syncs_done)
    else:
        on_startup_syncs_done()


def do_after_ankiweb_sync(callback: Callable[[], None]) -> None:
    """The callback is called after the AnkiWeb sync is done if there is one, otherwise it is called
    when the Anki profile is opened.
    """

    def on_profile_open():
        if not mw.can_auto_sync():
            LOGGER.info(
                "do_after_ankiweb_sync: Calling callback right away as mw.can_auto_sync() is False"
            )
            callback()
        else:

            def on_sync_did_finish():
                sync_did_finish.remove(on_sync_did_finish)

                LOGGER.info(
                    "do_after_ankiweb_sync: Calling callback after AnkiWeb sync"
                )
                callback()

            sync_did_finish.append(on_sync_did_finish)

    profile_did_open.append(on_profile_open)


def log_enabled_addons():
    enabled_addons = [x for x in mw.addonManager.all_addon_meta() if x.enabled]
    LOGGER.info(f"enabled addons:\n{pformat(enabled_addons)}")


def trigger_addon_update_check():
    # This sets the last_addon_update_check time to 25 hours before now and Anki usually checks
    # for add-on updates every 24 hours, so this will trigger an add-on update check on Anki startup.
    # See https://github.com/ankitects/anki/blob/21812556a6a29c7da34561e58824219783a867e7/qt/aqt/main.py#L896-L916
    mw.pm.set_last_addon_update_check(int(time.time()) - (60 * 60 * 25))


def adjust_ankihub_note_type_templates():
    mids = ankihub_db.ankihub_note_type_ids()

    # Filter out note types that don't exist in the Anki database to avoid errors.
    mids_filtered = [mid for mid in mids if mw.col.models.get(mid)]

    modify_note_type_templates(mids_filtered)
