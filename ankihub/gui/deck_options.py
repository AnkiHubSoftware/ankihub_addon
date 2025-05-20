from pathlib import Path

from aqt.deckoptions import DeckOptionsDialog
from aqt.gui_hooks import deck_options_did_load
from jinja2 import Template

from .utils import anki_theme

ADD_FSRS_REVERT_BUTTON_JS_PATH = (
    Path(__file__).parent / "web" / "add_fsrs_revert_button.js"
)
REVERT_FSRS_PARAMATERS_PYCMD = "ankihub_revert_fsrs_parameters"


def setup() -> None:
    def _on_deck_options_did_load(deck_options_dialog: DeckOptionsDialog) -> None:
        js = Template(ADD_FSRS_REVERT_BUTTON_JS_PATH.read_text()).render(
            {"THEME": anki_theme()}
        )
        deck_options_dialog.web.eval(js)

    deck_options_did_load.append(_on_deck_options_did_load)
