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
) -> str:
    template = env.get_template("deck_import_summary.html")
    result = template.render(
        ankihub_deck_names=ankihub_deck_names,
        import_results=import_results,
        anki_deck_names=anki_deck_names,
        zip=zip,
    )
    result = result.replace("\n", " ")
    return result


def deck_install_confirmation(decks: List[Deck]) -> str:
    template = env.get_template("deck_install_confirmation.html")
    result = template.render(decks=decks)
    result = result.replace("\n", " ")
    return result
