"""Module for handling LLM prompt functionality in the editor."""

import difflib
import json
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import aqt
from aqt import QFont, gui_hooks
from aqt.editor import Editor
from aqt.qt import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout
from aqt.utils import showWarning, tooltip
from jinja2 import Template

PROMPT_SELECTOR_BTN_ID = "ankihub-btn-llm-prompt"


class TemplateManager:
    """Manages LLM template operations and caching."""

    _templates_path = None
    _local_templates_dir = Path(__file__).parent / "prompt_templates"

    @classmethod
    def initialize(cls) -> None:
        """Initialize the template manager by finding the templates directory."""
        try:
            result = subprocess.run(
                ["uv", "run", "--no-project", "llm", "templates", "path"],
                capture_output=True,
                text=True,
                check=True,
            )
            cls._templates_path = Path(result.stdout.strip())
            print(f"Templates directory: {cls._templates_path}")

            # After finding templates path, try to copy local templates
            cls._copy_local_templates()
        except subprocess.CalledProcessError as e:
            print(f"Error finding templates directory: {e.stderr}")
            cls._templates_path = None
        except Exception as e:
            print(f"Unexpected error finding templates directory: {str(e)}")
            cls._templates_path = None

    @classmethod
    def _copy_local_templates(cls) -> None:
        """Copy local templates to user's templates directory if they don't exist."""
        if not cls._templates_path or not cls._local_templates_dir.exists():
            return

        try:
            # Create templates directory if it doesn't exist
            cls._templates_path.mkdir(parents=True, exist_ok=True)

            # Copy each template that doesn't already exist
            for template_file in cls._local_templates_dir.glob("*.yaml"):
                target_path = cls._templates_path / template_file.name
                if not target_path.exists():
                    print(f"Copying template: {template_file.name}")
                    target_path.write_text(template_file.read_text())
                else:
                    print(f"Template already exists: {template_file.name}")
        except Exception as e:
            print(f"Error copying templates: {str(e)}")

    @classmethod
    def get_templates_path(cls):
        """Get the cached templates path."""
        if cls._templates_path is None:
            cls.initialize()
        return cls._templates_path

    @classmethod
    def get_template_content(cls, template_name: str) -> str:
        """Get the content of a specific template."""
        templates_path = cls.get_templates_path()
        if not templates_path:
            return "Error: Templates directory not found"

        template_file = templates_path / f"{template_name}.yaml"
        if not template_file.exists():
            return "Template file not found"

        try:
            return template_file.read_text()
        except Exception as e:
            return f"Error reading template: {str(e)}"

    @classmethod
    def get_anki_templates(cls) -> List[str]:
        """Get list of Anki-specific template names."""
        templates_path = cls.get_templates_path()
        if not templates_path or not templates_path.exists():
            return ["No prompt templates found"]

        try:
            yaml_files = [
                f.stem
                for f in templates_path.glob("*.yaml")
                if f.is_file() and f.stem.lower().startswith("anki")
            ]
            yaml_files.sort()
            return yaml_files or ["No Anki templates found"]
        except Exception:
            return ["Error listing templates"]


class PromptPreviewDialog(QDialog):
    """Dialog for previewing and editing a prompt template before execution."""

    def __init__(self, parent, template_name: str, editor: Editor) -> None:
        super().__init__(parent)
        self.template_name = template_name
        self.editor = editor
        self.template_content = TemplateManager.get_template_content(template_name)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setWindowTitle(f"Preview Template: {self.template_name}")
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)

        layout = QVBoxLayout()

        # Add description label
        description = QLabel("Review and edit the prompt template below:")
        description.setWordWrap(True)
        layout.addWidget(description)

        # Add template editor
        self.template_edit = QTextEdit()
        self.template_edit.setPlainText(self.template_content)
        self.template_edit.setFont(QFont("Consolas"))
        layout.addWidget(self.template_edit)

        # Add button row
        button_layout = QHBoxLayout()

        # Execute button
        execute_button = QPushButton("Execute Prompt")
        execute_button.clicked.connect(self._on_execute)
        button_layout.addWidget(execute_button)

        # Cancel button
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def _on_execute(self) -> None:
        """Handle the execute button click."""
        modified_template = self.template_edit.toPlainText()
        # TODO Save the modified template if it differs from the original
        _execute_prompt_template(self.editor, self.template_name, modified_template)
        self.accept()


