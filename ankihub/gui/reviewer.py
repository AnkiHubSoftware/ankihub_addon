"""Modifies Anki's reviewer UI (aqt.reviewer)."""

import json
import uuid
from enum import Enum
from textwrap import dedent
from typing import Any, Callable, List, Optional, Set, Tuple

import aqt
import aqt.webview
from anki.cards import Card
from anki.notes import Note
from aqt import QTimer, colors, qconnect
from aqt.gui_hooks import (
    reviewer_did_show_answer,
    reviewer_did_show_question,
    reviewer_will_end,
    webview_did_receive_js_message,
    webview_will_set_content,
)
from aqt.reviewer import Reviewer
from aqt.theme import theme_manager
from aqt.utils import openLink
from aqt.webview import WebContent

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..db import ankihub_db
from ..gui.menu import AnkiHubLogin
from ..gui.webview import AuthenticationRequestInterceptor, CustomWebPage  # noqa: F401
from ..main.utils import Resource, mh_tag_to_resource
from ..settings import config, url_login
from .config_dialog import get_config_dialog_manager
from .js_message_handling import VIEW_NOTE_PYCMD, parse_js_message_kwargs
from .operations import AddonQueryOp
from .utils import get_ah_did_of_deck_or_ancestor_deck, using_qt5
from .web.templates import (
    get_empty_state_html,
    get_header_webview_html,
    get_remove_anking_button_js,
    get_reviewer_buttons_js,
)

VIEW_NOTE_BUTTON_ID = "ankihub-view-note-button"

INVALID_AUTH_TOKEN_PYCMD = "ankihub_invalid_auth_token"
REVIEWER_BUTTON_TOGGLED_PYCMD = "ankihub_reviewer_button_toggled"
CLOSE_SIDEBAR_PYCMD = "ankihub_close_sidebar"
LOAD_URL_IN_SIDEBAR_PYCMD = "ankihub_load_url_in_sidebar"
OPEN_SIDEBAR_CONTENT_IN_BROWSER_PYCMD = "ankihub_open_sidebar_content_in_browser"


class SidebarPageType(Enum):
    BOARDS_AND_BEYOND = "b&b"
    FIRST_AID = "fa4"
    CHATBOT = "chatbot"


PAGE_TYPE_TO_DISPLAY_NAME = {
    SidebarPageType.BOARDS_AND_BEYOND: "Boards & Beyond Viewer",
    SidebarPageType.FIRST_AID: "First Aid Viewer",
    SidebarPageType.CHATBOT: "Welcome to AnkiHub AI!",
}


class ResourceType(Enum):
    BOARDS_AND_BEYOND = "b&b"
    FIRST_AID = "fa4"


RESOURCE_TYPE_TO_TAG_PART = {
    ResourceType.BOARDS_AND_BEYOND: "#b&b",
    ResourceType.FIRST_AID: "#firstaid",
}


