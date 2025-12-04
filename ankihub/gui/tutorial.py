import json
from concurrent.futures import Future
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Callable, Optional, Set, Tuple, Type, Union, cast

import aqt
from aqt import dialogs, gui_hooks
from aqt.browser.browser import Browser
from aqt.main import MainWindowState
from aqt.overview import Overview, OverviewBottomBar, OverviewContent
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
from aqt.webview import AnkiWebView, WebContent

from ..gui.overlay_dialog import OverlayDialog
from ..settings import config

PRIMARY_BUTTON_CLICKED_PYCMD = "ankihub_tutorial_primary_button_clicked"
TARGET_RESIZE_PYCMD = "ankihub_tutorial_target_resize"
MODAL_CLOSED_PYCMD = "ankihub_modal_closed"


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

    if isinstance(context, (DeckBrowser, Reviewer, Overview)):
        return aqt.mw.web
    if isinstance(context, (BottomBar, DeckBrowserBottomBar, OverviewBottomBar, ReviewerBottomBar)):
        return aqt.mw.bottomWeb
    if isinstance(context, (Toolbar, TopToolbar)):
        return aqt.mw.toolbar.web
    if isinstance(context, (TutorialOverlayDialog,)):
        return context.web
    else:
        assert False, f"Webview context of type {type(context)} is not handled"


@dataclass
class TutorialStep:
    body: str
    target: Optional[Union[str, Callable[[], str]]]
    tooltip_context: Any
    target_context: Optional[Any] = None
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
    def contexts(self) -> Tuple[Any, ...]:
        return tuple()

    @property
    def steps(self) -> list[TutorialStep]:
        return []

    @property
    def extra_backdrop_contexts(self) -> tuple[Any, ...]:
        return tuple()

    @property
    def initial_contexts(self) -> Tuple[Any, ...]:
        return tuple()

    def refresh_initial_webviews(self) -> None:
        pass

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
        if not step.target:
            tooltip_options["showArrow"] = False
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
        self._render_tooltip()
        self._render_highlight()
        self._render_backdrop()

    def start(self) -> None:
        global active_tutorial
        active_tutorial = self
        gui_hooks.webview_will_set_content.append(self._on_webview_will_set_content)
        gui_hooks.webview_did_receive_js_message.append(self._on_webview_did_receive_js_message)

        initial_contexts = self.initial_contexts
        initial_contexts_loaded = 0

        def _on_initial_webview_will_set_content(web_content: WebContent, context: Optional[object] = None) -> None:
            if isinstance(context, initial_contexts):
                nonlocal initial_contexts_loaded
                initial_contexts_loaded += 1
            if initial_contexts_loaded == len(initial_contexts):
                gui_hooks.webview_will_set_content.remove(_on_initial_webview_will_set_content)
            aqt.mw.progress.single_shot(100, self.show_current)

        if initial_contexts:
            gui_hooks.webview_will_set_content.append(_on_initial_webview_will_set_content)
            self.refresh_initial_webviews()
        else:
            self.show_current()

    def end(self) -> None:
        gui_hooks.webview_will_set_content.remove(self._on_webview_will_set_content)
        gui_hooks.webview_did_receive_js_message.remove(self._on_webview_did_receive_js_message)
        last_step = self.steps[self.current_step - 1]
        for web in (
            webview_for_context(last_step.target_context),
            *[webview_for_context(context) for context in self.extra_backdrop_contexts],
        ):
            web.eval(
                "if(typeof AnkiHub.destroyActiveTutorialModal !== 'undefined') AnkiHub.destroyActiveTutorialModal()"
            )
        if last_step.hidden_callback:
            last_step.hidden_callback()
        global active_tutorial
        active_tutorial = None

    def _on_webview_will_set_content(self, web_content: WebContent, context: Optional[object] = None) -> None:
        if not isinstance(context, self.contexts):
            return
        web_base = f"/_addons/{aqt.mw.addonManager.addonFromModule(__name__)}/gui/web"
        web_content.css.append(f"{web_base}/lib/tutorial.css")
        web_content.js.append(f"{web_base}/lib/tutorial.js")

        if not self._show_timer:
            step = self.steps[self.current_step - 1]
            js = ""
            if step.tooltip_context == context:
                js = self._render_tooltip(False)
            elif step.target_context == context:
                js = self._render_highlight(False)
            elif context in self.extra_backdrop_contexts:
                js = self._backdrop_js()
            if js:
                web_content.body += f"<script>{js}</script>"
        self._loadded_context_types.add(type(context))

    def _on_webview_did_receive_js_message(
        self, handled: tuple[bool, Any], message: str, context: Any
    ) -> tuple[bool, Any]:
        if not isinstance(context, self.contexts):
            return handled
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
            web.eval(
                "if(typeof AnkiHub.destroyActiveTutorialModal !== 'undefined') AnkiHub.destroyActiveTutorialModal()"
            )

        if step.hidden_callback:
            step.hidden_callback()
        if self.current_step >= len(self.steps):
            self.end()
            return
        self.current_step += 1

        # Wait for a maximum of 2 seconds for the webviews to load then show the next step
        delay = 50
        time_passed = 0

        def task() -> None:
            nonlocal time_passed
            time_passed += delay
            next_step = self.steps[self.current_step - 1]
            if (
                (
                    type(next_step.tooltip_context) in self._loadded_context_types
                    and type(next_step.target_context) in self._loadded_context_types
                )
                or time_passed >= 2000
                or isinstance(next_step, QTutorialStep)
            ):
                if self._show_timer:
                    self._show_timer.deleteLater()
                self._show_timer = None
                self.show_current()

        if self._show_timer:
            self._show_timer.deleteLater()
        self._show_timer = aqt.mw.progress.timer(delay, task, repeat=True, parent=aqt.mw)


