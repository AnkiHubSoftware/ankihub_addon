import functools
import json
from asyncio.futures import Future
from dataclasses import dataclass
from functools import cached_property, partial
from typing import Any, Callable, Optional, Union

import aqt
from aqt import gui_hooks
from aqt.editor import Editor
from aqt.main import AnkiQt
from aqt.overview import Overview, OverviewBottomBar
from aqt.qt import (
    QPoint,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.reviewer import Reviewer, ReviewerBottomBar
from aqt.toolbar import BottomBar, Toolbar, TopToolbar
from aqt.utils import tooltip
from aqt.webview import AnkiWebView, WebContent

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..django import render_template, render_template_from_string
from ..gui.overlay_dialog import OverlayDialog
from ..settings import config
from .operations import AddonQueryOp

START_ONBOARDING_PYCMD = "ankihub_start_onboarding"
DISMISS_ONBOARDING_PYCMD = "ankihub_dismiss_onboarding"
SHOW_LATER_PYCMD = "ankihub_show_later"
NEXT_STEP_PYCMD = "ankihub_tutorial_next_step"
PREV_STEP_PYCMD = "ankihub_tutorial_prev_step"
TARGET_RESIZE_PYCMD = "ankihub_tutorial_target_resize"
TUTORIAL_CLOSED_PYCMD = "ankihub_tutorial_closed"
JS_LOADED_PYCMD = "ankihub_tutorial_js_loaded"
TARGET_CLICK_PYCMD = "ankihub_tutorial_target_click"


def render_dialog(
    title: str,
    body: str,
    text_button_label: Optional[str] = None,
    secondary_button_label: Optional[str] = None,
    main_button_label: Optional[str] = None,
    on_text_button_click: Optional[str] = None,
    on_secondary_button_click: Optional[str] = None,
    on_main_button_click: Optional[str] = None,
    on_close: Optional[str] = None,
) -> str:
    return render_template(
        "dialog.html",
        {
            "title": title,
            "body": body,
            "text_button_label": text_button_label,
            "secondary_button_label": secondary_button_label,
            "main_button_label": main_button_label,
            "on_text_button_click": on_text_button_click,
            "on_secondary_button_click": on_secondary_button_click,
            "on_main_button_click": on_main_button_click,
            "on_close": on_close,
        },
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
            "class": "ah-tour-step",
        },
    )


def render_arrow() -> str:
    return render_template("arrow.html")


def render_backdrop() -> str:
    return render_template_from_string("<c-v1.backdrop :open=True />")


def get_backdrop_js() -> str:
    return f"AnkiHub.addTutorialBackdrop({json.dumps(render_backdrop())});"


