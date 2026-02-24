import functools
import json
from asyncio.futures import Future
from dataclasses import dataclass
from functools import cached_property, partial
from typing import Any, Callable, Optional, Required, TypedDict, Union

import aqt
from anki.hooks import wrap
from aqt import gui_hooks
from aqt.browser import Browser
from aqt.browser.sidebar.item import SidebarItem, SidebarItemType
from aqt.browser.sidebar.tree import SidebarTreeView
from aqt.deckoptions import DeckOptionsDialog
from aqt.editor import Editor
from aqt.main import AnkiQt, MainWindowState
from aqt.operations.deck import set_current_deck
from aqt.operations.scheduling import unsuspend_cards
from aqt.overview import Overview, OverviewBottomBar
from aqt.qt import (
    QAbstractItemView,
    QPoint,
    Qt,
    QTimer,
    QToolButton,
    QVBoxLayout,
    QWidget,
    qconnect,
    sip,
)
from aqt.reviewer import Reviewer, ReviewerBottomBar
from aqt.toolbar import BottomBar, Toolbar, TopToolbar
from aqt.utils import tooltip
from aqt.webview import AnkiWebView, WebContent

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..django import render_template, render_template_from_string
from ..gui.overlay_dialog import OverlayDialog, OverlayTarget
from ..settings import config
from .flashcard_selector_dialog import (
    show_flashcard_selector,
)
from .operations import AddonQueryOp
from .utils import extract_argument

START_TUTORIAL_PYCMD = "ankihub_start_tutorial"
DISMISS_TUTORIAL_PYCMD = "ankihub_dismiss_tutorial"
SKIP_TUTORIAL_PYCMD = "ankihub_skip_tutorial"
NEXT_STEP_PYCMD = "ankihub_tutorial_next_step"
PREV_STEP_PYCMD = "ankihub_tutorial_prev_step"
TARGET_RESIZE_PYCMD = "ankihub_tutorial_target_resize"
TUTORIAL_CLOSED_PYCMD = "ankihub_tutorial_closed"
JS_LOADED_PYCMD = "ankihub_tutorial_js_loaded"
TARGET_CLICK_PYCMD = "ankihub_tutorial_target_click"
FLASHCARD_SELECTOR_OPEN_PYCMD = "ankihub_browser_flashcard_selector_open"


class RenderDialogKwargs(TypedDict, total=False):
    title: Required[str]
    body: Required[str]
    text_button_label: Optional[str]
    secondary_button_label: Optional[str]
    main_button_label: Optional[str]
    on_text_button_click: Optional[str]
    on_secondary_button_click: Optional[str]
    on_main_button_click: Optional[str]
    on_close: Optional[str]


def render_dialog(**kwargs: RenderDialogKwargs) -> str:
    return render_template(
        "dialog.html",
        context=kwargs,
    )


def render_tour_step(
    body: str,
    current_step: int,
    total_steps: int,
    back_label: str = "Back",
    on_back: Optional[str] = None,
    next_label: str = "Next",
    on_next: Optional[str] = None,
    on_close: Optional[str] = None,
    show_backdrop: bool = True,
    close_button: bool = True,
) -> str:
    return render_template(
        "tour_step.html",
        {
            "body": body,
            "current_step": current_step,
            "total_steps": total_steps,
            "back_label": back_label,
            "on_back": on_back,
            "next_label": next_label,
            "on_next": on_next,
            "on_close": on_close,
            "show_backdrop": show_backdrop,
            "close_button": close_button,
            "class": "ah-tour-step",
        },
    )


def render_arrow() -> str:
    return render_template("arrow.html")


def render_backdrop() -> str:
    return render_template_from_string("<c-v1.backdrop :open=True />")


def get_backdrop_js() -> str:
    return f"AnkiHub.addTutorialBackdrop({json.dumps(render_backdrop())});"


def render_link(href: str, text: str) -> str:
    # Copied from the Link component in the website - we can't use it directly right now
    # because it unconditionally adds an icon that we don't need here
    classes = (
        "component-default inline-flex items-center rounded-md "
        "outline-offset-0 text-text-primary-main underline decoration-1 underline-offset-4 "
        "hover:text-text-primary-main-hover hover:decoration-link-border hover:decoration-2 "
        "focus:outline-2 focus:outline-border-primary-focus focus-visible:outline-2 "
        "focus-visible:outline-border-primary-focus focus:hover:outline-link-border "
        "focus:hover:text-text-primary-hover focus:no-underline focus-visible:no-underline"
    )
    return f"<a class='{classes}' href='{href}'>{text}</a>"