class ReviewerSidebar:
    def __init__(self, reviewer: Reviewer):
        self.reviewer = reviewer
        self.splitter: Optional[aqt.QSplitter] = None
        self.container: Optional[aqt.QWidget] = None
        self.content_webview: Optional[aqt.webview.AnkiWebView] = None
        self.header_webview: Optional[aqt.webview.AnkiWebView] = None
        self.resources: List[Resource] = None
        self.page_type: Optional[SidebarPageType] = None
        self.original_mw_min_width = aqt.mw.minimumWidth()
        self.on_auth_failure_hook: Callable = None
        self.last_accessed_url: Optional[str] = None
        self.needs_to_accept_terms = False

        self._setup_ui()

    def _setup_ui(self):
        parent_widget = self.reviewer.mw

        if parent_widget is None:
            raise ValueError(
                "Reviewer does not have a parent widget to hold the splitter."
            )

        self.splitter = aqt.QSplitter()
        self.container = aqt.QWidget()
        container_layout = aqt.QVBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        self.container.setLayout(container_layout)

        self.header_webview = aqt.webview.AnkiWebView()
        self.header_webview.set_bridge_command(
            self.header_webview.defaultOnBridgeCmd, context=self
        )
        self.header_webview.setSizePolicy(
            aqt.QSizePolicy(
                aqt.QSizePolicy.Policy.Expanding, aqt.QSizePolicy.Policy.Fixed
            )
        )

        # Create a QWebEngineProfile with persistent storage
        self.profile = aqt.QWebEngineProfile("AnkiHubProfile", parent_widget)
        self.profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"
        )

        self.content_webview = aqt.webview.AnkiWebView()
        self.content_webview.set_bridge_command(
            self.content_webview.defaultOnBridgeCmd, context=self
        )
        # Set the content_webview as the webview which will receive messages from js message handlers
        self.web = self.content_webview

        self.content_webview.setMinimumWidth(self.original_mw_min_width)

        self.update_header_button_timer = QTimer(self.content_webview)
        qconnect(
            self.update_header_button_timer.timeout, self._update_header_button_state
        )
        self.update_header_button_timer.start(200)

        self.interceptor = AuthenticationRequestInterceptor(self.content_webview)

        page = CustomWebPage(
            self.content_webview, self.profile, self.content_webview._onBridgeCmd
        )
        page.profile().setUrlRequestInterceptor(self.interceptor)
        # Prevent white flicker on dark mode
        page.setBackgroundColor(theme_manager.qcolor(colors.CANVAS))
        aqt.qconnect(page.loadFinished, self._on_content_page_loaded)
        self.content_webview.setPage(page)
        # This ensures pycmd() is defined early
        page.setHtml("")

        container_layout.addWidget(self.header_webview)
        container_layout.addWidget(self.content_webview)
        self.header_webview.adjustSize()
        self.container.hide()

        layout = parent_widget.layout()
        if layout is None:
            layout = aqt.QVBoxLayout(parent_widget)
            parent_widget.setLayout(layout)

        layout.addWidget(self.splitter)
        self.splitter.setSizePolicy(
            aqt.QSizePolicy(
                aqt.QSizePolicy.Policy.Expanding, aqt.QSizePolicy.Policy.Expanding
            )
        )

        widget = self.reviewer.web
        # For compatibility with other add-ons that add a sidebar too (e.g. AMBOSS)
        if isinstance(self.reviewer.web.parentWidget(), aqt.QSplitter):
            widget = self.reviewer.web.parentWidget()
        widget_index = parent_widget.mainLayout.indexOf(widget)
        parent_widget.mainLayout.removeWidget(widget)
        self.splitter.addWidget(widget)
        self.splitter.addWidget(self.container)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([10000, 10000])

        parent_widget.mainLayout.insertWidget(widget_index, self.splitter)

    def show_resource_tabs(
        self, page_type: SidebarPageType, resources: List[Resource]
    ) -> None:
        self.page_type = page_type
        self.resources = resources

        if not self.resources:
            self.set_content_url(None)
        else:
            self.set_content_url(self.resources[0].url)

        self._update_header_webview()

    def show_chatbot(self, ah_nid: Optional[uuid.UUID]) -> None:
        self.page_type = SidebarPageType.CHATBOT
        self.resources = []

        # The web app handles the case when ah_nid is None and shows the "note not found" screen.
        url = f"{config.app_url}/ai/chatbot/{ah_nid}/?is_on_anki=true"
        self.set_content_url(url)

        self._update_header_webview()

    def _update_header_button_state(self):
        if not self.resources:
            return

        # We only want to enable the "Open in Browser" button if the content is not hosted on AnkiHub.
        enable_open_in_browser_button = not self.get_content_url().startswith(
            config.app_url
        )
        self.header_webview.eval(
            f"setOpenInBrowserButtonState({'true' if enable_open_in_browser_button else 'false'});"
        )

    def _update_header_webview(self):
        html = get_header_webview_html(
            self.resources,
            self.content_webview.url().toString(),
            f"{PAGE_TYPE_TO_DISPLAY_NAME[self.page_type]}",
            _ankihub_theme(),
        )

        # The height of the header depends on whether there is an active tab or not.
        # Using adjustHeight wouldn't work here, because it can only make the height bigger, not smaller.
        if not self.resources:
            self.header_webview.setFixedHeight(44)
        else:
            self.header_webview.setFixedHeight(88)

        self.header_webview.setHtml(html)

    def open_sidebar(self) -> bool:
        """Opens the sidebar if it's not already open.
        Handles authentication failures by displaying the login screen.
        Returns True if the sidebar is open or was opened successfully, False otherwise.
        """
        if not config.token():
            self._handle_auth_failure()
            return False

        if not self.is_sidebar_open():
            self.container.show()
            aqt.mw.setMinimumWidth(self.original_mw_min_width * 2)

        return True

    def set_needs_to_accept_terms(self, needs_to_accept_terms: bool) -> None:
        self.needs_to_accept_terms = needs_to_accept_terms

    def get_page_type(self) -> Optional[SidebarPageType]:
        return self.page_type

    def is_sidebar_open(self) -> bool:
        return self.container.isVisible()

    def close_sidebar(self) -> None:
        self.container.hide()
        aqt.mw.setMinimumWidth(self.original_mw_min_width)

    def clear_states(self) -> None:
        self.resources = None
        self.page_type = None

    def get_content_url(self) -> Optional[str]:
        return self.content_webview.url().toString()

    def set_content_url(self, url: Optional[str]) -> None:
        if not self.content_webview:
            return

        self.last_accessed_url = url
        if self.needs_to_accept_terms:
            self.refresh_page_content()

        self._update_content_webview_theme()
        if url:
            self.content_webview.setUrl(aqt.QUrl(url))
        else:
            html = get_empty_state_html(
                theme=_ankihub_theme(),
                resource_type=self.get_page_type().value,
            )
            self.content_webview.setHtml(html)

    def refresh_page_content(self):
        self.content_webview.reload()

    def access_last_accessed_url(self):
        if self.last_accessed_url and self.page_type:
            self.content_webview.setUrl(aqt.QUrl(self.last_accessed_url))

    def _update_content_webview_theme(self):
        self.content_webview.eval(
            f"localStorage.setItem('theme', '{_ankihub_theme()}');"
        )

    def _on_content_page_loaded(self, ok: bool) -> None:
        if url_login() in self.content_webview.url().toString():
            self._handle_auth_failure()
            return

        if ok:
            return

        def check_auth_failure_callback(value: str) -> None:
            if value.strip().endswith("Invalid token"):
                self._handle_auth_failure()
            else:
                LOGGER.error("Failed to load page.")

        self.content_webview.evalWithCallback(
            "document.body.innerHTML", check_auth_failure_callback
        )

    def set_on_auth_failure_hook(self, hook: Callable) -> None:
        self.on_auth_failure_hook = hook

    def _handle_auth_failure(self) -> None:
        if self.on_auth_failure_hook:
            self.on_auth_failure_hook()


