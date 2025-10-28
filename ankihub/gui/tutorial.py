import json
from concurrent.futures import Future
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Callable, Optional, Set, Tuple, Type, Union

from aqt import QTimer, gui_hooks, mw
from aqt.deckbrowser import DeckBrowser, DeckBrowserBottomBar
from aqt.main import MainWindowState
from aqt.overview import Overview, OverviewBottomBar, OverviewContent
from aqt.qt import QDialogButtonBox
from aqt.reviewer import Reviewer, ReviewerBottomBar
from aqt.toolbar import BottomBar, Toolbar, TopToolbar
from aqt.webview import AnkiWebView, WebContent

from ..gui.utils import show_dialog
from ..settings import config

PRIMARY_BUTTON_CLICKED_PYCMD = "ankihub_tutorial_primary_button_clicked"
TARGET_RESIZE_PYCMD = "ankihub_tutorial_target_resize"
MODAL_CLOSED_PYCMD = "ankihub_modal_closed"


def webview_for_context(context: Any) -> AnkiWebView:
    if isinstance(context, (DeckBrowser, Reviewer, Overview)):
        return mw.web
    if isinstance(context, (BottomBar, DeckBrowserBottomBar, OverviewBottomBar, ReviewerBottomBar)):
        return mw.bottomWeb
    if isinstance(context, (Toolbar, TopToolbar)):
        return mw.toolbar.web
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

    def __post_init__(self):
        if not self.target_context:
            self.target_context = self.tooltip_context


active_tutorial: Optional["Tutorial"] = None


class Tutorial:
    def __init__(self) -> None:
        self.name = ""
        self.current_step = 1
        self._loadded_context_types: Set[Type[Any]] = set()

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

    def _call_js_function_with_options(self, web: AnkiWebView, function: str, options: dict[str, Any]) -> None:
        js = f"{function}({{" + ",".join(f"{k}: {json.dumps(v)}" for k, v in options.items()) + "})"
        web.eval(js)

    def show_current(self) -> None:
        step = self.steps[self.current_step - 1]
        if step.shown_callback:
            step.shown_callback()
        tooltip_web = webview_for_context(step.tooltip_context)
        target_web = webview_for_context(step.target_context)
        tooltip_options = {
            "body": step.body,
            "currentStep": self.current_step,
            "stepCount": len(self.steps),
            "position": "bottom",
            "primaryButton": {
                "show": step.show_primary_button,
                "label": step.primary_button_label,
            },
        }
        if step.target and step.tooltip_context == step.target_context:
            tooltip_options["target"] = step.target if isinstance(step.target, str) else step.target()
        if not step.target:
            tooltip_options["showArrow"] = False
        self._call_js_function_with_options(tooltip_web, "showAnkiHubTutorialModal", tooltip_options)
        if step.target and step.tooltip_context != step.target_context:
            self._call_js_function_with_options(
                target_web, "highlightAnkiHubTutorialTarget", {"target": step.target, "currentStep": self.current_step}
            )
        for context in self.extra_backdrop_contexts:
            web = webview_for_context(context)
            if web not in (tooltip_web, target_web):
                web.eval("addAnkiHubTutorialBackdrop()")

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
            mw.progress.single_shot(100, self.show_current)

        gui_hooks.webview_will_set_content.append(_on_initial_webview_will_set_content)
        self.refresh_initial_webviews()

    def end(self) -> None:
        gui_hooks.webview_will_set_content.remove(self._on_webview_will_set_content)
        gui_hooks.webview_did_receive_js_message.remove(self._on_webview_did_receive_js_message)
        last_step = self.steps[self.current_step - 1]
        for web in (
            webview_for_context(last_step.target_context),
            *[webview_for_context(context) for context in self.extra_backdrop_contexts],
        ):
            web.eval("if(typeof destroyAnkiHubTutorialModal !== 'undefined') destroyAnkiHubTutorialModal()")
        global active_tutorial
        active_tutorial = None

    def _on_webview_will_set_content(self, web_content: WebContent, context: Optional[object] = None) -> None:
        if not isinstance(context, self.contexts):
            return
        web_base = f"/_addons/{mw.addonManager.addonFromModule(__name__)}/gui/web"
        web_content.css.append(f"{web_base}/modal.css")
        web_content.js.append(f"{web_base}/modal.js")
        web_content.js.append(f"{web_base}/tutorial.js")
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
            transform = f"translateX(calc(-50% + {width / 2}px))"
            if current_step == self.current_step:
                step = self.steps[current_step - 1]
                target_js = f"positionAnkiHubTutorialTarget({{top: {top}, left: {left}, transform: '{transform}'}});"
                tooltip_web = webview_for_context(step.tooltip_context)
                tooltip_web.eval(target_js)
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
            web.eval("if(typeof destroyAnkiHubTutorialModal !== 'undefined') destroyAnkiHubTutorialModal()")

        if step.hidden_callback:
            step.hidden_callback()
        if self.current_step >= len(self.steps):
            self.end()
            return
        self.current_step += 1

        # Wait for a maximum of 2 seconds for the webviews to load then show the next step
        timer: QTimer
        delay = 50
        time_passed = 0

        def task() -> None:
            nonlocal time_passed
            time_passed += delay
            next_step = self.steps[self.current_step - 1]
            if (
                type(next_step.tooltip_context) in self._loadded_context_types
                and type(next_step.target_context) in self._loadded_context_types
            ) or time_passed >= 2000:
                timer.deleteLater()
                self.show_current()

        timer = mw.progress.timer(delay, task, repeat=True, parent=mw)