class TutorialOverlayDialog(OverlayDialog):
    def __init__(self, parent: QWidget, target: Optional[OverlayDialog], target_outline: bool = True) -> None:
        self.target_outline = target_outline
        super().__init__(parent, target)

    def setup_ui(self) -> None:
        vbox = QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        self.setLayout(vbox)
        self.web = AnkiWebView(self)
        self.web.disable_zoom()
        self.web.set_bridge_command(self.on_bridge_cmd, self)
        vbox.addWidget(self.web)
        self.refresh()
        qconnect(self.finished, lambda: self.web.cleanup())

    def refresh(self) -> None:
        web_base = f"/_addons/{aqt.mw.addonManager.addonFromModule(__name__)}/gui/web"
        self.web.stdHtml(
            "<div id=target></div>",
            css=[f"{web_base}/overlay.css"],
            js=[],
            default_css=False,
            context=self,
        )
        self.web.page().setBackgroundColor(Qt.GlobalColor.transparent)

    def on_bridge_cmd(self, cmd: str) -> None:
        if cmd == FLASHCARD_SELECTOR_OPEN_PYCMD:
            show_flashcard_selector(config.anking_deck_id, parent=self.parentWidget())
        else:
            print("unhandled bridge cmd:", cmd)

    def on_position(self) -> None:
        super().on_position()
        if not self.target:
            return

        rect = self.target.rect()
        webview_top_left = self.web.mapFromGlobal(rect.topLeft())
        webview_bottom_right = self.web.mapFromGlobal(rect.bottomRight())
        top = webview_top_left.y()
        left = webview_top_left.x()
        width = webview_bottom_right.x() - left
        height = webview_bottom_right.y() - top
        # Clamp height to webview's visible area to prevent scrollbars
        webview_height = self.web.height()
        max_height = webview_height - top
        height = min(height, max_height)
        self.web.eval(
            """
(() => {
    const target = document.getElementById('target');
    const targetClass = '%(target_class)s';
    if(targetClass) target.classList.add(targetClass);
    target.style.top = '%(top)dpx';
    target.style.left = '%(left)dpx';
    target.style.width = '%(width)dpx';
    target.style.height = '%(height)dpx';
})();
        """
            % dict(
                target_class="ah-outline" if self.target_outline else "", top=top, left=left, width=width, height=height
            )
        )


def webview_for_context(context: Any) -> AnkiWebView:
    from aqt.deckbrowser import DeckBrowser, DeckBrowserBottomBar

    if isinstance(context, AnkiQt):
        return aqt.mw.web
    if isinstance(context, (DeckBrowser, Reviewer, Overview)):
        return aqt.mw.web
    if isinstance(context, (BottomBar, DeckBrowserBottomBar, OverviewBottomBar, ReviewerBottomBar)):
        return aqt.mw.bottomWeb
    if isinstance(context, (Toolbar, TopToolbar)):
        return aqt.mw.toolbar.web
    if isinstance(context, Editor):
        return context.web
    if isinstance(context, (TutorialOverlayDialog,)):
        return context.web
    if isinstance(context, DeckOptionsDialog):
        return context.web
    else:
        assert False, f"Webview context of type {type(context)} is not handled"


def on_ankihub_loaded_js(on_loaded: str) -> str:
    return (
        """
    (() => {
        const intervalId = setInterval(() => {
            if (typeof AnkiHub !== 'undefined') {
                clearInterval(intervalId);
                {%s}
            }
        }, 10);
    })();
"""
        % on_loaded
    )


def tutorial_assets_js(on_loaded: str) -> str:
    web_base = f"/_addons/{aqt.mw.addonManager.addonFromModule(__name__)}/gui/web"
    js = """
(() => {
    const onLoaded = () => {%(on_loaded)s};
    const cssId = "ankihub-tutorial-css";
    if(!document.getElementById(cssId)) {
        const css = document.createElement("link");
        css.id = cssId;
        css.rel = "stylesheet";
        css.type = "text/css";
        css.href = %(css_path)s;
        document.head.appendChild(css);
    }

    const jsId = "ankihub-tutorial-js";
    if(!document.getElementById(jsId)) {
        const js = document.createElement("script");
        js.id = jsId;
        js.addEventListener("load", onLoaded);
        document.body.appendChild(js);
        js.src = %(js_path)s;
    }
    else {
        onLoaded();
    }
})();
""" % dict(
        css_path=json.dumps(f"{web_base}/lib/tutorial.css"),
        js_path=json.dumps(f"{web_base}/lib/tutorial.js"),
        on_loaded=on_ankihub_loaded_js(on_loaded),
    )
    return js


def inject_tutorial_assets(context: Any, on_loaded: Optional[Callable[[], None]]) -> None:
    def on_webview_did_receive_js_message(handled: tuple[bool, Any], message: str, context: Any) -> tuple[bool, Any]:
        if message == JS_LOADED_PYCMD:
            on_loaded()
            gui_hooks.webview_did_receive_js_message.remove(on_webview_did_receive_js_message)
            return True, None
        return handled

    gui_hooks.webview_did_receive_js_message.append(on_webview_did_receive_js_message)
    js = tutorial_assets_js(f"pycmd('{JS_LOADED_PYCMD}');")
    web = webview_for_context(context)
    web.eval(js)


@dataclass
class TutorialStep:
    body: str
    target: Optional[Union[str, Callable[[], str]]] = ""
    click_target: Optional[Union[str, Callable[[], str]]] = ""
    tooltip_context: Optional[Any] = None
    target_context: Optional[Any] = None
    shown_callback: Optional[Callable[["TutorialStep"], None]] = None
    hidden_callback: Optional[Callable[[], None]] = None
    next_callback: Optional[Callable[[Callable[[], None]], None]] = None
    next_label: str = "Next"
    back_callback: Optional[Callable[[Callable[[], None]], None]] = None
    back_label: str = "Back"
    block_target_click: bool = False
    auto_advance: bool = True
    apply_backdrop: bool = True
    remove_parent_backdrop: bool = False
    close_button: bool = True

    def __post_init__(self):
        if not self.target_context:
            self.target_context = self.tooltip_context


