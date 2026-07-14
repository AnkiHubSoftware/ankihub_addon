"""Shows the Intercom Messenger launcher natively on Anki's home screens.

Injects into the deck browser (home) and deck overview (deck details) via the
``webview_will_set_content`` hook so the launcher bubble floats over those pages.

Identity verification relies on a ``user_hash`` that must be computed server-side
(the Intercom secret key must never ship in the addon). The addon reads the
non-secret ``intercom_app_id`` and the server-computed ``intercom_user_hash`` from
the cached ``/users/me`` user details. When the hash is absent the Messenger is
still booted without identity verification.

The onboarding tour host uses a z-index above Intercom (~2147483001), so the
launcher stays visible but behind the tour backdrop.
"""

import json
from typing import Any, Dict

import aqt
from aqt.webview import WebContent

from .. import LOGGER
from ..settings import config

# Standard Intercom loader snippet (see https://developers.intercom.com/installing-intercom/web/installation).
# Placeholders are substituted at runtime to avoid str.format/f-string brace escaping.
# The loader detects an already-booted instance and calls reattach_activator/update
# instead of loading a second time, so it is safe to inject on every re-render.
_INTERCOM_LOADER_JS = """
window.intercomSettings = __SETTINGS__;
(function(){
  var w=window;var ic=w.Intercom;
  if(typeof ic==="function"){
    ic('reattach_activator');ic('update',w.intercomSettings);
  }else{
    var d=document;var i=function(){i.c(arguments);};i.q=[];
    i.c=function(args){i.q.push(args);};w.Intercom=i;
    var l=function(){
      var s=d.createElement('script');s.type='text/javascript';s.async=true;
      s.src='https://widget.intercom.io/widget/__APP_ID__';
      var x=d.getElementsByTagName('script')[0];x.parentNode.insertBefore(s,x);
    };
    if(document.readyState==='complete'){l();}
    else if(w.attachEvent){w.attachEvent('onload',l);}
    else{w.addEventListener('load',l,false);}
  }
})();
"""


def setup() -> None:
    aqt.gui_hooks.webview_will_set_content.append(_inject_intercom)


def shutdown() -> None:
    """Shut down the Intercom session so the next user starts clean (called on logout)."""
    if aqt.mw and aqt.mw.web:
        aqt.mw.web.eval("if (window.Intercom) { window.Intercom('shutdown'); }")


def close_messenger() -> None:
    """Collapse an open Intercom Messenger panel back to the launcher.

    Safe to call even if Intercom is not loaded or the panel is already closed.
    """
    if not (aqt.mw and aqt.mw.web):
        return
    aqt.mw.web.eval("if (window.Intercom) { window.Intercom('hide'); }")


def is_enabled_for_user() -> bool:
    """Whether Intercom should run for the current user (flag + preference)."""
    if not config.is_logged_in():
        return False
    if not config.get_feature_flags().get("intercom_desktop_enabled", False):
        return False
    return bool(config.public_config.get("ankihub_support_button", True))


def sync_with_user_preference() -> None:
    """Apply the Support button preference after Config is closed.

    When disabled, shut Intercom down immediately. When enabled, reset the main
    window so the deck browser / overview re-injects the snippet.
    """
    if not is_enabled_for_user():
        shutdown()
        return
    if aqt.mw and aqt.mw.state in ("deckBrowser", "overview"):
        aqt.mw.reset()


def _inject_intercom(web_content: WebContent, context: object) -> None:
    from aqt.deckbrowser import DeckBrowser
    from aqt.overview import Overview

    if not isinstance(context, (DeckBrowser, Overview)):
        return

    if not is_enabled_for_user():
        return

    user_details = config.get_user_details() or {}
    app_id = user_details.get("intercom_app_id") or config.intercom_app_id
    if not app_id:
        return

    intercom_settings: Dict[str, Any] = {
        "api_base": "https://api-iam.intercom.io",
        "app_id": app_id,
    }
    if (user_id := user_details.get("id")) is not None:
        intercom_settings["user_id"] = str(user_id)
    if name := user_details.get("name"):
        intercom_settings["name"] = name
    if email := user_details.get("email"):
        intercom_settings["email"] = email
    # Server-computed HMAC that enables Intercom identity verification.
    if user_hash := user_details.get("intercom_user_hash"):
        intercom_settings["user_hash"] = user_hash

    boot_js = _INTERCOM_LOADER_JS.replace("__SETTINGS__", json.dumps(intercom_settings)).replace("__APP_ID__", app_id)
    web_content.body += f"<script>{boot_js}</script>"
    LOGGER.info(
        "Injected Intercom Messenger.",
        context=type(context).__name__,
        identity_verified=bool(user_hash),
    )
