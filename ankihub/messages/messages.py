import pathlib

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ankihub.constants import BUG_REPORT_FORM

templates = (pathlib.Path(__file__).parent / "templates").absolute()
env = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape())


def request_error():
    template = env.get_template("request_error.html")
    return template.render(bug_report_form=BUG_REPORT_FORM)