@dataclass
class QtTutorialStep(TutorialStep):
    parent_widget: Optional[Union[QWidget, Callable[[], QWidget]]] = None
    qt_target: Optional[Union[OverlayTarget, Callable[[], OverlayTarget]]] = None
    target_outline: bool = True
    apply_backdrop: bool = False


active_tutorial: Optional["Tutorial"] = None


class Tutorial:
    def __init__(self) -> None:
        self.current_step = 1

    @classmethod
    def is_active(cls) -> bool:
        return isinstance(active_tutorial, cls)

    @property
    def steps(self) -> list[TutorialStep]:
        return []

    def extra_backdrop_contexts(self) -> tuple[Any, ...]:
        return tuple()

    def extra_backdrop_context_types(self) -> tuple[Any, ...]:
        """Similar to `extra_backdrop_contexts`,
        but returns the types of the contexts instead of the contexts themselves.
        Required to handle DeckBrowserBottomBar and OverviewBottomBar,
        which Anki doesn't keep a reference to their instances.
        """
        return tuple(type(context) for context in self.extra_backdrop_contexts())

    def _render_js_function_with_options(self, function: str, options: dict[str, Any]) -> str:
        return on_ankihub_loaded_js(
            f"AnkiHub.{function}({{" + ",".join(f"{k}: {json.dumps(v)}" for k, v in options.items()) + "})"
        )

    def _render_tooltip(self, eval_js: bool = True) -> str:
        step = self.steps[self.current_step - 1]
        tooltip_web = webview_for_context(step.tooltip_context)
        modal = render_tour_step(
            body=step.body,
            current_step=self.current_step,
            total_steps=len(self.steps),
            back_label=step.back_label,
            on_back=f"pycmd('{PREV_STEP_PYCMD}')",
            on_next=f"pycmd('{NEXT_STEP_PYCMD}')",
            next_label=step.next_label,
            on_close=f"pycmd('{TUTORIAL_CLOSED_PYCMD}')",
            show_backdrop=step.apply_backdrop,
            close_button=step.close_button,
        )
        arrow = render_arrow() if step.target else ""
        tooltip_options = {
            "modal": modal,
            "arrow": arrow,
            "blockTargetClick": step.block_target_click,
            "removeParentBackdrop": step.remove_parent_backdrop,
        }
        if step.target and step.tooltip_context == step.target_context:
            tooltip_options["target"] = step.target if isinstance(step.target, str) else step.target()
        if step.click_target:
            tooltip_options["clickTarget"] = (
                step.click_target if isinstance(step.click_target, str) else step.click_target()
            )
        js = self._render_js_function_with_options("showTutorialStep", tooltip_options)
        if eval_js:
            tooltip_web.eval(js)
        return js

    def _render_highlight(self, eval_js: bool = True) -> str:
        step = self.steps[self.current_step - 1]
        target_web = webview_for_context(step.target_context)
        js = ""
        if step.target and step.tooltip_context != step.target_context:
            js = self._render_js_function_with_options(
                "highlightTutorialTarget",
                {
                    "target": step.target,
                    "currentStep": self.current_step,
                    "blockTargetClick": step.block_target_click,
                    "backdrop": render_backdrop(),
                    "removeParentBackdrop": step.remove_parent_backdrop,
                },
            )
            if eval_js:
                target_web.eval(js)
        return js

    def _render_backdrop(self) -> None:
        step = self.steps[self.current_step - 1]
        tooltip_web = webview_for_context(step.tooltip_context)
        target_web = webview_for_context(step.target_context)
        backdrop_js = get_backdrop_js()
        webviews = set()
        for context in self.extra_backdrop_contexts():
            web = webview_for_context(context)
            if web not in (tooltip_web, target_web):
                webviews.add(web)
        for web in webviews:
            web.eval(backdrop_js)

    def contexts_for_step(self, step: TutorialStep) -> tuple[Any]:
        backdrop_contexts = []
        backdrop_contexts.extend(self.extra_backdrop_contexts())
        return (step.tooltip_context, step.target_context, *backdrop_contexts)

    def show_current(self) -> None:
        step = self.steps[self.current_step - 1]
        if isinstance(step, QtTutorialStep):
            target = step.qt_target() if callable(step.qt_target) else step.qt_target
            parent_widget = step.parent_widget() if callable(step.parent_widget) else step.parent_widget
            overlay = TutorialOverlayDialog(
                parent_widget or (target and target.window()) or aqt.mw, target, step.target_outline
            )
            overlay.show()
            step.tooltip_context = overlay
            step.target_context = overlay
            step.target = "#target"

            def close_overlay() -> None:
                overlay.close()

            step.hidden_callback = close_overlay

        if step.shown_callback:
            step.shown_callback(step)

        contexts = []
        webviews = set()
        for context in self.contexts_for_step(step):
            web = webview_for_context(context)
            if web not in webviews:
                contexts.append(context)
                webviews.add(web)
        loaded_scripts = 0

        def on_script_loaded() -> None:
            nonlocal loaded_scripts
            loaded_scripts += 1
            if loaded_scripts == len(contexts):
                self._render_tooltip()
                self._render_highlight()
                self._render_backdrop()

        for context in contexts:
            inject_tutorial_assets(context, on_script_loaded)

    def start(self) -> None:
        global active_tutorial
        active_tutorial = self
        gui_hooks.webview_did_receive_js_message.append(self._on_webview_did_receive_js_message)
        gui_hooks.webview_will_set_content.append(self._on_webview_will_set_content)
        self.show_current()

    def restart(self) -> None:
        self.current_step = 1
        self.show_current()

    def _finalize_tutorial(self) -> None:
        gui_hooks.webview_did_receive_js_message.remove(self._on_webview_did_receive_js_message)
        gui_hooks.webview_will_set_content.remove(self._on_webview_will_set_content)
        global active_tutorial
        active_tutorial = None

    def end(self) -> None:
        self._cleanup_step(all_webviews=True)
        self._finalize_tutorial()

    def _on_webview_did_receive_js_message(
        self, handled: tuple[bool, Any], message: str, context: Any
    ) -> tuple[bool, Any]:
        if message == PREV_STEP_PYCMD:
            step = self.steps[self.current_step - 1]
            if step.back_callback:
                step.back_callback(self.back if step.auto_advance else lambda: None)
            else:
                self.back()
            return True, None

        if message == NEXT_STEP_PYCMD:
            step = self.steps[self.current_step - 1]
            if step.next_callback:
                step.next_callback(self.next if step.auto_advance else lambda: None)
            else:
                self.next()
            return True, None
        elif message.startswith(TARGET_RESIZE_PYCMD):
            parts = message.split(":")
            current_step = int(parts[1])
            top = float(parts[2])
            left = float(parts[3])
            width = float(parts[4])
            height = float(parts[5])
            if current_step == self.current_step:
                step = self.steps[current_step - 1]
                target_web = webview_for_context(step.target_context)
                tooltip_web = webview_for_context(step.tooltip_context)
                target_coords = target_web.mapToGlobal(QPoint(0, 0))
                tooltip_coords = tooltip_web.mapToGlobal(QPoint(0, 0))
                tooltip_web_geom = tooltip_web.geometry()

                if target_coords.y() - tooltip_coords.y() > 0:
                    top = tooltip_web_geom.bottom()
                    height = 0
                elif target_coords.y() - tooltip_coords.y() < 0:
                    top = 0
                    height = 0

                if target_coords.x() - tooltip_coords.x() > 0:
                    left = tooltip_web_geom.right()
                    width = 0
                elif target_coords.x() - tooltip_coords.x() < 0:
                    left = 0
                    width = 0

                tooltip_web.eval(
                    self._render_js_function_with_options(
                        "positionTutorialModal",
                        {
                            "top": top,
                            "left": left,
                            "width": width,
                            "height": height,
                        },
                    )
                )
            return True, None
        elif message == TUTORIAL_CLOSED_PYCMD:
            self.end()
            return True, None
        elif message == TARGET_CLICK_PYCMD:
            step = self.steps[self.current_step - 1]
            if step.next_callback:
                step.next_callback(self.next)
            else:
                self.next()

            return True, None
        return handled

    def _on_webview_will_set_content(self, web_content: WebContent, context: Any) -> None:
        step = self.steps[self.current_step - 1]
        js = ""
        if context == step.tooltip_context:
            js = self._render_tooltip(eval_js=False)
        elif context == step.target_context:
            js = self._render_highlight(eval_js=False)
        elif context in self.extra_backdrop_contexts() or isinstance(context, self.extra_backdrop_context_types()):
            js = get_backdrop_js()
        if js:
            js = tutorial_assets_js(js)
            web_content.body += f"<script>{js}</script>"

    def _cleanup_step(self, all_webviews: bool = False) -> None:
        step = self.steps[self.current_step - 1]
        webviews = set()
        for context in self.contexts_for_step(step):
            webviews.add(webview_for_context(context))
        if self.current_step < len(self.steps) and not all_webviews:
            next_step = self.steps[self.current_step]
            for context in self.contexts_for_step(next_step):
                # Skip if no context set yet (in Qt screens)
                if not context:
                    continue
                web = webview_for_context(context)
                if web in webviews:
                    webviews.remove(web)

        for web in webviews:
            web.eval("if(typeof AnkiHub !== 'undefined') AnkiHub.destroyActiveTutorialEffect()")

        if step.hidden_callback:
            step.hidden_callback()

    def back(self) -> None:
        if self.current_step == 1:
            self.end()
            return
        self._cleanup_step()
        self.current_step -= 1
        self.show_current()

    def next(self) -> None:
        if self.current_step >= len(self.steps):
            self.end()
            return
        self._cleanup_step()
        self.current_step += 1
        self.show_current()


