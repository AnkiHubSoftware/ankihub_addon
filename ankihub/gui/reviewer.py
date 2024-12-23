"""Modifies Anki's reviewer UI (aqt.reviewer)."""

from dataclasses import dataclass
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
from aqt.webview import AnkiWebPage, WebContent

from .. import LOGGER
from ..db import ankihub_db
from ..gui.menu import AnkiHubLogin
from ..gui.webview import AuthenticationRequestInterceptor, CustomWebPage  # noqa: F401
from ..main.utils import mh_tag_to_resource_title_and_slug
from ..settings import config, url_mh_integrations_preview
from .js_message_handling import (
    ANKIHUB_UPSELL,
    VIEW_NOTE_PYCMD,
    parse_js_message_kwargs,
)
from .utils import get_ah_did_of_deck_or_ancestor_deck, using_qt5
from .web.templates import (
    get_ankihub_ai_js,
    get_empty_state_html,
    get_header_webview_html,
    get_remove_anking_button_js,
    get_reviewer_buttons_js,
)

VIEW_NOTE_BUTTON_ID = "ankihub-view-note-button"

INVALID_AUTH_TOKEN_PYCMD = "ankihub_invalid_auth_token"
REVIEWER_BUTTON_TOGGLED_PYCMD = "ankihub_reviewer_button_toggled"
CLOSE_SIDEBAR_PYCMD = "ankihub_close_sidebar"
CLOSE_ANKIHUB_CHATBOT_PYCMD = "ankihub_close_chatbot"
OPEN_SPLIT_SCREEN_PYCMD = "ankihub_open_split_screen"
LOAD_URL_IN_SIDEBAR_PYCMD = "ankihub_load_url_in_sidebar"
OPEN_SIDEBAR_CONTENT_IN_BROWSER_PYCMD = "ankihub_open_sidebar_content_in_browser"


class ResourceType(Enum):
    BOARDS_AND_BEYOND = "b&b"
    FIRST_AID = "fa4"


RESOURCE_TYPE_TO_TAG_PART = {
    ResourceType.BOARDS_AND_BEYOND: "#b&b",
    ResourceType.FIRST_AID: "#firstaid",
}

RESOURCE_TYPE_TO_DISPLAY_NAME = {
    ResourceType.BOARDS_AND_BEYOND: "Boards & Beyond",
    ResourceType.FIRST_AID: "First Aid",
}


@dataclass(frozen=True)
class Resource:
    title: str
    url: str


