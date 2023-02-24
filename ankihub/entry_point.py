"""Code to be run on Anki start up."""
import time
from pprint import pformat

import aqt
from anki.errors import CardTypeError
from aqt.gui_hooks import profile_did_open

from . import LOGGER, ankihub_client
from .addons import setup_addons
from .auto_sync_setup import (
    maybe_sync_with_ankihub_on_startup,
    setup_ankihub_sync_on_ankiweb_sync,
)
from .db import ankihub_db
from .errors import setup_error_handler
from .gui import browser, editor
from .gui.anki_db_check import check_anki_db
from .gui.db_check import check_ankihub_db
from .gui.menu import refresh_ankihub_menu, setup_ankihub_menu
from .progress import setup_progress_manager
from .settings import (
    ANKI_VERSION,
    api_url_base,
    config,
    setup_logger,
    setup_profile_data_folder,
)
from .utils import OneTimeLock, modify_note_type_templates

# some code needs to be run only once even if the Anki profile changes
general_setup_lock = OneTimeLock()


def run():
    """Call this function in __init__.py when Anki starts."""

    config.setup_public_config_and_ankihub_app_url()

    ankihub_client.API_URL_BASE = api_url_base()
    LOGGER.info(f"Set AnkiHub API URL base to: {ankihub_client.API_URL_BASE}")

    setup_logger()
    LOGGER.info("Set up logger.")

    profile_did_open.append(on_profile_did_open)


def on_profile_did_open():
    if not profile_setup():
        return

    after_profile_setup()

    if general_setup_lock.aquire():
        general_setup()


def profile_setup() -> bool:
    """Set up profile data folder, config, and AnkiHub DB for the current profile.
    Returns whether the profile setup was successful.
    """
    if not setup_profile_data_folder():
        return False
    LOGGER.info(f"Set up profile data folder for the current profile: {aqt.mw.pm.name}")

    config.setup_private_config()
    LOGGER.info("Set up config for the current profile.")

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

    setup_error_handler()
    LOGGER.info("Set up error handler.")

    LOGGER.info(f"{ANKI_VERSION=}")

    aqt.mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    setup_addons()
    LOGGER.info("Set up addons.")

    setup_ankihub_menu()
    LOGGER.info("Set up AnkiHub menu.")

    editor.setup()
    LOGGER.info("Set up editor.")

    browser.setup()
    LOGGER.info("Set up browser.")

    setup_progress_manager()
    LOGGER.info("Set up progress manager.")

    trigger_addon_update_check()
    LOGGER.info("Triggered add-on update check.")

    from . import media_export  # noqa: F401

    LOGGER.info("Loaded media_export.")

    setup_ankihub_sync_on_ankiweb_sync(on_startup_syncs_done=on_startup_sync_done)
    LOGGER.info("Called setup_ankihub_sync_on_ankiweb_sync.")

    maybe_sync_with_ankihub_on_startup(on_startup_syncs_done=on_startup_sync_done)
    LOGGER.info("Called maybe_sync_with_ankihub_on_startup.")


def on_startup_sync_done():
    check_ankihub_db(on_success=check_anki_db)


def log_enabled_addons():
    enabled_addons = [x for x in aqt.mw.addonManager.all_addon_meta() if x.enabled]
    LOGGER.info(f"enabled addons:\n{pformat(enabled_addons)}")


def trigger_addon_update_check():
    # This sets the last_addon_update_check time to 25 hours before now and Anki usually checks
    # for add-on updates every 24 hours, so this will trigger an add-on update check on Anki startup.
    # See https://github.com/ankitects/anki/blob/21812556a6a29c7da34561e58824219783a867e7/qt/aqt/main.py#L896-L916
    aqt.mw.pm.set_last_addon_update_check(int(time.time()) - (60 * 60 * 25))


def adjust_ankihub_note_type_templates():
    mids = ankihub_db.ankihub_note_type_ids()

    # Filter out note types that don't exist in the Anki database to avoid errors.
    mids_filtered = [mid for mid in mids if aqt.mw.col.models.get(mid)]

    # we don't want the setup to fail if there is a problem with the note type templates
    # the CardTypeError can happen when the template has a problem (for example a missing field)
    try:
        modify_note_type_templates(mids_filtered)
    except CardTypeError:
        LOGGER.exception("Failed to adjust AnkiHub note type templates.")
