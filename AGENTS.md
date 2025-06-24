# CLAUDE.md

This file provides guidance to AI coding agents when working with code in this repository.

## Development Commands

### Testing
- `pytest tests/addon` - Run addon tests (excludes sequential and performance tests)
- `pytest tests/addon -m sequential -n 0` - Run sequential tests (flaky when run in parallel)
- `pytest tests/addon -m performance -n 0` - Run performance tests
- `pytest tests/client` - Run client tests with VCR cassettes

### Code Quality
- `mypy` - Type checking

## Architecture Overview

### Anki Platform Architecture
**Frontend**: Built with PyQt (Qt widgets), webviews (Chromium-based QWebEngine), and Svelte components
**Backend**: Core logic implemented in Rust with Python bindings, some components remain in Python

**Key Python Packages**:
- `anki`: Core data layer, provides Collection class and Rust bindings for reading/writing Anki collections
- `aqt`: UI layer, manages interface and background task scheduling (depends on `anki` for data)

### AnkiHub Add-on Structure
This is an Anki addon that enables collaborative deck sharing through the AnkiHub platform. The architecture follows a layered approach with clear separation of concerns:

**Data Layer**: Separate AnkiHub SQLite database (using Peewee ORM) maintains sync state alongside Anki's collection database. Located in `ankihub/db/`.
**Client Layer**: HTTP client for AnkiHub API communication with typed data models. Located in `ankihub/ankihub_client/`.
**Business Logic**: Core operations for importing, syncing, suggestions, and media management. Located in `ankihub/main/`.
**GUI Layer**: Deep Anki UI integration using hooks and async operations. Located in `ankihub/gui/`.

### Key Architectural Patterns

**Multi-Profile Support**: Each Anki profile gets isolated AnkiHub database (UUID-based, supports profile renaming)
**Dual Database Design**: AnkiHub database tracks "source of truth" separately from local Anki modifications
**Hook-Based Integration**: Extensive use of Anki's hook system for non-invasive UI integration
**Async Operations**: Background tasks using `AddonQueryOp`, or `aqt.mw.taskman.run_in_background` for GUI responsiveness

### Hook System Patterns

Anki provides extensive hooks for extending functionality without modifying core code:

**Hook Examples**:
- `aqt.gui_hooks.reviewer_did_show_question` - Triggered when question is shown in reviewer
- `aqt.gui_hooks.webview_will_set_content` - Before webview content is set
- `anki.hooks.note_will_be_added` - Before a note is added to collection
- `aqt.gui_hooks.editor_did_init_buttons` - When editor buttons are initialized

**Usage Pattern**:
```python
def setup_editor_buttons(buttons, editor):
    # Customize editor buttons here
    pass

editor_did_init_buttons.append(setup_editor_buttons)
```

### Python-JavaScript Bridge

**Webview Communication**:
- Uses PyQt's QWebEngine (Chromium-based) for web content
- Bidirectional communication between Python and JavaScript:

**Python to JS**:
```python
web.eval("console.log('hello')")
```

**JS to Python**:
```javascript
// JavaScript side
pycmd("ankihub_reviewer_button_toggled");
```

```python
# Python handler
gui_hooks.webview_did_receive_js_message.append(handle_message)
```

### Core Modules

#### Entry Point (`ankihub/entry_point.py`)
- Two-phase initialization: general setup (once) and profile-specific setup (per profile)
- Manages database initialization, config migration, and UI hook registration

#### Database Layer (`ankihub/db/`)
- **Models**: `AnkiHubNote`, `AnkiHubNoteType`, `DeckMedia` using Peewee ORM
- **Migrations**: Database schema versioning in `db_migrations.py`
- **Profile Isolation**: Separate database per Anki profile using UUIDs

#### Client Layer (`ankihub/ankihub_client/`)
- Pure HTTP client for AnkiHub API
- Typed data models (`models.py`) for API responses
- Wrapper client (`addon_ankihub_client.py`) adds logging and addon-specific behavior

#### Business Logic (`ankihub/main/`)
- **importing.py**: Core logic for importing decks and changes to decks during sync
- **suggestions.py**: User-contributed content suggestions
- **note_type_management.py**: Synchronization of note types between AnkiHub and Anki
- **media_utils.py**: Media file management and synchronization
- **deck_creation.py**: Collaborative deck creation workflow

#### GUI Integration (`ankihub/gui/`)
- **operations/**: Async operations using `AddonQueryOp` pattern
- **browser/**: Custom browser columns, search nodes, context menus
- **Dialogs**: Suggestion dialog, config dialog, error dialogs
- **Hooks**: Integration points for menu, reviewer, editor, deck browser

### Data Flow

1. **Sync Process**: Check AnkiWeb → Download AnkiHub updates → Apply to both databases → Update UI
2. **Conflict Resolution**: AnkiHub database maintains original state, local modifications preserved
3. **Media Management**: Separate background sync for media files
4. **Suggestions**: User changes to Anki's DB can be submitted to AnkiHub

### Configuration System

**Dual Config**: Public config (user-editable) and private config (internal state)
**Environment Variables**: `ANKIHUB_APP_URL`, `S3_BUCKET_URL`, `GOOGLE_API_KEY`, `REPORT_ERRORS`
**Multi-Environment**: Staging vs production URLs configurable via environment

### Testing Strategy

**Addon Tests**: Full integration tests with Anki collection
**Client Tests**: HTTP client tests using VCR cassettes for reproducibility
**Performance Tests**: Specialized tests for large deck operations
**Sequential Tests**: Tests that must run alone due to timing issues

### Media Management

**Separate Modules**: `media_export/` and `media_import/` as independent submodules
**Background Sync**: Asynchronous media upload/download
**Multiple Sources**: Google Drive, MEGA, local files support

### Key Files for Common Tasks

- **Sync Logic**: `ankihub/main/importing.py`
- **API Client**: `ankihub/ankihub_client/ankihub_client.py`
- **Database Models**: `ankihub/db/models.py`
- **GUI Operations**: `ankihub/gui/operations/`
- **Configuration**: `ankihub/settings.py`
- **Entry Point**: `ankihub/entry_point.py`

### Development Notes

- Uses bundled libraries in `ankihub/lib/` (sentry_sdk, structlog, mashumaro, etc.)
- Supports both Qt5 and Qt6 (Anki 2.1.50+ compatibility)
- Extensive error handling and logging via structlog
- Multi-profile architecture requires careful state management
- Long running tasks need to be run in the background to avoid blocking Anki's UI