class ReviewerSidebar:
    def __init__(self, reviewer: Reviewer):
        self.reviewer = reviewer
        self.splitter: Optional[aqt.QSplitter] = None
        self.container: Optional[aqt.QWidget] = None
        self.content_webview: Optional[aqt.webview.AnkiWebView] = None
        self.header_webview: Optional[aqt.webview.AnkiWebView] = None
        self.current_active_tab_url: Optional[str] = None
        self.resources: List[Resource] = None
        self.resource_type: Optional[ResourceType] = None
        self.original_mw_min_width = aqt.mw.minimumWidth()
        self.on_auth_failure_hook: Callable = None

        self.url_page: Optional[aqt.webview.QWebEnginePage] = None
        self.empty_state_pages: dict[ResourceType, aqt.webview.QWebEnginePage] = {}

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
        self.content_webview.setMinimumWidth(self.original_mw_min_width)

        self.update_header_button_timer = QTimer(self.content_webview)
        qconnect(
            self.update_header_button_timer.timeout, self._update_header_button_state
        )
        self.update_header_button_timer.start(200)

        self.url_page = CustomWebPage(self.profile, self.content_webview._onBridgeCmd)
        self.interceptor = AuthenticationRequestInterceptor(self.content_webview)
        self.url_page.profile().setUrlRequestInterceptor(self.interceptor)

        # Prevent white flicker on dark mode
        self.url_page.setBackgroundColor(theme_manager.qcolor(colors.CANVAS))

        aqt.qconnect(self.url_page.loadFinished, self._on_url_page_loaded)

        # Prepare empty state page for each resource type to prevent flickering
        for resource_type in ResourceType:
            page = AnkiWebPage(self.content_webview._onBridgeCmd)

            # Prevent white flicker on dark mode
            page.setBackgroundColor(theme_manager.qcolor(colors.CANVAS))

            html = get_empty_state_html(
                theme=_ankihub_theme(),
                resource_type=resource_type.value,
            )
            page.setHtml(html)

            self.empty_state_pages[resource_type] = page

        self.content_webview.setPage(list(self.empty_state_pages.values())[0])

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

    def update_tabs(
        self, resources: List[Resource], resource_type: Optional[ResourceType] = None
    ) -> None:
        self.resources = resources
        self.resource_type = resource_type if resource_type else self.resource_type

        self._update_content_webview()
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

    def _update_content_webview(self):
        if not self.resources:
            self.set_content_url(None)
        else:
            self.set_content_url(self.resources[0].url)

    def _update_header_webview(self):
        html = get_header_webview_html(
            self.resources,
            self.current_active_tab_url,
            f"{RESOURCE_TYPE_TO_DISPLAY_NAME[self.resource_type]} Viewer",
            _ankihub_theme(),
        )

        # The height of the header depends on whether there is an active tab or not.
        # Using adjustHeight wouldn't work here, because it can only make the height bigger, not smaller.
        if not self.current_active_tab_url:
            self.header_webview.setFixedHeight(44)
        else:
            self.header_webview.setFixedHeight(88)

        self.header_webview.setHtml(html)

    def open_sidebar(self):
        if not config.token():
            self._handle_auth_failure()
            return

        if not self.container.isVisible():
            self.container.show()
            aqt.mw.setMinimumWidth(self.original_mw_min_width * 2)
    

    def is_sidebar_open(self):
        return self.container.isVisible()

    def close_sidebar(self):
        self.container.hide()
        aqt.mw.setMinimumWidth(self.original_mw_min_width)

    def get_content_url(self) -> Optional[str]:
        return self.content_webview.url().toString()

    def set_content_url(self, url: Optional[str]) -> None:
        self.current_active_tab_url = url

        if not self.content_webview:
            return

        self._update_content_webview_theme()

        if url:
            self.url_page.setUrl(aqt.QUrl(url))
            if self.content_webview.page() != self.url_page:
                self.content_webview.setPage(self.url_page)
        else:
            self.content_webview.setPage(self.empty_state_pages[self.resource_type])
            
    def refresh_content_webview(self):
        self.content_webview.reload()



    def _update_content_webview_theme(self):
        self.content_webview.eval(
            f"localStorage.setItem('theme', '{_ankihub_theme()}');"
        )

    def _on_url_page_loaded(self, ok: bool) -> None:
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
        webview_will_set_content.append(_add_ankihub_ai_and_sidebar_and_buttons)
        reviewer_did_show_question.append(_notify_ankihub_ai_of_card_change)
        reviewer_did_show_question.append(_notify_reviewer_buttons_of_card_change)
        reviewer_did_show_question.append(_notify_sidebar_of_card_change)
        config.token_change_hook.append(_set_token_for_ankihub_ai_js)
        reviewer_did_show_question.append(_remove_anking_button)
        reviewer_did_show_answer.append(_remove_anking_button)

    webview_did_receive_js_message.append(_on_js_message)
    reviewer_will_end.append(_close_sidebar_if_exists)


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


