import json
from concurrent.futures import Future
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Callable, Optional, Set, Type, Union

import aqt
from aqt import dialogs, gui_hooks
from aqt.browser.browser import Browser
from aqt.editor import Editor
from aqt.main import AnkiQt, MainWindowState
from aqt.overview import Overview, OverviewBottomBar
from aqt.qt import (
    QPoint,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.reviewer import Reviewer, ReviewerBottomBar
from aqt.toolbar import BottomBar, Toolbar, TopToolbar
from aqt.webview import AnkiWebView

from ..gui.overlay_dialog import OverlayDialog
from ..settings import config

PRIMARY_BUTTON_CLICKED_PYCMD = "ankihub_tutorial_primary_button_clicked"
TARGET_RESIZE_PYCMD = "ankihub_tutorial_target_resize"
MODAL_CLOSED_PYCMD = "ankihub_modal_closed"
JS_LOADED_PYCMD = "ankihub_tutorial_js_loaded"


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


def inject_tutorial_assets(context: Any, on_loaded: Callable[[], None]) -> None:
    def on_webview_did_receive_js_message(handled: tuple[bool, Any], message: str, context: Any) -> tuple[bool, Any]:
        if message == JS_LOADED_PYCMD:
            # Some delay is required here before the global AnkiHub object is available for some reason
            aqt.mw.progress.single_shot(100, on_loaded)
            gui_hooks.webview_did_receive_js_message.remove(on_webview_did_receive_js_message)
            return True, None
        return handled

    gui_hooks.webview_did_receive_js_message.append(on_webview_did_receive_js_message)

    web = webview_for_context(context)
    web_base = f"/_addons/{aqt.mw.addonManager.addonFromModule(__name__)}/gui/web"
    web.eval(
        """
(() => {
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
        js.addEventListener("load", () => pycmd(%(loaded_cmd)s));
        document.body.appendChild(js);
        js.src = %(js_path)s;
    }
    else {
        pycmd(%(loaded_cmd)s);
    }
})();
"""
        % dict(
            css_path=json.dumps(f"{web_base}/lib/tutorial.css"),
            js_path=json.dumps(f"{web_base}/lib/tutorial.js"),
            loaded_cmd=json.dumps(JS_LOADED_PYCMD),
        )
    )


@dataclass
class TutorialStep:
    body: str
    target: Optional[Union[str, Callable[[], str]]] = ""
    tooltip_context: Optional[Any] = None
    target_context: Optional[Any] = None
    parent_widget: Optional[QWidget] = None
    qt_target: Optional[QWidget] = None
    shown_callback: Optional[Callable[[], None]] = None
    hidden_callback: Optional[Callable[[], None]] = None
    show_primary_button: bool = True
    primary_button_label: str = "Next"
    button_callback: Optional[Callable[[], None]] = None
    block_target_click: bool = False

    def __post_init__(self):
        if not self.target_context:
            self.target_context = self.tooltip_context


active_tutorial: Optional["Tutorial"] = None


class Tutorial:
    def __init__(self) -> None:
        self.name = ""
        self.current_step = 1
        self._loadded_context_types: Set[Type[Any]] = set()
        self._show_timer: Optional[QTimer] = None
        self.apply_backdrop = True

    @property
    def steps(self) -> list[TutorialStep]:
        return []

    @property
    def extra_backdrop_contexts(self) -> tuple[Any, ...]:
        return tuple()

    def _render_js_function_with_options(self, function: str, options: dict[str, Any]) -> str:
        return f"AnkiHub.{function}({{" + ",".join(f"{k}: {json.dumps(v)}" for k, v in options.items()) + "})"

    def _render_tooltip(self, eval_js: bool = True) -> str:
        step = self.steps[self.current_step - 1]
        if step.shown_callback:
            step.shown_callback()
        tooltip_web = webview_for_context(step.tooltip_context)
        tooltip_options = {
            "body": step.body,
            "currentStep": self.current_step,
            "stepCount": len(self.steps),
            "blockTargetClick": step.block_target_click,
            "primaryButton": {
                "show": step.show_primary_button,
                "label": step.primary_button_label,
            },
            "backdrop": self.apply_backdrop,
        }
        if step.target and step.tooltip_context == step.target_context:
            tooltip_options["target"] = step.target if isinstance(step.target, str) else step.target()
        js = self._render_js_function_with_options("showTutorialModal", tooltip_options)
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
                },
            )
            if eval_js:
                target_web.eval(js)
        return js

    def _backdrop_js(self) -> str:
        return "AnkiHub.addTutorialBackdrop()"

    def _render_backdrop(self) -> None:
        step = self.steps[self.current_step - 1]
        tooltip_web = webview_for_context(step.tooltip_context)
        target_web = webview_for_context(step.target_context)
        for context in self.extra_backdrop_contexts:
            web = webview_for_context(context)
            if web not in (tooltip_web, target_web):
                web.eval(self._backdrop_js())

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
        self.show_current()

    def end(self) -> None:
        gui_hooks.webview_did_receive_js_message.remove(self._on_webview_did_receive_js_message)
        last_step = self.steps[self.current_step - 1]
        for context in (
            last_step.target_context,
            *self.extra_backdrop_contexts,
        ):
            webview_for_context(context).eval("if(typeof AnkiHub !== 'undefined') AnkiHub.destroyActiveTutorialModal()")
        if last_step.hidden_callback:
            last_step.hidden_callback()
        global active_tutorial
        active_tutorial = None

    def _on_webview_did_receive_js_message(
        self, handled: tuple[bool, Any], message: str, context: Any
    ) -> tuple[bool, Any]:
        if message == PRIMARY_BUTTON_CLICKED_PYCMD:
            step = self.steps[self.current_step - 1]
            if step.button_callback:
                step.button_callback()
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

                js = f"AnkiHub.positionTutorialTarget({{top: {top}, left: {left}, width: {width}, height: {height}}});"
                tooltip_web.eval(js)
            return True, None
        elif message == MODAL_CLOSED_PYCMD:
            self.end()
            return True, None
        return handled

    def next(self) -> None:
        step = self.steps[self.current_step - 1]
        for web in (
            webview_for_context(step.tooltip_context),
            webview_for_context(step.target_context),
            *[webview_for_context(context) for context in self.extra_backdrop_contexts],
        ):
            web.eval("if(typeof AnkiHub !== 'undefined') AnkiHub.destroyActiveTutorialModal()")

        if step.hidden_callback:
            step.hidden_callback()
        if self.current_step >= len(self.steps):
            self.end()
            return
        self.current_step += 1

        self.show_current()


