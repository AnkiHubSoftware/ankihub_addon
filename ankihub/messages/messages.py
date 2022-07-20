import pathlib

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..constants import BUG_REPORT_FORM, ADDON_PACKAGE

templates = (pathlib.Path(__file__).parent / "templates").absolute()
env = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape())


def request_error(event_id):
    template = env.get_template("request_error.html")
    return template.render(
        bug_report_form=BUG_REPORT_FORM,
        addon_package=ADDON_PACKAGE,
        event_id=event_id,
    )
