import pathlib
from typing import List

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient

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


def get_ankihub_ai_js(
    template_name: str,
    knox_token: str,
    app_url: str,
    endpoint_path: str,
    query_parameters: str,
    theme: str,
) -> str:
    return env.get_template(template_name).render(
        {
            "KNOX_TOKEN": knox_token,
            "APP_URL": app_url,
            "ENDPOINT_PATH": endpoint_path,
            "QUERY_PARAMETERS": query_parameters,
            "THEME": theme,
        }
    )


def get_reviewer_buttons_js(theme: str, enabled_buttons: List[str]) -> str:
    client = AnkiHubClient()
    return env.get_template("reviewer_buttons.js").render(
        {
            "THEME": theme,
            "ENABLED_BUTTONS": ",".join(enabled_buttons),
            "IS_PREMIUM": str(client.is_premium_user()),
        }
    )


def get_empty_state_html(theme: str, resource_type: str) -> str:
    return env.get_template("mh_no_urls_empty_state.html").render(
        {"theme": theme, "resource_type": resource_type}
    )


def get_remove_anking_button_js() -> str:
    return env.get_template("remove_anking_button.js").render()
