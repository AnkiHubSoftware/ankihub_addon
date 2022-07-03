import pathlib

from ankihub.constants import BUG_REPORT_FORM

messages_dir = pathlib.Path(__file__).parent.absolute()


def request_error():
    path = messages_dir / "request_error.html"
    with path.open() as f:
        lines = f.read()
    return lines.format(BUG_REPORT_FORM)
