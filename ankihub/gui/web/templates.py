import pathlib

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_PATH = (pathlib.Path(__file__).parent).absolute()

env = Environment(
    loader=FileSystemLoader(TEMPLATES_PATH), autoescape=select_autoescape()
)


def get_header_webview_html(
    tabs, current_active_tab_url: str, page_title: str, theme: str
) -> str:
    return env.get_template("sidebar_tabs.html").render(
        {
            "tabs": tabs,
            "current_active_tab_url": current_active_tab_url,
            "page_title": page_title,
            "theme": theme,
        }
    )


def get_reviewer_buttons_js(theme: str) -> str:
    return env.get_template("reviewer_buttons.js").render({"THEME": theme})


def get_empty_state_html(theme: str, resource_type: str) -> str:
    return env.get_template("mh_no_urls_empty_state.html").render(
        {"theme": theme, "resource_type": resource_type}
    )


def get_remove_anking_button_js() -> str:
    return env.get_template("remove_anking_button.js").render()