def ensure_mw_state(
    state: MainWindowState,
) -> Callable[[Callable[..., None]], Callable[..., None]]:
    def change_state_and_call_func(func: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        def on_state_did_change(old_state: MainWindowState, new_state: MainWindowState) -> None:
            gui_hooks.state_did_change.remove(on_state_did_change)
            # Some delay appears to be required for the toolbar
            aqt.mw.progress.single_shot(100, lambda: func(*args, **kwargs))

        gui_hooks.state_did_change.append(on_state_did_change)
        aqt.mw.moveToState(state)

    def decorated_func(func: Callable[..., None]) -> Callable[..., None]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            if aqt.mw.state != state:
                change_state_and_call_func(func, *args, **kwargs)
            else:
                func(*args, **kwargs)

        return wrapper

    return decorated_func


def prompt_for_tutorial(
    context_types: tuple[Any, ...],
    contexts: tuple[Any, ...],
    dialog_context: Any,
    dialog_kwargs: RenderDialogKwargs,
    on_start: Callable[[], None],
    on_dismiss: Callable[[], None],
    on_skip: Optional[Callable[[], None]] = None,
) -> None:
    if active_tutorial:
        return

    def js_for_context(context: Any) -> str:
        if isinstance(context, dialog_context):
            body = render_dialog(
                on_main_button_click=f"pycmd('{START_TUTORIAL_PYCMD}')",
                on_secondary_button_click=f"pycmd('{SKIP_TUTORIAL_PYCMD}')",
                on_close=f"pycmd('{DISMISS_TUTORIAL_PYCMD}')",
                **dialog_kwargs,
            )
            js = f"AnkiHub.showModal({json.dumps(body)})"
        else:
            js = get_backdrop_js()
        return on_ankihub_loaded_js(js)

    def on_webview_did_receive_js_message(handled: tuple[bool, Any], message: str, context: Any) -> tuple[bool, Any]:
        if message == START_TUTORIAL_PYCMD:
            remove_hooks()
            on_start()
            return True, None
        if message == DISMISS_TUTORIAL_PYCMD:
            clean_up_webviews()
            on_dismiss()
            return True, None
        if message == SKIP_TUTORIAL_PYCMD:
            clean_up_webviews()
            if on_skip:
                on_skip()
            return True, None
        return handled

    def on_webview_will_set_content(web_content: WebContent, context: Any) -> None:
        if context not in contexts and not isinstance(context, context_types):
            return
        # Rerender the prompt if the deck browser refreshes due to background operations or mw.reset()
        js = tutorial_assets_js(js_for_context(context))
        web_content.body += f"<script>{js}</script>"

    def remove_hooks() -> None:
        gui_hooks.webview_did_receive_js_message.remove(on_webview_did_receive_js_message)
        gui_hooks.webview_will_set_content.remove(on_webview_will_set_content)

    def clean_up_webviews() -> None:
        remove_hooks()
        for context in contexts:
            web = webview_for_context(context)
            web.eval("AnkiHub.destroyActiveTutorialEffect()")

    gui_hooks.webview_did_receive_js_message.append(on_webview_did_receive_js_message)
    gui_hooks.webview_will_set_content.append(on_webview_will_set_content)

    loaded_scripts = 0

    def on_script_loaded() -> None:
        nonlocal loaded_scripts
        loaded_scripts += 1
        if loaded_scripts == len(contexts):
            for context in contexts:
                web = webview_for_context(context)
                web.eval(js_for_context(context))

    for context in contexts:
        inject_tutorial_assets(context, on_script_loaded)


def prompt_for_onboarding_tutorial() -> None:
    from aqt.deckbrowser import DeckBrowser, DeckBrowserBottomBar

    if config.get_feature_flags().get("onboarding_tour", False):
        return

    config.set_onboarding_tutorial_pending(True)

    context_types = (DeckBrowser, DeckBrowserBottomBar, TopToolbar)
    contexts = (aqt.mw.deckBrowser, aqt.mw.deckBrowser.bottom, aqt.mw.toolbar)

    prompt_for_tutorial(
        context_types=context_types,
        contexts=contexts,
        dialog_context=DeckBrowser,
        dialog_kwargs=dict(
            title="📚 First time with Anki?",
            body="Find your way in the app with this <b>onboarding tour</b>.<br>"
            "You can revisit it anytime in AnkiHub's Help menu.",
            secondary_button_label="Maybe later",
            main_button_label="Take tour",
        ),
        on_start=lambda: OnboardingTutorial().start(),
        on_dismiss=lambda: config.set_onboarding_tutorial_pending(False),
    )


def prompt_for_step_deck_tutorial(on_skip: Optional[Callable[[], None]] = None) -> None:
    from aqt.deckbrowser import DeckBrowser, DeckBrowserBottomBar

    if config.get_feature_flags().get("step_deck_tour", False):
        return

    config.set_step_deck_tutorial_pending(True)
    config.set_show_step_deck_tutorial(False)

    context_types = (DeckBrowser, DeckBrowserBottomBar, TopToolbar)
    contexts = (aqt.mw.deckBrowser, aqt.mw.deckBrowser.bottom, aqt.mw.toolbar)

    prompt_for_tutorial(
        context_types=context_types,
        contexts=contexts,
        dialog_context=DeckBrowser,
        dialog_kwargs=dict(
            title="📘 Add cards to your study queue",
            body="When installed, the AnKing Step Deck comes with all cards hidden. "
            "Take this tour to learn how to <b>select cards to study</b> and <b>set your daily limits</b>.<br><br>"
            "You can revisit this anytime in AnkiHub's Help menu.",
            secondary_button_label="Skip for now",
            main_button_label="Take tour",
        ),
        on_start=lambda: StepDeckTutorial().start(),
        on_dismiss=lambda: config.set_step_deck_tutorial_pending(False),
        on_skip=on_skip,
    )


def prompt_for_pending_tutorial() -> None:
    if config.onboarding_tutorial_pending():
        prompt_for_onboarding_tutorial()
    elif config.step_deck_tutorial_pending():
        prompt_for_step_deck_tutorial()


def setup() -> None:
    gui_hooks.main_window_did_init.append(prompt_for_pending_tutorial)


class DeckBrowserOverviewBackdropMixin:
    def extra_backdrop_contexts(self) -> tuple[Any, ...]:
        return (aqt.mw.deckBrowser, aqt.mw.overview, aqt.mw.deckBrowser.bottom, aqt.mw.toolbar, aqt.mw.overview.bottom)

    def extra_backdrop_context_types(self) -> tuple[Any, ...]:
        from aqt.deckbrowser import DeckBrowserBottomBar

        return (*super().extra_backdrop_context_types(), DeckBrowserBottomBar, OverviewBottomBar)


class OnboardingTutorial(DeckBrowserOverviewBackdropMixin, Tutorial):
    @ensure_mw_state("deckBrowser")
    def start(self) -> None:
        return super().start()

    def end(self) -> None:
        config.set_onboarding_tutorial_pending(False)
        return super().end()

    def _monitor_mw_state_change(self, on_done: Callable[[], None]) -> None:
        def on_state_did_change(*args: Any, **kwargs: Any) -> None:
            gui_hooks.state_did_change.remove(on_state_did_change)
            on_done()

        gui_hooks.state_did_change.append(on_state_did_change)

    def _move_to_intro_deck_overview(self, on_done: Callable[[], None]) -> None:
        did = config.deck_config(config.intro_deck_id).anki_id
        aqt.mw.col.decks.set_current(did)
        self._monitor_mw_state_change(on_done)
        aqt.mw.moveToState("overview")

    def _move_to_review(self, on_done: Callable[[], None]) -> None:
        self._monitor_mw_state_change(on_done)
        aqt.mw.moveToState("review")

    def _move_to_deck_browser(self, on_done: Callable[[], None]) -> None:
        self._monitor_mw_state_change(on_done)
        aqt.mw.moveToState("deckBrowser")

    @cached_property
    def steps(self) -> list[TutorialStep]:
        steps = [
            TutorialStep(
                body="<b>Decks</b> is the main page, "
                "where you will find both your local decks and the ones you subscribed to.",
                target="#decks",
                tooltip_context=aqt.mw.deckBrowser,
                target_context=aqt.mw.toolbar,
                block_target_click=True,
                remove_parent_backdrop=True,
            )
        ]

        intro_deck_config = config.deck_config(config.intro_deck_id)
        if intro_deck_config:
            steps.append(
                TutorialStep(
                    body="We've already subscribed you to this deck. Click on it to open.",
                    target=f"[id='{intro_deck_config.anki_id}']",
                    click_target=lambda: f"[id='{intro_deck_config.anki_id}'] a.deck",
                    tooltip_context=aqt.mw.deckBrowser,
                    next_callback=self._move_to_intro_deck_overview,
                )
            )
        else:

            def on_sync_with_ankihub_done(on_done: Callable[[], None], future: Future) -> None:
                future.result()
                if not config.deck_config(config.intro_deck_id):
                    self.end()
                    tooltip("Tour canceled.")
                    return
                on_done()

            def subscribe_to_intro_deck() -> None:
                client = AnkiHubClient()
                client.subscribe_to_deck(config.intro_deck_id)

            def on_subscribed_to_intro_deck(on_done: Callable[[], None]) -> None:
                from ..gui.operations.ankihub_sync import sync_with_ankihub

                sync_with_ankihub(partial(on_sync_with_ankihub_done, on_done), skip_summary=True)

            def on_sync_with_ankihub_button_clicked(on_done: Callable[[], None]) -> None:
                AddonQueryOp(
                    parent=aqt.mw,
                    op=lambda _: subscribe_to_intro_deck(),
                    success=lambda _: on_subscribed_to_intro_deck(on_done),
                ).with_progress().run_in_background()

            steps.append(
                TutorialStep(
                    body="The <b>Getting Started with Anki</b> deck is not installed, "
                    "but we’ve already subscribed you to it.<br><br>"
                    "To make a deck you are subscribed to appear here, "
                    "select Anki menu > AnkiHub > Sync with AnkiHub.<br><br>"
                    "Right now you can just <b>click the sync button below</b>.",
                    target="center table",
                    tooltip_context=aqt.mw.deckBrowser,
                    next_callback=on_sync_with_ankihub_button_clicked,
                    next_label="Sync with AnkiHub",
                    block_target_click=True,
                )
            )

            steps.append(
                TutorialStep(
                    body="You now have the deck <b>Getting Started with Anki</b> installed."
                    "<br><br>Click on it to open.",
                    target=lambda: f"[id='{config.deck_config(config.intro_deck_id).anki_id}']",
                    click_target=lambda: f"[id='{config.deck_config(config.intro_deck_id).anki_id}'] a.deck",
                    tooltip_context=aqt.mw.deckBrowser,
                    next_callback=self._move_to_intro_deck_overview,
                )
            )

        steps.append(
            TutorialStep(
                body="This deck will help you understand the basics of card reviewing.",
                target="",
                tooltip_context=aqt.mw.overview,
                back_callback=self._move_to_deck_browser,
            )
        )
        steps.append(
            TutorialStep(
                "These daily stats show you:<br><ul>"
                "<li><b class='text-text-information-main'>New</b>: cards that you have downloaded or created yourself,"
                " but have never studied before</li>"
                "<li><b class='text-text-destructive-main'>Learning</b>: cards that were seen "
                "for the first time recently, and are still being learned</li>"
                "<li><b class='text-text-confirmation-main'>To Review</b>: cards that you have finished learning. "
                "They will be shown again after their delay has elapsed</li></ul>",
                target="td",
                tooltip_context=aqt.mw.overview,
            )
        )

        steps.append(
            TutorialStep(
                "Click this button and start practicing card reviewing now!",
                target="#study",
                click_target="#study",
                tooltip_context=aqt.mw.overview,
                next_callback=self._move_to_review,
            )
        )
        return steps


class StepDeckTutorial(DeckBrowserOverviewBackdropMixin, Tutorial):
    def __init__(self) -> None:
        super().__init__()
        self.anking_deck_config = config.deck_config(config.anking_deck_id)
        assert self.anking_deck_config is not None
        self.deckoptions: Optional[DeckOptionsDialog] = None
        self.browser: Optional[Browser] = None

    @ensure_mw_state("deckBrowser")
    def start(self) -> None:
        base_start = super().start
        set_current_deck(parent=aqt.mw, deck_id=self.anking_deck_config.anki_id).success(
            lambda _: base_start()
        ).run_in_background()

    def end(self) -> None:
        config.set_step_deck_tutorial_pending(False)
        return super().end()

    def on_deck_options_did_load(self, deckoptions: DeckOptionsDialog) -> None:
        self.deckoptions = deckoptions
        self.next()

    def on_gears_icon_step(self, step: TutorialStep) -> None:
        gui_hooks.deck_options_did_load.append(self.on_deck_options_did_load)

    def on_gears_icon_step_hidden(self) -> None:
        gui_hooks.deck_options_did_load.remove(self.on_deck_options_did_load)

    def on_deckoptions_step(self, step: TutorialStep) -> None:
        if not self.deckoptions:
            self.deckoptions = DeckOptionsDialog(aqt.mw, aqt.mw.col.decks.get(self.anking_deck_config.anki_id))
        step.tooltip_context = self.deckoptions
        step.target_context = self.deckoptions

    def on_deckoptions_next(self, on_done: Callable[[], None]) -> None:
        self.deckoptions.web.eval("document.querySelector('.save').click()")
        on_done()

    def is_step_sidebar_item(self, item: SidebarItem) -> bool:
        return item.id == self.anking_deck_config.anki_id and item.item_type != SidebarItemType.DECK_CURRENT

    def find_step_deck_sidebar_item(self, root: SidebarItem) -> SidebarItem:
        for child in root.children:
            if child.item_type == SidebarItemType.DECK_ROOT:
                for grandchild in child.children:
                    if self.is_step_sidebar_item(grandchild):
                        grandchild.search(self.anking_deck_config.name)
                        return grandchild

        raise RuntimeError("Sidebar item for Step deck not found")

    def get_step_deck_sidebar_item_rect(self) -> OverlayTarget:
        sidebar = self.browser.sidebar
        model = sidebar.model()
        step_sidebar_item = self.find_step_deck_sidebar_item(model.root)
        idx = model.index_for_item(step_sidebar_item)
        rect = sidebar.visualRect(idx)
        return OverlayTarget(sidebar, rect)

    def get_tags_sidebar_item(self) -> OverlayTarget:
        sidebar = self.browser.sidebar
        model = sidebar.model()
        for child in model.root.children:
            if child.item_type == SidebarItemType.TAG_ROOT:
                sidebar.collapseAll()
                idx = model.index_for_item(child)
                sidebar.expand(idx)
                sidebar.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                rect = sidebar.visualRect(idx)
                return OverlayTarget(sidebar, rect)
        raise RuntimeError("Sidebar item for Tags not found")

    def open_browser_and_move_to_next_step(self, on_done: Callable[[], None]) -> None:
        timer: Optional[QTimer] = None

        def clear_sidebar_highlight(root: SidebarItem) -> None:
            for child in root.children:
                if child._search_matches_self and not self.is_step_sidebar_item(child):
                    child._search_matches_self = False
                if child._search_matches_child:
                    clear_sidebar_highlight(child)

        def wrapped_on_done(root: SidebarItem) -> None:
            self.browser.sidebar.search_for(self.anking_deck_config.name)
            clear_sidebar_highlight(self.browser.sidebar.model().root)
            step_sidebar_item = self.find_step_deck_sidebar_item(root)
            search = aqt.mw.col.build_search_string(step_sidebar_item.search_node)
            self.browser.search_for(search)
            on_done()

        def _build_deck_tree(*args: Any, **kwargs: Any) -> None:
            _old: Callable[[SidebarItem], None] = kwargs.pop("_old")
            args, kwargs, root = extract_argument(func=_old, args=args, kwargs=kwargs, arg_name="root")
            _old(*args, **kwargs, root=root)

            def on_main() -> None:
                # There can be multiple sidebar refresh events at browser startup,
                # so we need to ensure we only call .next() once
                nonlocal timer
                if timer and not sip.isdeleted(timer):
                    timer.deleteLater()
                    timer = None
                timer = aqt.mw.progress.timer(
                    1000, functools.partial(wrapped_on_done, root=root), repeat=False, parent=self.browser
                )
                SidebarTreeView._deck_tree = original_deck_tree

            aqt.mw.taskman.run_on_main(on_main)

        original_deck_tree = SidebarTreeView._deck_tree
        SidebarTreeView._deck_tree = wrap(SidebarTreeView._deck_tree, _build_deck_tree, "around")
        self.browser = aqt.dialogs.open("Browser", aqt.mw)

    def unsuspend_cards_and_move_to_next_step(self, on_done: Callable[[], None]) -> None:
        nids = [
            1500401546879,
            1500401591194,
            1470839334989,
            1470839322331,
            1470839316273,
            1470839294833,
            1470839280347,
            1550661266842,
            1472161006747,
            1478831098355,
            1474154340790,
            1472426521266,
            1472426529103,
            1474224658849,
            1482115345843,
            1482021715220,
            1484686747922,
            1462992514871,
            1485913667420,
            1485913618153,
            1462326105448,
            1462326951145,
            1608908268526,
            1518568098631,
            1482361493392,
            1474509558350,
            1502065388242,
            1488680344608,
            1483930816351,
            1476583175080,
            1478834028289,
            1540336057514,
            1480908234945,
            1478833957640,
        ]
        cids = set()
        for nid in nids:
            cids.update(aqt.mw.col.card_ids_of_note(nid))

        def success(_):
            self.browser.close()
            on_done()

        unsuspend_cards(parent=self.browser, card_ids=cids).success(success).run_in_background()

    def _steps(self) -> list[TutorialStep]:
        steps = []
        steps.append(
            TutorialStep(
                body="Click on the deck’s gear icon and select <b>Options</b>.",
                target=f"[id='{self.anking_deck_config.anki_id}'] .opts",
                tooltip_context=aqt.mw.deckBrowser,
                shown_callback=self.on_gears_icon_step,
                hidden_callback=self.on_gears_icon_step_hidden,
            )
        )

        steps.append(
            TutorialStep(
                body="Here, you can set your daily limits.<br><br>"
                "<b>Recommended:</b><br>"
                "maximum reviews = 10x new cards.<br><br>"
                "Ex. If you plan to study 10 new cards daily, set your maximum reviews to 100 per day.<br><br>"
                "Click <b>Next</b> when you're done.",
                # NOTE: This assumes Daily Limits is the first section.
                # We should add section IDs to Anki
                target=".row",
                shown_callback=self.on_deckoptions_step,
                next_callback=self.on_deckoptions_next,
            )
        )

        steps.append(
            TutorialStep(
                body="Now click on <b>Browse</b>.",
                target="#browse",
                tooltip_context=aqt.mw.deckBrowser,
                target_context=aqt.mw.toolbar,
                next_callback=self.open_browser_and_move_to_next_step,
                remove_parent_backdrop=True,
            )
        )

        steps.append(
            QtTutorialStep(
                body="On <b>Browse</b>, you’ll find all cards from your decks.<br><br>"
                "When the <b>AnKing Step Deck</b> is selected, only its cards are shown.",
                qt_target=self.get_step_deck_sidebar_item_rect,
                parent_widget=lambda: self.browser,
            )
        )

        steps.append(
            QtTutorialStep(
                body="You can find specific cards to study using a few methods.<br><br>"
                "One of them is typing terms on the <b>search bar</b>.",
                qt_target=lambda: OverlayTarget(self.browser, self.browser.form.searchEdit),
                parent_widget=lambda: self.browser,
            )
        )

        steps.append(
            QtTutorialStep(
                body="You can also use <b>tags</b> to find specific sets of cards.",
                qt_target=self.get_tags_sidebar_item,
                parent_widget=lambda: self.browser,
            )
        )

        smart_search_link = render_link(f'javascript:pycmd("{FLASHCARD_SELECTOR_OPEN_PYCMD}")', "Smart Search")
        body = (
            f"Or use our AI {smart_search_link}, "
            "a Premium feature that helps you search decks for cards"
            " that match your study materials (lecture notes, PDFs, study aids, and more)."
        )
        steps.append(
            QtTutorialStep(
                body=body,
                qt_target=lambda: OverlayTarget(
                    self.browser.sidebar, self.browser.findChild(QToolButton, "AnkiHubSmartSearchButton")
                ),
                parent_widget=lambda: self.browser,
            )
        )

        media_base = f"/_addons/{aqt.mw.addonManager.addonFromModule(__name__)}/gui/web/media"
        steps.append(
            QtTutorialStep(
                body="Suspended cards won't appear in study sessions. In the Browser, "
                "they're shown with a <span style='background-color: #FFE77E'>yellow background</span>.<br><br>"
                "During the tour, you can’t perform actions. Normally, to unsuspend a card, "
                "you would right-click it and uncheck <b>Toggle Suspend</b>.<br><br>"
                "Click Next and we'll unsuspend a card for you as an example.<br><br>"
                f"<img src='{media_base}/toggle_suspend.png'>",
                qt_target=lambda: self.browser.form.tableView,
                parent_widget=lambda: self.browser,
                target_outline=False,
                next_callback=self.unsuspend_cards_and_move_to_next_step,
            )
        )

        forum_link = render_link("https://community.ankihub.net/c/support/5", "forum")
        steps.append(
            TutorialStep(
                body="We've unsuspended some cards for you and they are ready for study. Check them out, "
                "then try selecting cards on your own!<br><br>"
                f"<b>Need help?</b> Post in the {forum_link} and our support team will be happy to assist.",
                target=f"[id='{self.anking_deck_config.anki_id}']",
                click_target=f"[id='{self.anking_deck_config.anki_id}'] a.deck",
                tooltip_context=aqt.mw.deckBrowser,
                next_label="End tour",
                back_label="Restart tour",
                back_callback=lambda _: self.restart(),
                close_button=False,
            )
        )

        return steps

    @cached_property
    def steps(self) -> list[TutorialStep]:
        steps = self._steps()
        # Hide back button for all but the last step
        for step in steps[:-1]:
            step.back_label = ""
        return steps