class OnboardingTutorial(Tutorial):
    def __init__(self) -> None:
        super().__init__()

    @cached_property
    def steps(self) -> list[TutorialStep]:
        steps = [
            TutorialStep(
                body="<b>Decks</b> is where you will find your subscribed decks.",
                target="#decks",
                tooltip_context=aqt.mw.deckBrowser,
                target_context=aqt.mw.toolbar,
                block_target_click=True,
            )
        ]

        def on_overview_did_refresh(overview: Overview) -> None:
            aqt.mw.progress.single_shot(100, self.next)

        def on_intro_step_shown() -> None:
            gui_hooks.overview_did_refresh.append(on_overview_did_refresh)

        def on_intro_step_hidden() -> None:
            gui_hooks.overview_did_refresh.remove(on_overview_did_refresh)

        intro_deck_config = config.deck_config(config.intro_deck_id)
        if intro_deck_config:
            steps.append(
                TutorialStep(
                    body="We've already subscribed you to this deck.<br><br>Click on it to open.",
                    target=f"[id='{intro_deck_config.anki_id}']",
                    tooltip_context=aqt.mw.deckBrowser,
                    shown_callback=on_intro_step_shown,
                    hidden_callback=on_intro_step_hidden,
                    show_primary_button=False,
                )
            )
        else:

            def on_sync_with_ankihub_done(future: Future) -> None:
                future.result()
                self.next()

            def on_sync_with_ankihub_button_clicked() -> None:
                from ..gui.operations.ankihub_sync import sync_with_ankihub

                sync_with_ankihub(on_sync_with_ankihub_done)

            steps.append(
                TutorialStep(
                    body="There is no deck here, but we've already subscribed you to "
                    "the <b>Getting Started with Anki</b> deck.<br><br>"
                    "To make a deck you are subscribed to appear here, "
                    "select Anki menu > AnkiHub > Sync with AnkiHub.<br><br>"
                    "Right now you can just <b>click on the button bellow</b>.",
                    target=".deck",
                    tooltip_context=aqt.mw.deckBrowser,
                    show_primary_button=True,
                    primary_button_label="Sync with AnkiHub",
                    button_callback=on_sync_with_ankihub_button_clicked,
                )
            )
            steps.append(
                TutorialStep(
                    body="You now have the deck <b>Getting Started with Anki</b> installed. Click on it to open.",
                    target=lambda: f"[id='{config.deck_config(config.intro_deck_id).anki_id}']",
                    tooltip_context=aqt.mw.deckBrowser,
                    shown_callback=on_intro_step_shown,
                    hidden_callback=on_intro_step_hidden,
                    show_primary_button=False,
                )
            )

        steps.append(
            TutorialStep(
                body="This deck will help you understand the basics of card reviewing.",
                target="",
                tooltip_context=aqt.mw.overview,
            )
        )
        steps.append(
            TutorialStep(
                "These daily stats show you:<br><ul>"
                "<li><b>New</b>: new cards to study</li>"
                "<li><b>Learning</b>: reviewed cards on short delay to come back</li>"
                "<li><b>To Review</b>: reviewed cards on long delay to come back</li></ul>",
                target="td",
                tooltip_context=aqt.mw.overview,
            )
        )

        def on_state_did_change(new_state: MainWindowState, old_state: MainWindowState) -> None:
            if new_state == "review":
                self.next()

        def on_study_step_shown() -> None:
            gui_hooks.state_did_change.append(on_state_did_change)

        def on_study_step_hidden() -> None:
            gui_hooks.state_did_change.remove(on_state_did_change)

        steps.append(
            TutorialStep(
                "Click this button and start practicing card reviewing now!",
                target="#study",
                tooltip_context=aqt.mw.overview,
                shown_callback=on_study_step_shown,
                hidden_callback=on_study_step_hidden,
                show_primary_button=False,
            )
        )
        return steps

    @property
    def extra_backdrop_contexts(self) -> tuple[Any, ...]:
        return (aqt.mw.deckBrowser, aqt.mw.overview, aqt.mw.deckBrowser.bottom, aqt.mw.toolbar)


def prompt_for_onboarding_tutorial() -> None:
    if active_tutorial or not config.get_feature_flags().get("addon_tours", False):
        return

    inject_tutorial_assets(aqt.mw, lambda: aqt.mw.web.eval("AnkiHub.promptForOnboardingTour()"))


class QtTutorialDemo(Tutorial):
    def __init__(self) -> None:
        super().__init__()
        self.apply_backdrop = True
        self.browser: Browser

    def start(self) -> None:
        self.browser = dialogs.open("Browser", aqt.mw)
        return super().start()

    @cached_property
    def steps(self) -> list[TutorialStep]:
        return [
            TutorialStep(
                "Notes list",
                parent_widget=self.browser,
                qt_target=self.browser.form.tableView,
            ),
            TutorialStep(
                "Editor",
                parent_widget=self.browser,
                qt_target=self.browser.form.fieldsArea,
            ),
            TutorialStep(
                "View on AnkiHub",
                tooltip_context=self.browser.editor,
                target="#ankihub-btn-view-note",
            ),
            TutorialStep(
                "Sidebar",
                parent_widget=self.browser,
                qt_target=self.browser.sidebar.searchBar,
            ),
        ]