def _check_and_install_uv() -> None:
    """Check if uv is installed and install it if not."""
    try:
        subprocess.run(["uv", "version"], capture_output=True, check=True)
        print("uv is installed")
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
                    shell=True,
                    check=True,
                )
            else:  # macOS and Linux
                subprocess.run(
                    "curl -LsSf https://astral.sh/uv/install.sh | sh",
                    shell=True,
                    check=True,
                )
            tooltip("Successfully installed uv")
        except subprocess.CalledProcessError as e:
            showWarning(f"Failed to install uv: {str(e)}")


def _install_llm() -> None:
    """Install llm and additional providers using uv if not already installed."""
    # TODO Prompt users to set up their API keys.
    try:
        subprocess.run(["llm", "--version"], capture_output=True, check=True)
        print("llm is already installed")
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            # Install base llm package
            subprocess.run(
                ["uv", "tool", "install", "llm"],
                check=True,
                capture_output=True,
            )
            tooltip("Successfully installed llm")

            # Install additional providers
            providers = ["llm-gemini", "llm-perplexity", "llm-claude-3"]
            for provider in providers:
                try:
                    subprocess.run(
                        ["uv", "run", "--no-project", "llm", "install", "-U", provider],
                        check=True,
                        capture_output=True,
                    )
                    print(f"Successfully installed {provider}")
                except subprocess.CalledProcessError as e:
                    showWarning(f"Failed to install {provider}: {str(e)}")

        except subprocess.CalledProcessError as e:
            showWarning(f"Failed to install llm: {str(e)}")


def setup() -> None:
    """Set up the LLM prompt functionality."""
    _check_and_install_uv()
    _install_llm()
    TemplateManager.initialize()  # Initialize templates path
    gui_hooks.editor_did_init_buttons.append(_setup_prompt_selector_button)
    gui_hooks.webview_did_receive_js_message.append(_handle_js_message)


