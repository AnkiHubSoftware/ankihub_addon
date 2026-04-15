"""In-editor UI mockup for toggling per-field AnkiHub protection.

Adds a padlock icon to each field header in the note editor. The icon reflects
whether that field is currently protected by an AnkiHub_Protect::<Field> tag,
and clicking it toggles the tag.

This is a design mockup intended to be reviewed via screenshots before being
promoted to a finished feature.
"""

import json
from typing import Any, Tuple

import aqt
from aqt import gui_hooks
from aqt.editor import Editor

from .. import settings
from ..db import ankihub_db
from ..main.note_conversion import (
    TAG_FOR_PROTECTING_ALL_FIELDS,
    TAG_FOR_PROTECTING_FIELDS,
    get_fields_protected_by_tags,
)

_TOGGLE_MSG_PREFIX = "ankihub_toggle_protect_field:"

_LOCK_CLOSED_SVG = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
    '<path d="M17 8h-1V6a4 4 0 0 0-8 0v2H7a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V10a2 '
    '2 0 0 0-2-2Zm-5 10a2 2 0 1 1 0-4 2 2 0 0 1 0 4Zm3-10H9V6a3 3 0 0 1 6 0v2Z"/>'
    "</svg>"
)

_LOCK_OPEN_SVG = (
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="5" y="11" width="14" height="10" rx="2"/>'
    '<path d="M8 11V7a4 4 0 0 1 7.5-2"/>'
    '<circle cx="12" cy="16" r="1.2" fill="currentColor" stroke="none"/>'
    "</svg>"
)


def setup() -> None:
    gui_hooks.editor_did_load_note.append(_on_editor_did_load_note)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)


def _on_editor_did_load_note(editor: Editor) -> None:
    if editor is None or editor.web is None or editor.note is None:
        return
    if not ankihub_db.is_ankihub_note_type(editor.note.mid):
        return
    _inject_field_protection_ui(editor)


def _inject_field_protection_ui(editor: Editor) -> None:
    note = editor.note
    field_names = [name for name in note.keys() if name != settings.ANKIHUB_NOTE_TYPE_FIELD_NAME]
    protected_fields = get_fields_protected_by_tags(note)

    payload = json.dumps(
        {
            "fieldNames": field_names,
            "protectedFields": protected_fields,
        }
    )
    script = (
        _JS_TEMPLATE.replace("__DATA__", payload)
        .replace("__LOCK_CLOSED_SVG__", _LOCK_CLOSED_SVG)
        .replace("__LOCK_OPEN_SVG__", _LOCK_OPEN_SVG)
    )
    editor.web.eval(script)


def _on_js_message(handled: Tuple[bool, Any], message: str, context: Any) -> Tuple[bool, Any]:
    if not message.startswith(_TOGGLE_MSG_PREFIX):
        return handled

    try:
        field_idx = int(message[len(_TOGGLE_MSG_PREFIX) :])
    except ValueError:
        return handled

    editor = context if isinstance(context, Editor) else None
    if editor is None or editor.note is None:
        return (True, None)

    editor.call_after_note_saved(lambda: _toggle_field_protection(editor, field_idx), keepFocus=True)
    return (True, None)


def _toggle_field_protection(editor: Editor, field_idx: int) -> None:
    note = editor.note
    all_field_names = [n for n in note.keys() if n != settings.ANKIHUB_NOTE_TYPE_FIELD_NAME]
    if field_idx < 0 or field_idx >= len(all_field_names):
        return
    field_name = all_field_names[field_idx]
    currently_protected = get_fields_protected_by_tags(note)

    if field_name in currently_protected:
        new_protected = [f for f in currently_protected if f != field_name]
    else:
        new_protected = currently_protected + [field_name]

    # Strip any existing AnkiHub_Protect tags
    note.tags = [t for t in note.tags if not t.lower().startswith(f"{TAG_FOR_PROTECTING_FIELDS.lower()}")]

    if all_field_names and set(new_protected) == set(all_field_names):
        note.tags.append(TAG_FOR_PROTECTING_ALL_FIELDS)
    else:
        for fname in new_protected:
            note.tags.append(f"{TAG_FOR_PROTECTING_FIELDS}::{fname.replace(' ', '_')}")

    aqt.mw.col.update_note(note)
    editor.loadNote()


_JS_TEMPLATE = r"""
(function() {
    const data = __DATA__;

    if (!document.getElementById('ankihub-field-protection-style')) {
        const style = document.createElement('style');
        style.id = 'ankihub-field-protection-style';
        style.textContent = `
            .ankihub-lock-btn {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 2px 5px;
                margin: 0 2px;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                vertical-align: middle;
                color: var(--fg-subtle, #8a8a8a);
                opacity: 0.35;
                transition: opacity 0.12s ease, color 0.12s ease, background 0.12s ease;
            }
            .ankihub-lock-btn:hover {
                opacity: 1;
                background: var(--canvas-inset, rgba(127,127,127,0.15));
            }
            .ankihub-lock-btn.protected {
                opacity: 1;
                color: #f59e0b;
            }
            .ankihub-lock-btn.protected:hover {
                background: rgba(245, 158, 11, 0.12);
            }
            .ankihub-field-protected {
                box-shadow: inset 3px 0 0 0 #f59e0b;
            }
        `;
        document.head.appendChild(style);
    }

    const LOCK_CLOSED_SVG = `__LOCK_CLOSED_SVG__`;
    const LOCK_OPEN_SVG = `__LOCK_OPEN_SVG__`;

    function pickHeader(wrapper) {
        // Try common selectors used by Anki's editor field component
        return (
            wrapper.querySelector('.label-container') ||
            wrapper.querySelector('.field-state') ||
            wrapper.querySelector('[class*="label"]') ||
            wrapper.firstElementChild ||
            wrapper
        );
    }

    async function setupField(i, fieldName, isProtected) {
        const noteEditor = require('anki/NoteEditor').instances[0];
        if (!noteEditor || !noteEditor.fields[i]) return;

        let element;
        try {
            element = await noteEditor.fields[i].element;
        } catch (e) {
            return;
        }
        if (!element || !element.parentElement) return;

        const wrapper = element.parentElement.parentElement || element.parentElement;
        const header = pickHeader(wrapper);

        // Remove any stale button so re-injection stays idempotent
        const existing = header.querySelector('.ankihub-lock-btn');
        if (existing) existing.remove();

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'ankihub-lock-btn' + (isProtected ? ' protected' : '');
        btn.innerHTML = isProtected ? LOCK_CLOSED_SVG : LOCK_OPEN_SVG;
        btn.title = isProtected
            ? `"${fieldName}" is protected from AnkiHub updates — click to unprotect`
            : `Click to protect "${fieldName}" from AnkiHub updates`;
        btn.addEventListener('mousedown', (e) => {
            // Prevent the field from losing focus-then-saving twice
            e.preventDefault();
        });
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            pycmd('ankihub_toggle_protect_field:' + i);
        });
        header.appendChild(btn);

        if (isProtected) {
            wrapper.classList.add('ankihub-field-protected');
        } else {
            wrapper.classList.remove('ankihub-field-protected');
        }
    }

    function run() {
        for (let i = 0; i < data.fieldNames.length; i++) {
            const name = data.fieldNames[i];
            const isProtected = data.protectedFields.indexOf(name) !== -1;
            setupField(i, name, isProtected);
        }
    }

    if (window.require && require('anki/ui') && require('anki/ui').loaded) {
        require('anki/ui').loaded.then(() => setTimeout(run, 0));
    } else {
        setTimeout(run, 50);
    }
})();
"""