reviewer_sidebar: Optional[ReviewerSidebar] = None


def setup():
    """Sets up the AnkiHub AI chatbot. Adds the "View on AnkiHub" button to the reviewer toolbar."""
    reviewer_did_show_question.append(_add_or_refresh_view_note_button)

    if not using_qt5():
        webview_will_set_content.append(_inject_ankihub_features_and_setup_sidebar)
        reviewer_did_show_question.append(_notify_ankihub_ai_of_card_change)
        reviewer_did_show_question.append(_notify_reviewer_buttons_of_card_change)
        reviewer_did_show_question.append(_notify_resource_tabs_of_card_change)
        reviewer_did_show_question.append(_remove_anking_button)
        reviewer_did_show_answer.append(_remove_anking_button)

        _setup_sidebar_update_on_config_close()

    webview_did_receive_js_message.append(_on_js_message)
    reviewer_will_end.append(_close_sidebar_and_clear_states_if_exists)


def _setup_sidebar_update_on_config_close() -> None:
    """Sets up the update of the reviewer buttons and resource tabs when the config dialog is closed."""
    from .ankiaddonconfig import ConfigWindow

    def setup_config_close_callback(window: ConfigWindow) -> None:
        window.execute_on_close(notify_elements)

    def notify_elements() -> None:
        card = aqt.mw.reviewer.card
        if card:
            _notify_reviewer_buttons_of_card_change(card)
            _notify_resource_tabs_of_card_change(card)

    get_config_dialog_manager().on_window_open(setup_config_close_callback)