class OnboardingTutorial(Tutorial):
    def __init__(self) -> None:
        super().__init__()

    @property
    def contexts(self) -> Tuple[Any, ...]:
        from aqt.deckbrowser import DeckBrowser, DeckBrowserBottomBar

        return (
            DeckBrowser,
            Overview,
            TopToolbar,
            BottomBar,
            DeckBrowserBottomBar,
            OverviewBottomBar,
        )

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

        def on_overview_will_render_content(overview: Overview, content: OverviewContent) -> None:
            did = aqt.mw.col.decks.id(content.deck)
            intro_deck_config = config.deck_config(config.intro_deck_id)
            if intro_deck_config and did == intro_deck_config.anki_id:
                self.next()

        def on_intro_step_shown() -> None:
            gui_hooks.overview_will_render_content.append(on_overview_will_render_content)

        def on_intro_step_hidden() -> None:
            gui_hooks.overview_will_render_content.remove(on_overview_will_render_content)

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
            pass

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

    @property
    def initial_contexts(self) -> Tuple[Any, ...]:
        from aqt.deckbrowser import DeckBrowser, DeckBrowserBottomBar

        return (
            DeckBrowser,
            TopToolbar,
            DeckBrowserBottomBar,
        )

    def refresh_initial_webviews(self) -> None:
        aqt.mw.deckBrowser.refresh()
        aqt.mw.toolbar.draw()
        aqt.mw.deckBrowser.bottom.draw()


def prompt_for_onboarding_tutorial() -> None:
    if active_tutorial:
        return

    from aqt.deckbrowser import DeckBrowser

    def on_webview_will_set_content(web_content: WebContent, context: Optional[object] = None) -> None:
        print("on_webview_will_set_content", context)
        if not isinstance(context, DeckBrowser):
            return
        web_base = f"/_addons/{aqt.mw.addonManager.addonFromModule(__name__)}/gui/web"
        web_content.css.append(f"{web_base}/lib/tutorial.css")
        web_content.js.append(f"{web_base}/lib/tutorial.js")
        web_content.body += "<script>AnkiHub.promptForOnboardingTour()</script>"
        gui_hooks.webview_will_set_content.remove(on_webview_will_set_content)

    gui_hooks.webview_will_set_content.append(on_webview_will_set_content)
    aqt.mw.deckBrowser.refresh()


@dataclass
class QTutorialStep(TutorialStep):
    target: Optional[Union[str, Callable[[], str]]] = ""
    tooltip_context: Optional[Any] = None
    parent_widget: Optional[QWidget] = None
    qt_target: Optional[QWidget] = None


class QtTutorial(Tutorial):
    def __init__(self) -> None:
        super().__init__()
        self.apply_backdrop = False

    def show_current(self) -> None:
        step = cast(QTutorialStep, self.steps[self.current_step - 1])
        overlay = TutorialOverlayDialog(step.parent_widget, step.qt_target)
        overlay.show()
        step.tooltip_context = overlay
        step.target_context = overlay
        step.target = "#target"

        def close_overlay() -> None:
            overlay.close()

        step.hidden_callback = close_overlay
        super().show_current()

    @property
    def contexts(self) -> Tuple[Any, ...]:
        return (TutorialOverlayDialog,)


class QtTutorialDemo(QtTutorial):
    def __init__(self) -> None:
        super().__init__()
        self.browser: Browser

    def start(self) -> None:
        self.browser = dialogs.open("Browser", aqt.mw)
        return super().start()

    @cached_property
    def steps(self) -> list[TutorialStep]:
        return [
            QTutorialStep(
                "Notes list",
                parent_widget=self.browser,
                qt_target=self.browser.form.tableView,
            ),
            QTutorialStep(
                "Editor",
                parent_widget=self.browser,
                qt_target=self.browser.form.fieldsArea,
            ),
            QTutorialStep(
                "Sidebar",
                parent_widget=self.browser,
                qt_target=self.browser.sidebar.searchBar,
            ),
        ]
