"""Module for handling LLM prompt functionality in the editor."""

import json
import subprocess
from pathlib import Path
from typing import Any, List

import aqt
from aqt import gui_hooks
from aqt.editor import Editor
from aqt.qt import QDialog, QPushButton, QTextEdit, QVBoxLayout
from aqt.utils import tooltip
from jinja2 import Template

PROMPT_SELECTOR_BTN_ID = "ankihub-btn-llm-prompt"


def setup() -> None:
    """Set up the LLM prompt functionality."""
    gui_hooks.editor_did_init_buttons.append(_setup_prompt_selector_button)
    gui_hooks.webview_did_receive_js_message.append(_handle_js_message)


def _setup_prompt_selector_button(buttons: List[str], editor: Editor) -> None:
    """Add the LLM prompt selector button to the editor."""
    prompt_button = editor.addButton(
        icon=None,
        cmd=PROMPT_SELECTOR_BTN_ID,
        func=_on_prompt_button_press,
        label="Prompts ▾",
        id=PROMPT_SELECTOR_BTN_ID,
        disables=False,
    )
    buttons.append(prompt_button)

    # Add button styling
    buttons.append(
        "<style> "
        f"  #{PROMPT_SELECTOR_BTN_ID} {{ width:auto; padding:1px; }}\n"
        f"  #{PROMPT_SELECTOR_BTN_ID}[disabled] {{ opacity:.4; }}\n"
        "</style>"
    )


def _get_prompt_templates() -> List[str]:
    """Get list of prompt template files from the LLM templates directory."""
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
        return ["No prompt templates found"]


def _on_prompt_button_press(editor: Editor) -> None:
    """Handle the prompt selector button click by showing a dropdown menu."""
    prompts = _get_prompt_templates()

    # Read and render the JavaScript template
    js_template_path = Path(__file__).parent / "prompt_selector.js"
    with open(js_template_path, "r") as f:
        template = Template(f.read())

    script = template.render(
        button_id=PROMPT_SELECTOR_BTN_ID, options=json.dumps(prompts)
    )
    editor.web.eval(script)


def _get_note_content(editor: Editor) -> str:
    """Extract content from the current note's fields."""
    note = editor.note
    if not note:
        return ""

    fields_dict = {name: note[name] for name in note.keys()}
    return json.dumps(fields_dict)


def _show_llm_response(response: str) -> None:
    """Display the LLM response in a dialog."""
    dialog = QDialog(aqt.mw)
    dialog.setWindowTitle("LLM Response")
    dialog.setMinimumWidth(600)
    dialog.setMinimumHeight(400)

    layout = QVBoxLayout()

    # Create text display area
    text_edit = QTextEdit()
    text_edit.setPlainText(response)
    text_edit.setReadOnly(True)
    layout.addWidget(text_edit)

    # Add close button
    close_button = QPushButton("Close")
    close_button.clicked.connect(dialog.accept)
    layout.addWidget(close_button)

    dialog.setLayout(layout)
    dialog.exec()


def _execute_prompt_template(editor: Editor, template_name: str) -> None:
    """Execute the selected prompt template with the current note as input."""
    note_content = _get_note_content(editor)
    if not note_content:
        tooltip("No note content available")
        return

    try:
        # Run the LLM command with the template and note content
        # Use shlex.quote to properly escape the note content for shell command
        import shlex

        escaped_content = shlex.quote(note_content)
        result = subprocess.run(
            [
                "llm",
                "--no-stream",
                "-t",
                template_name.replace(".yaml", ""),
                escaped_content,
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        # Show the response in a dialog
        _show_llm_response(result.stdout)

    except subprocess.CalledProcessError as e:
        error_msg = f"Error running LLM command: {e.stderr}"
        tooltip(error_msg)
    except Exception as e:
        tooltip(f"Unexpected error: {str(e)}")


def _handle_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    """Handle JavaScript messages for prompt template selection."""
    if message.startswith("prompt-select:"):
        template_name = message.split(":", 1)[1]
        _execute_prompt_template(context, template_name)
        return (True, None)
    return handled