def _add_or_refresh_view_note_button(card: Card) -> None:
    """Adds the "View on AnkiHub" button to the reviewer toolbar if it doesn't exist yet,
    or refreshes it if it does exist already."""

    if (
        not aqt.mw.reviewer
        or not aqt.mw.reviewer.bottom
        or not aqt.mw.reviewer.bottom.web
    ):
        return

    html = dedent(
        f"""
        <button id="{VIEW_NOTE_BUTTON_ID}">
            View on AnkiHub
        </button>

        <style>
            #{VIEW_NOTE_BUTTON_ID} {{
                position: absolute;
            }}

            #{VIEW_NOTE_BUTTON_ID}:disabled {{
                color: gray;
                cursor: not-allowed;
                pointer-events: none;
            }}

            /* to not overlap with the answer buttons */
            @media(max-width: 900px) {{
                #{VIEW_NOTE_BUTTON_ID} {{
                    display: none;
                }}
            }}
        </style>
        """
    ).replace(
        "\n", " "
    )  # remove newlines to make insertAdjacentHTML work

    ankihub_nid = ankihub_db.ankihub_nid_for_anki_nid(card.nid)
    js = dedent(
        f"""
        (function() {{
        if (document.querySelector("#{VIEW_NOTE_BUTTON_ID}") === null) {{
            document.querySelector("#innertable td").insertAdjacentHTML("beforeend", '{html}');
            let button = document.querySelector("#{VIEW_NOTE_BUTTON_ID}");
            button.addEventListener("click", () => {{ pycmd('{VIEW_NOTE_PYCMD}') }})
        }}

        let button = document.querySelector("#{VIEW_NOTE_BUTTON_ID}");
        button.disabled = {"true" if ankihub_nid is None else "false"};
        }})()
        """
    )

    aqt.mw.reviewer.bottom.web.eval(js)


def _inject_ankihub_features_and_setup_sidebar(
    web_content: WebContent, context
) -> None:
    if not isinstance(context, Reviewer):
        return

    reviewer: Reviewer = context

    reviewer_button_js = get_reviewer_buttons_js(theme=_ankihub_theme())
    web_content.body += f"<script>{reviewer_button_js}</script>"

    global reviewer_sidebar
    if not reviewer_sidebar:
        reviewer_sidebar = ReviewerSidebar(reviewer)
        reviewer.ah_sidebar = reviewer_sidebar  # type: ignore[attr-defined]
        reviewer_sidebar.set_on_auth_failure_hook(_handle_auth_failure)

    if _check_access_and_notify_buttons_once not in reviewer_did_show_question._hooks:
        reviewer_did_show_question.append(_check_access_and_notify_buttons_once)

    if _check_access_and_notify_buttons_once not in config.token_change_hook:
        config.token_change_hook.append(_check_access_and_notify_buttons_once)


def _check_access_and_notify_buttons_once(*args, **kwargs) -> None:
    card = aqt.mw.reviewer.card

    if card and _visible_buttons(card):
        _check_access_and_notify_buttons()
        reviewer_did_show_question.remove(_check_access_and_notify_buttons_once)


def _check_access_and_notify_buttons() -> None:
    """Fetches the user's access to the reviwer extension feature in the background and notifies the reviewer buttons
    once the status is fetched."""

    def fetch_has_reviewer_extension_access(_) -> bool:
        client = AnkiHubClient()
        return client.has_reviewer_extension_access()

    def notify_reviewer_buttons(has_reviewer_extension_access: bool) -> None:
        js = _wrap_with_reviewer_buttons_check(
            f"ankihubReviewerButtons.updateHasReviewerExtensionAccess({'true' if has_reviewer_extension_access else 'false'})"  # noqa: E501
        )
        aqt.mw.reviewer.web.eval(js)

    def on_failure(exception: Exception) -> None:
        notify_reviewer_buttons(False)
        raise exception

    AddonQueryOp(
        op=fetch_has_reviewer_extension_access,
        success=notify_reviewer_buttons,
        parent=aqt.mw,
    ).without_collection().failure(on_failure).run_in_background()


