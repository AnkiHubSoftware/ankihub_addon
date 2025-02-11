"""Module for handling LLM template functionality in the editor."""

import json
import subprocess
from pathlib import Path
from typing import Any, List

from aqt import gui_hooks
from aqt.editor import Editor
from aqt.utils import tooltip
from jinja2 import Template

TEMPLATE_BTN_ID = "ankihub-btn-llm-templates"


def setup() -> None:
    """Set up the LLM templates functionality."""
    gui_hooks.editor_did_init_buttons.append(_setup_editor_button)
    gui_hooks.webview_did_receive_js_message.append(_handle_js_message)


def _setup_editor_button(buttons: List[str], editor: Editor) -> None:
    """Add the LLM templates button to the editor."""
    template_button = editor.addButton(
        icon=None,
        cmd=TEMPLATE_BTN_ID,
        func=_on_template_button_press,
        label="Templates ▾",
        id=TEMPLATE_BTN_ID,
        disables=False,
    )
    buttons.append(template_button)

    # Add button styling
    buttons.append(
        "<style> "
        f"  #{TEMPLATE_BTN_ID} {{ width:auto; padding:1px; }}\n"
        f"  #{TEMPLATE_BTN_ID}[disabled] {{ opacity:.4; }}\n"
        "</style>"
    )


def _get_template_files() -> List[str]:
    """Get list of template files from the LLM templates directory."""
    try:
        # Get templates directory path from llm command
        result = subprocess.run(
            ["llm", "templates", "path"], capture_output=True, text=True, check=True
        )
        templates_path = Path(result.stdout.strip())

        # Get all yaml files from the directory
        yaml_files = []
        if templates_path.exists():
            yaml_files = [f.name for f in templates_path.glob("*.yaml") if f.is_file()]
            yaml_files.sort()

        return yaml_files
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ["No templates found"]


def _on_template_button_press(editor: Editor) -> None:
    """Handle the template button click by showing a dropdown menu."""
    options = _get_template_files()

    # Read and render the JavaScript template
    js_template_path = Path(__file__).parent / "editor_dropdown.js"
    with open(js_template_path, "r") as f:
        template = Template(f.read())

    script = template.render(button_id=TEMPLATE_BTN_ID, options=json.dumps(options))
    editor.web.eval(script)


def _handle_template_selection(editor: Editor, template_name: str) -> None:
    """Handle when a template is selected from the dropdown."""
    tooltip(f"Selected template: {template_name}")


def _handle_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    """Handle JavaScript messages for template selection."""
    if message.startswith("template-select:"):
        template_name = message.split(":", 1)[1]
        _handle_template_selection(context, template_name)
        return (True, None)
    return handled