def _setup_prompt_selector_button(buttons: List[str], editor: Editor) -> None:
    """Add the LLM prompt selector button to the editor."""
    prompt_button = editor.addButton(
        icon=None,
        cmd=PROMPT_SELECTOR_BTN_ID,
        func=_on_prompt_button_press,
        label="✨ LLM Prompts",
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
    return TemplateManager.get_anki_templates()


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


def _update_note_fields(editor: Editor, new_fields: Dict[str, str]) -> None:
    """Update the note fields with new content."""
    note = editor.note
    if not note:
        return

    # Only update fields that exist in the note
    for field_name, new_content in new_fields.items():
        if field_name in note:
            note[field_name] = new_content

    # Save changes and update the editor
    note.flush()
    editor.loadNote()


def _create_diff_html(original: str, suggested: str) -> str:
    """Create HTML diff between original and suggested text."""
    differ = difflib.Differ()
    diff = list(differ.compare(original.splitlines(True), suggested.splitlines(True)))

    html = []
    for line in diff:
        if line.startswith("+"):
            html.append(f'<span style="background-color: #e6ffe6">{line[2:]}</span>')
        elif line.startswith("-"):
            html.append(
                f'<span style="background-color: #ffe6e6; text-decoration: line-through">{line[2:]}</span>'
            )
        elif line.startswith("?"):
            continue
        else:
            html.append(line[2:])

    return "".join(html)


def _show_llm_response(editor: Editor, response: str) -> None:
    """Display the LLM response in a dialog with option to update note."""
    try:
        suggested_fields = json.loads(response)
        if not isinstance(suggested_fields, dict):
            showWarning("Invalid response format. Expected a JSON object.")
            return
    except json.JSONDecodeError:
        showWarning("Invalid JSON response from LLM")
        return

    dialog = QDialog(aqt.mw)
    dialog.setWindowTitle("LLM Response - Field Changes")
    dialog.setMinimumWidth(800)
    dialog.setMinimumHeight(600)

    layout = QVBoxLayout()

    # Create text display area with HTML formatting
    text_edit = QTextEdit()
    text_edit.setReadOnly(True)

    # Build HTML content showing diffs for each field
    html_content = [
        "<style>",
        "body { font-family: monospace; }",
        ".field-name { font-weight: bold; font-size: 1.1em; margin-top: 1em; }",
        ".field-content { margin-left: 1em; white-space: pre-wrap; }",
        "</style>",
    ]

    note = editor.note
    for field_name, suggested_content in suggested_fields.items():
        if field_name in note:
            original_content = note[field_name]
            html_content.append(f'<div class="field-name">{field_name}:</div>')
            html_content.append('<div class="field-content">')
            html_content.append(_create_diff_html(original_content, suggested_content))
            html_content.append("</div>")

    text_edit.setHtml("".join(html_content))
    layout.addWidget(text_edit)

    # Create button row
    button_layout = QHBoxLayout()

    # Add update button
    update_button = QPushButton("Update Note")
    update_button.clicked.connect(lambda: _handle_update_note(editor, response, dialog))
    button_layout.addWidget(update_button)

    # Add close button
    close_button = QPushButton("Close")
    close_button.clicked.connect(dialog.accept)
    button_layout.addWidget(close_button)

    layout.addLayout(button_layout)
    dialog.setLayout(layout)
    dialog.exec()


def _handle_update_note(editor: Editor, response: str, dialog: QDialog) -> None:
    """Handle the update note button click."""
    try:
        # Parse the JSON response
        new_fields = json.loads(response)
        if not isinstance(new_fields, dict):
            showWarning("Invalid response format. Expected a JSON object.")
            return

        # Update the note
        _update_note_fields(editor, new_fields)
        tooltip("Note updated successfully")
        dialog.accept()

    except json.JSONDecodeError:
        showWarning("Invalid JSON response from LLM")
    except Exception as e:
        showWarning(f"Error updating note: {str(e)}")


def _handle_prompt_selection(editor: Editor, template_name: str) -> None:
    """Handle the selection of a prompt template."""
    dialog = PromptPreviewDialog(None, template_name, editor)
    dialog.exec()


def _execute_prompt_template(
    editor: Editor, template_name: str, template_content=None
) -> None:
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
        # TODO Exclude ankihub_id field
        note_schema = json.dumps([{field: "string" for field in editor.note.keys()}])

        cmd = [
            "uv",
            "run",
            "--no-project",
            "llm",
            # TODO Allow users to choose model
            # TODO Allow users to continue a conversation
            # TODO Allow users to add an attachment
            "-m",
            "gpt-4o",
            "--no-stream",
        ]

        if template_content:
            # If we have modified template content, pass it via stdin
            cmd.extend(["-s", template_content])
        else:
            # Otherwise use the template file
            cmd.extend(["-t", template_name])

        cmd.extend(
            [
                "-p",
                "note_schema",
                shlex.quote(note_schema),
                escaped_content,
                "-o",
                "json_object",
                "1",
            ]
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

        # Show the response in a dialog
        _show_llm_response(editor, result.stdout)

    except subprocess.CalledProcessError as e:
        error_msg = f"Error running LLM command: {e.stderr}"
        tooltip(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        tooltip(f"Unexpected error: {str(e)}")
        raise Exception(f"Unexpected error: {str(e)}")


def _handle_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    """Handle JavaScript messages for prompt template selection."""
    if message.startswith("prompt-select:"):
        template_name = message.split(":", 1)[1]
        _handle_prompt_selection(context, template_name)
        return (True, None)
    return handled