def _related_ah_deck_has_note_embeddings(note: Note) -> bool:
    ah_did_of_note = ankihub_db.ankihub_did_for_anki_nid(note.id)
    ah_did_of_note_type = ankihub_db.ankihub_did_for_note_type(note.mid)
    ah_did_of_deck = get_ah_did_of_deck_or_ancestor_deck(
        aqt.mw.col.decks.current()["id"]
    )
    ah_dids = {ah_did_of_note, ah_did_of_deck, ah_did_of_note_type} - {None}
    return any(
        (
            (deck_config := config.deck_config(ah_did))
            and deck_config.has_note_embeddings
        )
        for ah_did in ah_dids
    )


def _ankihub_theme() -> str:
    """Returns the theme that AnkiHub should use based on the current Anki theme."""
    return "dark" if theme_manager.night_mode else "light"


def _notify_ankihub_ai_of_card_change(card: Card) -> None:
    feature_flags = config.get_feature_flags()
    if not feature_flags.get("chatbot", False):
        return

    if (
        reviewer_sidebar
        and reviewer_sidebar.is_sidebar_open()
        and reviewer_sidebar.get_page_type() == SidebarPageType.CHATBOT
    ):
        _show_chatbot_for_current_card(card)


def _show_chatbot_for_current_card(card: Card) -> None:
    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(card.nid)
    reviewer_sidebar.show_chatbot(ah_nid)


def _remove_anking_button(_: Card) -> None:
    """Removes the AnKing button (provided by the AnKing note types) from the webview if it exists.
    This is necessary because it overlaps with the AnkiHub AI chatbot button."""
    feature_flags = config.get_feature_flags()
    if not (feature_flags.get("mh_integration") or feature_flags.get("chatbot")):
        return

    js = get_remove_anking_button_js()
    aqt.mw.reviewer.web.eval(js)


def _wrap_with_reviewer_buttons_check(js: str) -> str:
    """Wraps the given JavaScript code to only run if the ankihubReviewerButtons object is defined."""
    return f"if (typeof ankihubReviewerButtons !== 'undefined') {{ {js} }}"


def _close_sidebar_and_clear_states_if_exists():
    if reviewer_sidebar:
        reviewer_sidebar.close_sidebar()
        reviewer_sidebar.clear_states()


def _notify_resource_tabs_of_card_change(_: Card) -> None:
    if (
        reviewer_sidebar
        and reviewer_sidebar.is_sidebar_open()
        and reviewer_sidebar.get_page_type() != SidebarPageType.CHATBOT
    ):
        page_type = reviewer_sidebar.get_page_type()
        resource_type = ResourceType(page_type.value)
        _show_resources_for_current_card(resource_type)


def _is_anking_deck(card: Card) -> bool:
    return ankihub_db.ankihub_did_for_anki_nid(card.note().id) == config.anking_deck_id


def _notify_reviewer_buttons_of_card_change(card: Card) -> None:
    note = card.note()
    bb_count = len(_get_resources(note.tags, ResourceType.BOARDS_AND_BEYOND))
    fa_count = len(_get_resources(note.tags, ResourceType.FIRST_AID))

    visible_buttons = _visible_buttons(card)
    js = _wrap_with_reviewer_buttons_check(
        f"""
        ankihubReviewerButtons.updateButtons(
            {bb_count},
            {fa_count},
            {json.dumps(list(visible_buttons))}
        );
        """
    )
    aqt.mw.reviewer.web.eval(js)


def _visible_buttons(card: Card) -> Set[str]:
    return _get_enabled_buttons() & _get_relevant_buttons_for_card(card)


def _get_enabled_buttons() -> Set[str]:
    result = set()
    feature_flags = config.get_feature_flags()

    if feature_flags.get("chatbot") and config.public_config.get("ankihub_ai_chatbot"):
        result.add(SidebarPageType.CHATBOT.value)

    if feature_flags.get("mh_integration"):
        if _get_enabled_steps_for_resource_type(ResourceType.BOARDS_AND_BEYOND):
            result.add(SidebarPageType.BOARDS_AND_BEYOND.value)
        if _get_enabled_steps_for_resource_type(ResourceType.FIRST_AID):
            result.add(SidebarPageType.FIRST_AID.value)

    return result