class OnboardingTutorial(Tutorial):
    def __init__(self) -> None:
        super().__init__()

    @property
    def contexts(self) -> Tuple[Any, ...]:
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
                tooltip_context=mw.deckBrowser,
                target_context=mw.toolbar,
            )
        ]

        def on_overview_will_render_content(overview: Overview, content: OverviewContent) -> None:
            did = mw.col.decks.id(content.deck)
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
                    tooltip_context=mw.deckBrowser,
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
                    tooltip_context=mw.deckBrowser,
                    show_primary_button=True,
                    primary_button_label="Sync with AnkiHub",
                    button_callback=on_sync_with_ankihub_button_clicked,
                )
            )
            steps.append(
                TutorialStep(
                    body="You now have the deck <b>Getting Started with Anki</b> installed. Click on it to open.",
                    target=lambda: f"[id='{config.deck_config(config.intro_deck_id).anki_id}']",
                    tooltip_context=mw.deckBrowser,
                    shown_callback=on_intro_step_shown,
                    hidden_callback=on_intro_step_hidden,
                    show_primary_button=False,
                )
            )

        steps.append(
            TutorialStep(
                body="This deck will help you understand the basics of card reviewing.",
                target="",
                tooltip_context=mw.overview,
            )
        )
        steps.append(
            TutorialStep(
                "These daily stats show you:<br><ul>"
                "<li><b>New</b>: new cards to study</li>"
                "<li><b>Learning</b>: reviewed cards on short delay to come back</li>"
                "<li><b>To Review</b>: reviewed cards on long delay to come back</li></ul>",
                target="td",
                tooltip_context=mw.overview,
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
                tooltip_context=mw.overview,
                shown_callback=on_study_step_shown,
                hidden_callback=on_study_step_hidden,
                show_primary_button=False,
            )
        )
        return steps

    @property
    def extra_backdrop_contexts(self) -> tuple[Any, ...]:
        return (mw.deckBrowser, mw.overview, mw.deckBrowser.bottom, mw.toolbar)

    @property
    def initial_contexts(self) -> Tuple[Any, ...]:
        return (
            DeckBrowser,
            TopToolbar,
            DeckBrowserBottomBar,
        )

    def refresh_initial_webviews(self) -> None:
        mw.deckBrowser.refresh()
        mw.toolbar.draw()
        mw.deckBrowser.bottom.draw()


def prompt_for_onboarding_tutorial() -> None:
    if active_tutorial:
        return

    def _on_take_tour_button_clicked(button_index: int) -> None:
        if button_index == 1:
            OnboardingTutorial().start()

    show_dialog(
        text="Find your way in the app with this onboarding tour.",
        title="📚 First time with Anki?",
        buttons=[
            ("Close", QDialogButtonBox.ButtonRole.RejectRole),
            ("Take tour", QDialogButtonBox.ButtonRole.AcceptRole),
        ],
        default_button_idx=1,
        callback=lambda button_index: _on_take_tour_button_clicked(button_index),
    )
