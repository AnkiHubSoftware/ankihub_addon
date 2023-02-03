"""Code to be run on Anki start up."""
import time
from pprint import pformat
from typing import Callable

from anki.errors import CardTypeError
from anki.hooks import wrap
from aqt import mw
from aqt.gui_hooks import profile_did_open, profile_will_close

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

PROFILE_WILL_CLOSE = False

# only has a defined value when using the "sync_on_startup" config option
DID_STARTUP_SYNC = False


def run():
    """Call this function in __init__.py when Anki starts."""
    profile_did_open.append(on_profile_did_open)


def on_profile_did_open():
    global PROFILE_WILL_CLOSE
    PROFILE_WILL_CLOSE = False

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
    LOGGER.debug(f"Set up profile data folder for the current profile: {mw.pm.name}")

    config.setup()
    LOGGER.debug("Setup config for the current profile.")

    ankihub_db.setup_and_migrate()
    LOGGER.debug("Set up and migrated AnkiHub DB for the current profile.")

    from .gui.menu import ankihub_menu

    if ankihub_menu:
        refresh_ankihub_menu()
        LOGGER.debug("Refreshed AnkiHub menu.")

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

    LOGGER.debug(f"{ANKI_VERSION=}")

    mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    do_or_setup_ankihub_sync(after_startup_syncs=on_startup_syncs_done)
    LOGGER.debug("Did or set up ankihub sync.")

    setup_ankihub_menu()
    LOGGER.debug("Set up AnkiHub menu.")

    editor.setup()
    LOGGER.debug("Set up editor.")

    browser.setup()
    LOGGER.debug("Set up browser.")

    setup_addons()
    LOGGER.debug("Set up addons.")

    setup_error_handler()
    LOGGER.debug("Set up error handler.")

    setup_progress_manager()
    LOGGER.debug("Set up progress manager.")

    trigger_addon_update_check()
    LOGGER.debug("Triggered add-on update check.")

    from . import media_export  # noqa: F401

    LOGGER.debug("Loaded media_export.")

    profile_will_close.append(on_profile_will_close)
    LOGGER.debug("Set up profile_will_close hook.")


def on_startup_syncs_done() -> None:
    # Called after AnkiWeb sync and AnkiHub sync are done after starting Anki.
    check_ankihub_db(on_success=check_anki_db)


def do_or_setup_ankihub_sync(after_startup_syncs: Callable[[], None]):
    """This will set up the AnkiHub sync to run before the AnkiWeb sync if the user has enabled
    the "sync_on_startup" or "sync_on_ankiweb_sync" config options.
    If Anki won't sync with AnkiWeb, the AnkiHub sync will be run immediately.
    after_startup_syncs is called after the AnkiHub and AnkiWeb syncs are done if they are run,
    or immediately if they aren't run.
    """
    global DID_STARTUP_SYNC

    original_sync_collection_and_media = mw._sync_collection_and_media

    def wrapper(*args, **kwargs) -> None:
        LOGGER.info("Running maybe_do_or_setup_ankihub_sync.wrapper")
        try:
            _wrap_sync_collection_and_media(
                old_after_sync=args[0],
                old=kwargs.pop("_old"),
                after_startup_syncs=after_startup_syncs,
            )
        except Exception as e:
            LOGGER.exception("Error in _wrap_sync_collection_and_media", exc_info=e)

            # call the original sync function if there was an error in the modified one
            original_sync_collection_and_media(*args, **kwargs)

    mw._sync_collection_and_media = wrap(  # type: ignore
        mw._sync_collection_and_media,
        wrapper,
        "around",
    )

    if not mw.can_auto_sync():
        # The AnkiWeb sync won't be run on startup, so we run the AnkiHub sync immediately.
        LOGGER.info("Can't auto sync with AnkiWeb")
        if config.public_config["sync_on_startup"]:
            sync_with_progress(after_startup_syncs)
            DID_STARTUP_SYNC = True
        else:
            after_startup_syncs()


def _wrap_sync_collection_and_media(
    old: Callable[[Callable[[], None]], None],
    old_after_sync: Callable[[], None],
    after_startup_syncs: Callable[[], None],
) -> None:
    global DID_STARTUP_SYNC

    def after_ankihub_sync() -> None:
        global DID_STARTUP_SYNC
        LOGGER.info(
            f"Running maybe_do_or_setup_ankihub_sync.after_ankihub_sync, DID_STARTUP_SYNC={DID_STARTUP_SYNC}"
        )
        if not DID_STARTUP_SYNC:
            old(after_startup_syncs)
            DID_STARTUP_SYNC = True
        else:
            old(old_after_sync)

    LOGGER.info("Runnig _wrap_sync_collection_and_media")

    # If the profile is being closed, don't sync with AnkiHub to avoid errors / delays.
    if not PROFILE_WILL_CLOSE and (
        (config.public_config["sync_on_startup"] and not DID_STARTUP_SYNC)
        or config.public_config["sync_on_ankiweb_sync"]
    ):
        sync_with_progress(on_done=after_ankihub_sync)
    else:
        old(old_after_sync)


def log_enabled_addons():
    enabled_addons = [x for x in mw.addonManager.all_addon_meta() if x.enabled]
    LOGGER.debug(f"enabled addons:\n{pformat(enabled_addons)}")


def trigger_addon_update_check():
    # This sets the last_addon_update_check time to 25 hours before now and Anki usually checks
    # for add-on updates every 24 hours, so this will trigger an add-on update check on Anki startup.
    # See https://github.com/ankitects/anki/blob/21812556a6a29c7da34561e58824219783a867e7/qt/aqt/main.py#L896-L916
    mw.pm.set_last_addon_update_check(int(time.time()) - (60 * 60 * 25))


def adjust_ankihub_note_type_templates():
    mids = ankihub_db.ankihub_note_type_ids()

    # Filter out note types that don't exist in the Anki database to avoid errors.
    mids_filtered = [mid for mid in mids if mw.col.models.get(mid)]

    # we don't want the setup to fail if there is a problem with the note type templates
    # the CardTypeError can happen when the template has a problem (for example a missing field)
    try:
        modify_note_type_templates(mids_filtered)
    except CardTypeError:
        LOGGER.exception("Failed to adjust AnkiHub note type templates.")


def on_profile_will_close():
    global PROFILE_WILL_CLOSE
    PROFILE_WILL_CLOSE = True
