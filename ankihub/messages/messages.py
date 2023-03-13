import pathlib

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..importing import AnkiHubImportResult
from ..settings import ADDON_PACKAGE

templates = (pathlib.Path(__file__).parent / "templates").absolute()
env = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape())


def request_error(event_id):
    template = env.get_template("request_error.html")
    return template.render(
        addon_package=ADDON_PACKAGE,
        event_id=event_id,
    )


def other_error(event_id):
    template = env.get_template("other_error.html")
    return template.render(
        addon_package=ADDON_PACKAGE,
        event_id=event_id,
    )


def deck_import_summary(deck_name: str, import_result: AnkiHubImportResult):
    template = env.get_template("deck_import_summary.html")
    return template.render(deck_name=deck_name, import_result=import_result)