def _add_ankihub_ai_and_sidebar_and_buttons(web_content: WebContent, context):
    if not isinstance(context, Reviewer):
        return

    feature_flags = config.get_feature_flags()
    # TODO This condition is not placed correctly. It should be used to
    # show/hide the AnkiHub AI chatbot button when the reviewer_did_show_question hook is called.
    # The consquence of the current implementation is that the chatbot icon is shown for notes it
    # shouldn't be shown for and (maybe) isn't shown for notes it should be shown for in some cases.
    # However, we don't have to fix it for the old chatbot implementation, because we will switch
    # to a new one soon. For the new implementation, we should implement the correct logic.
    if feature_flags.get("chatbot"):
        if feature_flags.get("mh_integration"):
            ankihub_ai_js_template_name = "ankihub_ai.js"
        else:
            # The new chatbot js (ankihub_ai.js) doesn't show a button, so we can always set it up.
            # However, the old chatbot js (ankihub_ai_old.js) shows a button when executed, so we only
            # want to execute it when the note has note embeddings.
            if not _related_ah_deck_has_note_embeddings(aqt.mw.reviewer.card.note()):
                return

            ankihub_ai_js_template_name = "ankihub_ai_old.js"

        ankihub_ai_js = get_ankihub_ai_js(
            template_name=ankihub_ai_js_template_name,
            knox_token=config.token(),
            app_url=config.app_url,
            endpoint_path="ai/chatbot",
            query_parameters="is_on_anki=true",
            theme=_ankihub_theme(),
        )
        web_content.body += f"<script>{ankihub_ai_js}</script>"

    if feature_flags.get("mh_integration"):
        global reviewer_sidebar
        if not reviewer_sidebar:
            reviewer_sidebar = ReviewerSidebar(context)
            reviewer_sidebar.set_on_auth_failure_hook(_handle_auth_failure)

        reviewer_button_js = get_reviewer_buttons_js(
            theme=_ankihub_theme(),
            enabled_buttons=_get_enabled_buttons_list(),
        )
        web_content.body += f"<script>{reviewer_button_js}</script>"


def _get_enabled_buttons_list() -> List[str]:
    buttons_map = {
        "ankihub_ai_chatbot": "chatbot",
        "boards_and_beyond": "b&b",
        "first_aid_forward": "fa4",
    }
    public_config = config.public_config
    buttons_config = dict(
        filter(
            lambda item: item[0]
            in ["ankihub_ai_chatbot", "boards_and_beyond", "first_aid_forward"],
            public_config.items(),
        )
    )
    enabled_buttons_list = [
        buttons_map[key] for key, value in buttons_config.items() if value
    ]
    return enabled_buttons_list


def _related_ah_deck_has_note_embeddings(note: Note) -> bool:
    ah_did_of_note = ankihub_db.ankihub_did_for_anki_nid(note.id)
    ah_dids_of_note_type = ankihub_db.ankihub_dids_for_note_type(note.mid)
    ah_did_of_deck = get_ah_did_of_deck_or_ancestor_deck(
        aqt.mw.col.decks.current()["id"]
    )
    ah_dids = {ah_did_of_note, ah_did_of_deck, *ah_dids_of_note_type} - {None}
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

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(card.nid)
    js = _wrap_with_ankihubAI_check(f"ankihubAI.cardChanged('{ah_nid}');")
    aqt.mw.reviewer.web.eval(js)


def _remove_anking_button(_: Card) -> None:
    """Removes the AnKing button (provided by the AnKing note types) from the webview if it exists.
    This is necessary because it overlaps with the AnkiHub AI chatbot button."""
    feature_flags = config.get_feature_flags()
    if not (feature_flags.get("mh_integration") or feature_flags.get("chatbot")):
        return

    js = _wrap_with_ankihubAI_check(get_remove_anking_button_js())
    aqt.mw.reviewer.web.eval(js)


def _set_token_for_ankihub_ai_js() -> None:
    feature_flags = config.get_feature_flags()
    if not feature_flags.get("chatbot", False):
        return

    js = _wrap_with_ankihubAI_check(f"ankihubAI.setToken('{config.token()}');")
    aqt.mw.reviewer.web.eval(js)


def _wrap_with_ankihubAI_check(js: str) -> str:
    """Wraps the given JavaScript code to only run if the AnkiHub AI object is defined."""
    return f"if (typeof ankihubAI !== 'undefined') {{ {js} }}"


def _wrap_with_reviewer_buttons_check(js: str) -> str:
    """Wraps the given JavaScript code to only run if the ankihubReviewerButtons object is defined."""
    return f"if (typeof ankihubReviewerButtons !== 'undefined') {{ {js} }}"


def _close_sidebar_if_exists():
    if reviewer_sidebar:
        reviewer_sidebar.close_sidebar()