class TutorialOverlayDialog(OverlayDialog):
    def setup_ui(self) -> None:
        vbox = QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        self.setLayout(vbox)
        self.web = AnkiWebView(self)
        self.web.disable_zoom()
        self.web.set_bridge_command(self.web.defaultOnBridgeCmd, self)
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

    def on_position(self) -> None:
        super().on_position()
        target = self.target
        geom = target.contentsRect()
        target_global_top_left = target.mapToGlobal(geom.topLeft())
        target_global_bottom_right = target.mapToGlobal(geom.bottomRight())
        webview_top_left = self.web.mapFromGlobal(target_global_top_left)
        webview_bottom_right = self.web.mapFromGlobal(target_global_bottom_right)
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
    target.style.top = '%(top)dpx';
    target.style.left = '%(left)dpx';
    target.style.width = '%(width)dpx';
    target.style.height = '%(height)dpx';
})();
        """
            % dict(top=top, left=left, width=width, height=height)
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
    parent_widget: Optional[QWidget] = None
    qt_target: Optional[QWidget] = None
    shown_callback: Optional[Callable[[], None]] = None
    hidden_callback: Optional[Callable[[], None]] = None
    next_callback: Optional[Callable[[Callable[[], None]], None]] = None
    next_label: str = "Next"
    back_callback: Optional[Callable[[Callable[[], None]], None]] = None
    back_label: str = "Back"
    block_target_click: bool = False
    auto_advance: bool = True

    def __post_init__(self):
        if not self.target_context:
            self.target_context = self.tooltip_context


active_tutorial: Optional["Tutorial"] = None


class Tutorial:
    def __init__(self) -> None:
        self.name = ""
        self.current_step = 1
        self.apply_backdrop = True

    @property
    def steps(self) -> list[TutorialStep]:
        return []

    @property
    def extra_backdrop_contexts(self) -> tuple[Any, ...]:
        return tuple()

    def extra_backdrop_context_types(self) -> tuple[Any, ...]:
        """Similar to `extra_backdrop_contexts`,
        but returns the types of the contexts instead of the contexts themselves.
        Required to handle DeckBrowserBottomBar and OverviewBottomBar,
        which Anki doesn't keep a reference to their instances.
        """
        return tuple(type(context) for context in self.extra_backdrop_contexts)

    def _render_js_function_with_options(self, function: str, options: dict[str, Any]) -> str:
        return on_ankihub_loaded_js(
            f"AnkiHub.{function}({{" + ",".join(f"{k}: {json.dumps(v)}" for k, v in options.items()) + "})"
        )

    def _render_tooltip(self, eval_js: bool = True) -> str:
        step = self.steps[self.current_step - 1]
        if step.shown_callback:
            step.shown_callback()
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
            show_backdrop=self.apply_backdrop,
        )
        arrow = render_arrow() if step.target else ""
        tooltip_options = {
            "modal": modal,
            "arrow": arrow,
            "blockTargetClick": step.block_target_click,
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
        for context in self.extra_backdrop_contexts:
            web = webview_for_context(context)
            if web not in (tooltip_web, target_web):
                webviews.add(web)
        for web in webviews:
            web.eval(backdrop_js)

    def show_current(self) -> None:
        step = self.steps[self.current_step - 1]
        if step.qt_target:
            overlay = TutorialOverlayDialog(step.parent_widget, step.qt_target)
            overlay.show()
            step.tooltip_context = overlay
            step.target_context = overlay
            step.target = "#target"

            def close_overlay() -> None:
                overlay.close()

            step.hidden_callback = close_overlay

        contexts = []
        webviews = set()
        for context in [step.tooltip_context, step.target_context, *self.extra_backdrop_contexts]:
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

    def _finalize_tutorial(self) -> None:
        gui_hooks.webview_did_receive_js_message.remove(self._on_webview_did_receive_js_message)
        gui_hooks.webview_will_set_content.remove(self._on_webview_will_set_content)
        global active_tutorial
        active_tutorial = None

    def end(self) -> None:
        self._cleanup_step()
        self._finalize_tutorial()
        config.set_onboarding_tutorial_pending(False)

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
        elif context in self.extra_backdrop_contexts or isinstance(context, self.extra_backdrop_context_types()):
            js = get_backdrop_js()
        if js:
            js = tutorial_assets_js(js)
            web_content.body += f"<script>{js}</script>"

    def _cleanup_step(self, destroy_effect: bool = True) -> None:
        step = self.steps[self.current_step - 1]
        if destroy_effect:
            webviews = set()
            for context in (step.tooltip_context, step.target_context, *self.extra_backdrop_contexts):
                webviews.add(webview_for_context(context))
            for web in webviews:
                web.eval("if(typeof AnkiHub !== 'undefined') AnkiHub.destroyActiveTutorialEffect()")

        if step.hidden_callback:
            step.hidden_callback()

    def back(self) -> None:
        if self.current_step == 1:
            self.end()
            return
        self._cleanup_step(destroy_effect=False)
        self.current_step -= 1
        self.show_current()

    def next(self) -> None:
        if self.current_step >= len(self.steps):
            self.end()
            return
        self._cleanup_step(destroy_effect=False)
        self.current_step += 1
        self.show_current()


def ensure_mw_state(state: str) -> Callable[[...], None]:
    def change_state_and_call_func(func: Callable[[...], None], *args: Any, **kwargs: Any) -> None:
        from aqt.main import MainWindowState

        def on_state_did_change(old_state: MainWindowState, new_state: MainWindowState) -> None:
            gui_hooks.state_did_change.remove(on_state_did_change)
            # Some delay appears to be required for the toolbar
            aqt.mw.progress.single_shot(100, lambda: func(*args, **kwargs))

        gui_hooks.state_did_change.append(on_state_did_change)
        aqt.mw.moveToState(state)

    def decorated_func(func: Callable[[...], None]) -> Callable[[...], None]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            if aqt.mw.state != state:
                change_state_and_call_func(func, *args, **kwargs)
            else:
                func(*args, **kwargs)

        return wrapper

    return decorated_func


def prompt_for_onboarding_tutorial() -> None:
    from aqt.deckbrowser import DeckBrowser, DeckBrowserBottomBar

    if active_tutorial or not config.get_feature_flags().get("addon_tours", True):
        return

    config.set_onboarding_tutorial_pending(True)

    context_types = (DeckBrowser, DeckBrowserBottomBar, TopToolbar)
    contexts = (aqt.mw.deckBrowser, aqt.mw.deckBrowser.bottom, aqt.mw.toolbar)

    def js_for_context(context: Any) -> str:
        if isinstance(context, DeckBrowser):
            body = render_dialog(
                title="📚 First time with Anki?",
                body="Find your way in the app with this <b>onboarding tour</b>.<br>"
                "You can revisit it anytime in AnkiHub's Help menu.",
                secondary_button_label="Maybe later",
                main_button_label="Take tour",
                on_main_button_click=f"pycmd('{START_ONBOARDING_PYCMD}')",
                on_secondary_button_click=f"pycmd('{SHOW_LATER_PYCMD}')",
                on_close=f"pycmd('{DISMISS_ONBOARDING_PYCMD}')",
            )
            js = f"AnkiHub.showModal({json.dumps(body)})"
        else:
            js = get_backdrop_js()
        return on_ankihub_loaded_js(js)

    def on_webview_did_receive_js_message(handled: tuple[bool, Any], message: str, context: Any) -> tuple[bool, Any]:
        if message == START_ONBOARDING_PYCMD:
            remove_hooks()
            OnboardingTutorial().start()
            return True, None
        if message == DISMISS_ONBOARDING_PYCMD:
            clean_up_webviews()
            config.set_onboarding_tutorial_pending(False)
            return True, None
        if message == SHOW_LATER_PYCMD:
            clean_up_webviews()
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
        for web in (aqt.mw.web, aqt.mw.bottomWeb, aqt.mw.toolbar.web):
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


def prompt_for_pending_onboarding_tutorial() -> None:
    if config.onboarding_tutorial_pending():
        prompt_for_onboarding_tutorial()


def setup() -> None:
    gui_hooks.main_window_did_init.append(prompt_for_pending_onboarding_tutorial)


class OnboardingTutorial(Tutorial):
    @ensure_mw_state("deckBrowser")
    def start(self) -> None:
        return super().start()

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

    @property
    def extra_backdrop_contexts(self) -> tuple[Any, ...]:
        return (aqt.mw.deckBrowser, aqt.mw.overview, aqt.mw.deckBrowser.bottom, aqt.mw.toolbar, aqt.mw.overview.bottom)

    def extra_backdrop_context_types(self) -> tuple[Any, ...]:
        from aqt.deckbrowser import DeckBrowserBottomBar

        return (*super().extra_backdrop_context_types(), DeckBrowserBottomBar, OverviewBottomBar)
