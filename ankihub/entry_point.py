"""Code to be run on Anki start up."""

import re
import time
from pathlib import Path

import aqt
from anki.errors import CardTypeError
from anki.hooks import wrap
from aqt.gui_hooks import profile_did_open, profile_will_close
from aqt.main import AnkiQt

from . import LOGGER, anki_logger
from .db import ankihub_db
from .feature_flags import update_feature_flags_in_background
from .gui import (
    browser,
    deckbrowser,
    editor,
    js_message_handling,
    overview,
    progress,
    reviewer,
)
from .gui.addons import setup_addons
from .gui.auto_sync import setup_auto_sync
from .gui.config_dialog import setup_config_dialog_manager
from .gui.errors import setup_error_handler
from .gui.media_sync import media_sync
from .gui.menu import menu_state, refresh_ankihub_menu, setup_ankihub_menu
from .gui.operations.ankihub_sync import setup_full_sync_patch
from .gui.operations.username import fetch_username_in_background
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

CALL_ON_PROFILE_DID_OPEN_ON_MAYBE_AUTO_SYNC = bool(re.match(r"24\.06\.", ANKI_VERSION))

# The general setup should be only once, because it sets up menu items, hooks, etc.
# We don't want to set them up multiple times when the profile is opened multiple times,
# because that would cause multiple menu items, hooks, etc.
ATTEMPTED_GENERAL_SETUP = False

WEB_MEDIA_PATH = Path(__file__).parent / "gui/web/media"


def run():
    """Call this function in __init__.py when Anki starts."""

    config.setup_public_config_and_other_settings()

    setup_logger()
    LOGGER.info("Set up logger.")

    LOGGER.info(
        "Application and version info",
        addon_version=ADDON_VERSION,
        anki_version=ANKI_VERSION,
        qt_version=aqt.QT_VERSION_STR,
        app_url=config.app_url,
        s3_bucket_url=config.s3_bucket_url,
    )

    _setup_on_profile_did_open()
    profile_will_close.append(_on_profile_will_close)

    anki_logger.setup()


def _setup_on_profile_did_open() -> None:
    """Makes sure that _on_profile_did_open gets called after the profile is loaded and before
    maybe_auto_sync_on_open_close is called."""

    if not CALL_ON_PROFILE_DID_OPEN_ON_MAYBE_AUTO_SYNC:
        profile_did_open.append(_on_profile_did_open)
        return

    profile_is_opening = True

    # Starting from Anki 24.06 AnkiQt.maybe_auto_sync_on_open_close is called before
    # the profile_did_open hook. (Both are called in AnkiQt.loadProfile)
    # We need to call _on_profile_did_open before maybe_auto_sync_on_open_close, so we do this.
    def maybe_call_on_profile_did_open(*args, **kwargs) -> None:
        LOGGER.info("maybe_auto_sync_on_open_close called.")

        nonlocal profile_is_opening
        if profile_is_opening:
            LOGGER.info("Calling _on_profile_did_open.")
            try:
                _on_profile_did_open()
            except Exception as e:  # pragma: no cover
                # Raise the exception without disrupting the calling code.
                exception = e

                def raise_exception() -> None:
                    raise exception

                aqt.mw.taskman.run_in_background(
                    raise_exception, on_done=lambda future: future.result()
                )

        profile_is_opening = not profile_is_opening

    AnkiQt.maybe_auto_sync_on_open_close = wrap(  # type: ignore
        old=AnkiQt.maybe_auto_sync_on_open_close,
        new=maybe_call_on_profile_did_open,
        pos="before",
    )


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
    LOGGER.info("Profile will close, stopping background threads.")


def _profile_setup() -> bool:
    """Set up profile data folder, config, and AnkiHub DB for the current profile.
    Returns whether the profile setup was successful.
    """
    if not setup_profile_data_folder():
        return False
    LOGGER.info(
        "Set up profile data folder for the current profile.", profile=aqt.mw.pm.name
    )

    config.setup_private_config()
    LOGGER.info("Set up config for the current profile.")

    ankihub_db.setup_and_migrate(ankihub_db_path())
    LOGGER.info("Set up and migrated AnkiHub DB for the current profile.")

    if menu_state.ankihub_menu:
        refresh_ankihub_menu()
        LOGGER.info("Refreshed AnkiHub menu.")

    _copy_web_media_to_media_folder()
    LOGGER.info("Copied web media to media folder.")

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

    aqt.mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    setup_addons()
    LOGGER.info("Set up addons.")

    js_message_handling.setup()
    LOGGER.info("Set up JavaScript message handling.")

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

    deckbrowser.setup()
    LOGGER.info("Set up deck browser")

    overview.setup()
    LOGGER.info("Set up deck overview")

    _trigger_addon_update_check()
    LOGGER.info("Triggered add-on update check.")

    from . import media_export  # noqa: F401

    LOGGER.info("Loaded media_export.")

    setup_auto_sync()
    LOGGER.info("Set up auto sync.")

    setup_full_sync_patch()
    LOGGER.info("Set up AnkiWeb full sync patch.")

    # Call setup_feature_flags_in_background last among the setup functions.
    # This is because other setup functions can add callbacks which react to the feature flags getting fetched.
    # If this function is called earlier, the feature flags might be fetched before the callbacks are added,
    # which would cause the callbacks to not be called.
    update_feature_flags_in_background()
    fetch_username_in_background()

    config.token_change_hook.append(update_feature_flags_in_background)
    config.token_change_hook.append(fetch_username_in_background)

    LOGGER.info(
        "Set up feature flag fetching (flags will be fetched in the background)."
    )


def _copy_web_media_to_media_folder():
    """Copy media files from the web folder to the media folder. Existing files with the same name
    will be overwritten.
    The media file names should start with '_' so that Anki doesn't remove them when checking for unused media.
    """
    for file in WEB_MEDIA_PATH.glob("*"):
        file_name = file.name
        file_path = Path(aqt.mw.col.media.dir()) / file_name
        file_path.write_bytes(file.read_bytes())


def _log_enabled_addons():
    enabled_addons = [
        {"dir_name": x.dir_name, "human_version": x.human_version}
        for x in aqt.mw.addonManager.all_addon_meta()
        if x.enabled
    ]
    LOGGER.info("Enabled addons", enabled_addons=enabled_addons)


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