def _notify_sidebar_of_card_change(_: Card) -> None:
    if reviewer_sidebar and reviewer_sidebar.is_sidebar_open():
        _update_sidebar_tabs_based_on_tags(reviewer_sidebar.resource_type)


def _is_anking_deck(card: Card) -> bool:
    return ankihub_db.ankihub_did_for_anki_nid(card.note().id) == config.anking_deck_id


def _notify_reviewer_buttons_of_card_change(card: Card) -> None:
    note = card.note()
    bb_count = len(_get_resources(note.tags, ResourceType.BOARDS_AND_BEYOND))
    fa_count = len(_get_resources(note.tags, ResourceType.FIRST_AID))

    is_anking_deck = _is_anking_deck(aqt.mw.reviewer.card)
    is_anking_deck = True
    show_chatbot = _related_ah_deck_has_note_embeddings(card.note())
    show_chatbot = True
    js = _wrap_with_reviewer_buttons_check(
        f"""
        ankihubReviewerButtons.updateButtons(
            {bb_count},
            {fa_count},
            {'true' if show_chatbot else 'false'},
            {'true' if is_anking_deck else 'false'},
        );
        """
    )
    aqt.mw.reviewer.web.eval(js)
    if reviewer_sidebar and config.token():
        reviewer_sidebar.set_content_url("http://localhost:8000/ai/chatbot/d165ea37-83b9-4a8e-9563-500ba9dccdd9/is_on_anki=true")

def _update_sidebar_tabs_based_on_tags(resource_type: ResourceType) -> None:
    if not reviewer_sidebar:
        return

    tags = aqt.mw.reviewer.card.note().tags
    resources = _get_resources(tags, resource_type)
    reviewer_sidebar.update_tabs(resources, resource_type)


def _get_resources(tags: List[str], resource_type: ResourceType) -> List[Resource]:
    resource_tags = _get_resource_tags(tags, resource_type)
    result = {
        Resource(title=title, url=url_mh_integrations_preview(slug))
        for tag in resource_tags
        if (title_and_slug := mh_tag_to_resource_title_and_slug(tag))
        for title, slug in [title_and_slug]
    }
    return list(sorted(result, key=lambda x: x.title))


def _get_resource_tags(tags: List[str], resource_type: ResourceType) -> Set[str]:
    """Get all (v12) tags matching a specific resource type."""
    search_pattern = f"v12::{RESOURCE_TYPE_TO_TAG_PART[resource_type]}".lower()
    return {tag for tag in tags if search_pattern in tag.lower()}


def _on_js_message(handled: Tuple[bool, Any], message: str, context: Any) -> Any:
    """Handles messages sent from JavaScript code."""
    if message == INVALID_AUTH_TOKEN_PYCMD:
        _handle_auth_failure()

        return True, None
    elif message == CLOSE_ANKIHUB_CHATBOT_PYCMD:
        assert isinstance(context, Reviewer), context
        js = _wrap_with_ankihubAI_check("ankihubAI.hideIframe();")
        context.web.eval(js)

        return True, None
    elif message.startswith(REVIEWER_BUTTON_TOGGLED_PYCMD):
        assert isinstance(context, Reviewer), context
        kwargs = parse_js_message_kwargs(message)
        button_name = kwargs.get("buttonName")
        is_active = kwargs.get("isActive")

        if button_name == "chatbot":
            if is_active:
                reviewer_sidebar.open_sidebar()
                # js = _wrap_with_ankihubAI_check("ankihubAI.showIframe();")
            else:
                reviewer_sidebar.close_sidebar()
                # js = _wrap_with_ankihubAI_check("ankihubAI.hideIframe();")
            # context.web.eval(js)
        else:
            # TODO load correct sidebar content (Boards&Beyond, First Aid or AnkiHub Chatbot)
            # depending on the button that was toggled
            if is_active:
                reviewer_sidebar.open_sidebar()
                resource_type = ResourceType(button_name)
                _update_sidebar_tabs_based_on_tags(resource_type)
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
    elif message == ANKIHUB_UPSELL:
        js = _wrap_with_ankihubAI_check("ankihubAI.hideIframe();")
        context.web.eval(js)
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