import pathlib
from typing import List

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..ankihub_client import Deck
from ..importing import AnkiHubImportResult

templates = (pathlib.Path(__file__).parent / "templates").absolute()
env = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape())


def deck_import_summary(deck_name: str, import_result: AnkiHubImportResult):
    template = env.get_template("deck_import_summary.html")
    return template.render(deck_name=deck_name, import_result=import_result)


def deck_install_confirmation(decks: List[Deck]):
    template = env.get_template("deck_install_confirmation.html")
    return template.render(decks=decks)
