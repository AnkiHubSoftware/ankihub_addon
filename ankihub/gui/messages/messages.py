import pathlib
from typing import List

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ...ankihub_client import Deck
from ...main.importing import AnkiHubImportResult

templates = (pathlib.Path(__file__).parent / "templates").absolute()
env = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape())


def deck_import_summary(
    ankihub_deck_names: List[str],
    anki_deck_names: List[str],
    import_results: List[AnkiHubImportResult],
    logged_to_ankiweb: bool,
) -> str:
    template = env.get_template("deck_import_summary.html")
    result = template.render(
        ankihub_deck_names=ankihub_deck_names,
        import_results=import_results,
        anki_deck_names=anki_deck_names,
        logged_to_ankiweb=logged_to_ankiweb,
        zip=zip,
    )
    result = result.replace("\n", " ")
    return result


def deck_install_confirmation(decks: List[Deck], logged_to_ankiweb: bool) -> str:
    template = env.get_template("deck_install_confirmation.html")
    result = template.render(decks=decks, logged_to_ankiweb=logged_to_ankiweb)
    result = result.replace("\n", " ")
    return result