def _get_relevant_buttons_for_card(card: Card) -> Set[str]:
    result = set()

    show_chatbot = _related_ah_deck_has_note_embeddings(card.note())
    if show_chatbot:
        result.add(SidebarPageType.CHATBOT.value)

    show_mh_buttons = _is_anking_deck(aqt.mw.reviewer.card)
    if show_mh_buttons:
        result |= {
            SidebarPageType.BOARDS_AND_BEYOND.value,
            SidebarPageType.FIRST_AID.value,
        }

    return result


def _show_resources_for_current_card(resource_type: ResourceType) -> None:
    tags = aqt.mw.reviewer.card.note().tags
    resources = _get_resources(tags, resource_type)
    page_type = SidebarPageType(resource_type.value)
    reviewer_sidebar.show_resource_tabs(page_type, resources)


def _get_resources(tags: List[str], resource_type: ResourceType) -> List[Resource]:
    resource_tags = _get_resource_tags(tags, resource_type)
    result = {
        resource
        for tag in resource_tags
        if (
            (resource := mh_tag_to_resource(tag))
            and resource.usmle_step
            in _get_enabled_steps_for_resource_type(resource_type)
        )
    }
    return list(sorted(result, key=lambda x: x.title))


def _get_enabled_steps_for_resource_type(resource_type: ResourceType) -> Set[int]:
    resource_type_to_config_key_prefix = {
        ResourceType.BOARDS_AND_BEYOND: "boards_and_beyond",
        ResourceType.FIRST_AID: "first_aid_forward",
    }
    config_key_prefix = resource_type_to_config_key_prefix[resource_type]
    return {
        step
        for step in [1, 2]
        if config.public_config.get(f"{config_key_prefix}_step_{step}")
    }


def _get_resource_tags(tags: List[str], resource_type: ResourceType) -> Set[str]:
    """Get all (v12) tags matching a specific resource type."""
    search_pattern = f"v12::{RESOURCE_TYPE_TO_TAG_PART[resource_type]}".lower()
    return {tag for tag in tags if search_pattern in tag.lower()}


def _on_js_message(handled: Tuple[bool, Any], message: str, context: Any) -> Any:
    """Handles messages sent from JavaScript code."""
    if message == INVALID_AUTH_TOKEN_PYCMD:
        _handle_auth_failure()

        return True, None
    elif message.startswith(REVIEWER_BUTTON_TOGGLED_PYCMD):
        assert isinstance(context, Reviewer), context
        kwargs = parse_js_message_kwargs(message)
        button_name = kwargs.get("buttonName")
        is_active = kwargs.get("isActive")

        if button_name == "chatbot":
            if is_active:
                if reviewer_sidebar.open_sidebar():
                    _show_chatbot_for_current_card(context.card)
            else:
                reviewer_sidebar.close_sidebar()
        else:
            if is_active:
                if reviewer_sidebar.open_sidebar():
                    resource_type = ResourceType(button_name)
                    _show_resources_for_current_card(resource_type)
            else:
                reviewer_sidebar.close_sidebar()

        return True, None
    elif message == CLOSE_SIDEBAR_PYCMD:
        reviewer_sidebar.close_sidebar()

        js = _wrap_with_reviewer_buttons_check(
            "ankihubReviewerButtons.unselectAllButtons()"
        )
        aqt.mw.reviewer.web.eval(js)

        return True, None
    elif message.startswith(LOAD_URL_IN_SIDEBAR_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        reviewer_sidebar.set_content_url(kwargs["url"])

        return True, None
    elif message == OPEN_SIDEBAR_CONTENT_IN_BROWSER_PYCMD:
        url = reviewer_sidebar.get_content_url()
        if url:
            openLink(url)

        return True, None

    return handled


def _handle_auth_failure():
    if reviewer_sidebar:
        reviewer_sidebar.close_sidebar()

    js = _wrap_with_reviewer_buttons_check(
        "ankihubReviewerButtons.unselectAllButtons()"
    )
    aqt.mw.reviewer.web.eval(js)

    AnkiHubLogin.display_login()
