import copy
import hashlib
import re
import time
from collections import defaultdict
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Any, Collection, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aqt
from anki.collection import EmptyCardsReport
from anki.decks import DeckId
from anki.models import ChangeNotetypeRequest, NoteType, NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.utils import checksum, ids2str
from aqt.emptycards import EmptyCardsDialog

from .. import LOGGER, settings
from ..db import ankihub_db
from ..settings import (
    ANKI_INT_VERSION,
    ANKI_VERSION_23_10_00,
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    config,
    url_mh_integrations_preview,
    url_view_note,
)
from .exceptions import ChangesRequireFullSyncError

if ANKI_INT_VERSION >= ANKI_VERSION_23_10_00:
    from anki.collection import AddNoteRequest

# The following constants are used to identify AnkiHub modifications in note types.
ANKIHUB_SNIPPET_MARKER = "ANKIHUB MODFICATIONS"
ANKIHUB_SNIPPET_RE = (
    f"<!-- BEGIN {ANKIHUB_SNIPPET_MARKER} -->"
    r"[\w\W]*"
    f"<!-- END {ANKIHUB_SNIPPET_MARKER} -->"
)

# The end comment is used to allow users to add their own content below it without it being overwritten
# when the template is updated.
ANKIHUB_HTML_END_COMMENT = (
    "<!--\n"
    "ANKIHUB_END\n"
    "Text below this comment will not be modified by AnkiHub or AnKing add-ons.\n"
    "Do not edit or remove this comment if you want to protect the content below.\n"
    "-->"
)
ANKIHUB_CSS_END_COMMENT = (
    "/*\n"
    "ANKIHUB_END\n"
    "Text below this comment will not be modified by AnkiHub or AnKing add-ons.\n"
    "Do not edit or remove this comment if you want to protect the content below.\n"
    "*/"
)

ANKIHUB_HTML_END_COMMENT_RE = re.compile(
    rf"{re.escape(ANKIHUB_HTML_END_COMMENT)}(?P<text_to_migrate>[\w\W]*)"
)
ANKIHUB_CSS_COMMENT_RE = re.compile(
    rf"{re.escape(ANKIHUB_CSS_END_COMMENT)}(?P<text_to_migrate>[\w\W]*)"
)

# decks


def create_deck_with_id(deck_name: str, deck_id: DeckId) -> None:
    source_did = aqt.mw.col.decks.add_normal_deck_with_name(
        get_unique_ankihub_deck_name(deck_name)
    ).id
    aqt.mw.col.db.execute(f"UPDATE decks SET id={deck_id} WHERE id={source_did};")
    aqt.mw.col.db.execute(f"UPDATE cards SET did={deck_id} WHERE did={source_did};")
    aqt.mw.col.save()

    LOGGER.info("Created deck.", deck_name=deck_name, deck_id=deck_id)


def all_dids() -> Set[DeckId]:
    return {DeckId(x.id) for x in aqt.mw.col.decks.all_names_and_ids()}


def lowest_level_common_ancestor_did(dids: Iterable[DeckId]) -> Optional[DeckId]:
    return aqt.mw.col.decks.id_for_name(
        lowest_level_common_ancestor_deck_name(
            [aqt.mw.col.decks.name(did) for did in dids]
        )
    )


def lowest_level_common_ancestor_deck_name(deck_names: Iterable[str]) -> Optional[str]:
    lowest_level_deck_name = max(deck_names, key=lambda name: name.count("::"))
    parts = lowest_level_deck_name.split("::")
    result = lowest_level_deck_name
    for i in range(1, len(parts) + 1):
        cur_deck_name = "::".join(parts[:i])
        if any(not name.startswith(cur_deck_name) for name in deck_names):
            result = "::".join(parts[: i - 1])
            break

    if result == "":
        return None
    else:
        return result


def dids_of_notes(notes: List[Note]) -> Set[DeckId]:
    result: Set[DeckId] = set()
    for note in notes:
        dids_for_note = set(c.did for c in note.cards())
        result |= dids_for_note
    return result


def get_unique_ankihub_deck_name(deck_name: str) -> str:
    """Returns the passed deck_name if it is unique, otherwise returns a unique version of it
    by adding a suffix."""
    if not aqt.mw.col.decks.by_name(deck_name):
        return deck_name

    result = f"{deck_name} (AnkiHub)"
    if aqt.mw.col.decks.by_name(result) is not None:
        result += f" {checksum(str(time.time()))[:5]}"
    return result


def highest_level_did(dids: Iterable[DeckId]) -> DeckId:
    return min(dids, key=lambda did: aqt.mw.col.decks.name(did).count("::"))


def note_types_with_ankihub_id_field() -> List[NotetypeId]:
    return [
        mid
        for mid in aqt.mw.col.models.ids()
        if has_ankihub_id_field(aqt.mw.col.models.get(mid))
    ]


def has_ankihub_id_field(model: NotetypeDict) -> bool:
    return any(field["name"] == ANKIHUB_NOTE_TYPE_FIELD_NAME for field in model["flds"])


def nids_in_deck_but_not_in_subdeck(deck_name: str) -> Sequence[NoteId]:
    """Return note IDs of notes that are in the deck but not also in a subdeck of the deck.
    For example if a notes is in the deck "A" but not in "A::B" or "A::C" then it is returned.
    """
    return aqt.mw.col.find_notes(f'deck:"{deck_name}" -deck:"{deck_name}::*"')


# notes


def add_notes(notes: Collection[Note], deck_id: DeckId) -> None:
    """Add notes to the Anki database in an efficient way."""
    if ANKI_INT_VERSION >= ANKI_VERSION_23_10_00:
        add_note_requests = [AddNoteRequest(note, deck_id=deck_id) for note in notes]
        aqt.mw.col.add_notes(add_note_requests)
    else:
        # Anki versions before 23.10 don't have col.add_notes, so we have to add them one by one.
        # It's ok to this because adding them one by one is fast on Anki versions before 23.10.
        for note in notes:
            aqt.mw.col.add_note(note, deck_id=deck_id)
        aqt.mw.col.save()


def move_notes_to_decks_while_respecting_odid(nid_to_did: Dict[NoteId, DeckId]) -> None:
    """Moves the cards of notes to the decks specified in nid_to_did.
    If a card is in a filtered deck it is not moved and only its original deck id value gets changed.
    """
    cards_to_update = []
    for nid, did in nid_to_did.items():
        # This is a bit faster than aqt.mw.col.get_note(nid).cards() to get the cards of a note.
        cids = aqt.mw.col.db.list(f"SELECT id FROM cards WHERE nid={nid}")
        cards = [aqt.mw.col.get_card(cid) for cid in cids]
        for card in cards:
            if card.odid == 0:
                card.did = did
            else:
                card.odid = did
            cards_to_update.append(card)
    aqt.mw.col.update_cards(cards_to_update)


# cards


def clear_empty_cards() -> None:
    """Delete empty cards from the database.
    Uses the EmptyCardsDialog to delete empty cards without showing the dialog."""

    def on_done(future: Future) -> None:
        # This uses the EmptyCardsDialog to delete empty cards without showing the dialog.
        report: EmptyCardsReport = future.result()
        if not report.notes:
            LOGGER.info("No empty cards found.")
            return
        dialog = EmptyCardsDialog(aqt.mw, report)
        deleted_amount = dialog._delete_cards(keep_notes=True)
        LOGGER.info("Deleted empty cards.", deleted_amount=deleted_amount)

    aqt.mw.taskman.run_in_background(aqt.mw.col.get_empty_cards, on_done=on_done)


# note types
def create_note_type_with_id(note_type: NotetypeDict, mid: NotetypeId) -> None:
    note_type_copy = copy.deepcopy(note_type)
    note_type_copy["id"] = 0
    changes = aqt.mw.col.models.add_dict(note_type_copy)

    # Swap out the note type id that Anki assigns to the new note type with our own id.
    # TODO check if seperate statements are necessary
    aqt.mw.col.db.execute(f"UPDATE notetypes SET id={mid} WHERE id={changes.id};")
    aqt.mw.col.db.execute(f"UPDATE templates SET ntid={mid} WHERE ntid={changes.id};")
    aqt.mw.col.db.execute(f"UPDATE fields SET ntid={mid} WHERE ntid={changes.id};")
    aqt.mw.col.models._clear_cache()  # TODO check if this is necessary
    aqt.mw.col.save()

    LOGGER.info(
        "Created note type.", note_type_name=note_type["name"], note_type_id=mid
    )


def note_type_contains_field(
    note_type: NoteType, field=settings.ANKIHUB_NOTE_TYPE_FIELD_NAME
) -> bool:
    """Check that a field is defined in the note type."""
    fields: List[Dict] = note_type["flds"]
    field_names = [field["name"] for field in fields]
    return field in field_names


def get_note_types_in_deck(did: DeckId) -> List[NotetypeId]:
    """Returns list of note model ids in the given deck."""
    dids = [did]
    dids += [child[1] for child in aqt.mw.col.decks.children(did)]
    dids_str = ids2str(dids)
    # odid is the original did for cards in filtered decks
    query = (
        "SELECT DISTINCT mid FROM cards "
        "INNER JOIN notes ON cards.nid = notes.id "
        f"WHERE did in {dids_str} or odid in {dids_str}"
    )
    return aqt.mw.col.db.list(query)


def change_note_types_of_notes(
    nid_mid_pairs: List[Tuple[NoteId, NotetypeId]], raise_if_full_sync_required=False
) -> None:
    """Changes the note type of notes based on provided pairs of note id and target note type id."""

    # Group notes by source and target note type
    notes_grouped_by_type_change: Dict[
        Tuple[NotetypeId, NotetypeId], List[NoteId]
    ] = defaultdict(list)
    for nid, mid in nid_mid_pairs:
        current_mid = aqt.mw.col.db.scalar(f"SELECT mid FROM notes WHERE id={nid}")
        if current_mid is None:
            continue

        if current_mid != mid:
            notes_grouped_by_type_change[(current_mid, mid)].append(nid)

    if raise_if_full_sync_required and notes_grouped_by_type_change:
        affected_note_type_ids = set(
            target_note_type_id
            for _, target_note_type_id in notes_grouped_by_type_change.keys()
        )
        LOGGER.info(
            "Changing note types of notes requires full sync.",
            affected_note_type_ids=affected_note_type_ids,
        )
        raise ChangesRequireFullSyncError(affected_note_type_ids=affected_note_type_ids)

    # Change note types of notes for each group
    for (
        source_note_type_id,
        target_note_type_id,
    ), note_ids in notes_grouped_by_type_change.items():
        current_schema = collection_schema()
        target_note_type = aqt.mw.col.models.get(NotetypeId(target_note_type_id))
        request = ChangeNotetypeRequest(
            note_ids=note_ids,
            old_notetype_id=source_note_type_id,
            new_notetype_id=target_note_type_id,
            current_schema=current_schema,
            new_fields=list(range(0, len(target_note_type["flds"]))),
        )
        aqt.mw.col.models.change_notetype_of_notes(request)
        LOGGER.debug(
            "Changed note type of notes.",
            source_note_type_id=source_note_type_id,
            target_note_type_id=target_note_type_id,
        )
    LOGGER.info("Changed note types of notes.")


def mids_of_notes(nids: Sequence[NoteId]) -> Set[NotetypeId]:
    """Returns the note type ids of the given notes."""
    return set(
        aqt.mw.col.db.list(
            f"SELECT DISTINCT mid FROM notes WHERE id in {ids2str(nids)}"
        )
    )


def retain_nids_with_ah_note_type(nids: Collection[NoteId]) -> Collection[NoteId]:
    """Return nids that have an AnkiHub note type. Other nids are not included in the result."""
    nids_to_mids = get_anki_nid_to_mid_dict(nids)
    mid_to_is_ankihub_note_type = {
        mid: ankihub_db.is_ankihub_note_type(mid) for mid in set(nids_to_mids.values())
    }
    result = [
        nid for nid, mid in nids_to_mids.items() if mid_to_is_ankihub_note_type[mid]
    ]
    return result


def get_anki_nid_to_mid_dict(nids: Collection[NoteId]) -> Dict[NoteId, NotetypeId]:
    result = {
        id_: mid
        for id_, mid in aqt.mw.col.db.execute(
            f"select id, mid from notes where id in {ids2str(nids)}"
        )
    }
    return result


# ... note type modifications


def note_type_name_without_ankihub_modifications(name: str) -> str:
    return re.sub(r"(\s*\([^()]+ / [^()]+\))+\s*$", "", name)


def modified_ankihub_note_type_name(note_type_name: str, deck_name) -> str:
    name_without_modifications = note_type_name_without_ankihub_modifications(
        note_type_name
    )
    name = f"{name_without_modifications} ({deck_name} / {config.username_or_email()})"
    return name


def modified_note_type(note_type: NotetypeDict) -> NotetypeDict:
    """Returns a modified version of the note type with the AnkiHub field added and
    the card templates updated."""
    note_type = copy.deepcopy(note_type)

    _modify_fields(note_type)

    return note_type_with_updated_templates_and_css(
        old_note_type=note_type,
        new_note_type=None,
    )


def _modify_fields(note_type: Dict) -> None:
    fields = note_type["flds"]
    field_names = [field["name"] for field in fields]
    if settings.ANKIHUB_NOTE_TYPE_FIELD_NAME in field_names:
        LOGGER.info(
            "AnkiHub field already exists in note type.",
            note_type_name=note_type["name"],
            note_type_id=note_type["id"],
        )
        return
    ankihub_field = aqt.mw.col.models.new_field(ANKIHUB_NOTE_TYPE_FIELD_NAME)
    # Put AnkiHub field last
    ankihub_field["ord"] = len(fields)
    note_type["flds"].append(ankihub_field)


def modify_note_type_templates(note_type_ids: Iterable[NotetypeId]) -> None:
    for mid in note_type_ids:
        note_type = aqt.mw.col.models.get(mid)
        note_type = note_type_with_updated_templates_and_css(
            old_note_type=note_type,
            new_note_type=None,
        )
        aqt.mw.col.models.update_dict(note_type)


def _template_side_with_view_on_ankihub_snippet(template_side: str) -> str:
    """Return template html with the AnkiHub view note snippet added to it."""
    snippet = dedent(
        f"""
        <!-- BEGIN {ANKIHUB_SNIPPET_MARKER} -->
        {{{{#{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}
        <a class='ankihub-view-note'
            href='{url_view_note()}{{{{{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}'>
            View Note on AnkiHub
        </a>

        <style>
        .ankihub-view-note {{
            display: none;
        }}

        .mobile .ankihub-view-note
          {{
            display: block;
            left: 50%;
            margin-right: -50%;
            padding: 8px;
            border-radius: 50px;
            background-color: #cde3f8;
            font-size: 12px;
            color: black;
            text-decoration: none;
        }}

        /* AnkiDroid (Android)
        The button is fixed to the bottom of the screen. */
        .android .ankihub-view-note {{
            position: fixed;
            bottom: 5px;
            transform: translate(-50%, -50%);
        }}

        /* AnkiMobile (IPhone)
        position: fixed doesn't work on AnkiMobile, so the button is just below the content instead. */
        .iphone .ankihub-view-note,
        .ipad .ankihub-view-note {{
            position: relative;
            transform: translate(-50%, 0);
            width: fit-content;
            margin-top: 20px;
        }}
        </style>

        <script>
            if(document.querySelector("html").classList.contains("android")) {{
                // Add a margin to the bottom of the card content so that the button doesn't
                // overlap the content.
                var container = document.querySelector('#qa');
                var button = document.querySelector('.ankihub-view-note');
                container.style.marginBottom = 2 * button.offsetHeight + "px";
            }}
        </script>

        {{{{/{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}
        <!-- END {ANKIHUB_SNIPPET_MARKER} -->
        """
    ).strip("\n")

    snippet_pattern = (
        f"<!-- BEGIN {ANKIHUB_SNIPPET_MARKER} -->"
        r"[\w\W]*"
        f"<!-- END {ANKIHUB_SNIPPET_MARKER} -->"
    )

    if not re.search(snippet_pattern, template_side):
        return template_side.rstrip("\n ") + "\n\n" + snippet
    else:
        # update existing snippet to make sure it is up to date
        return re.sub(
            snippet_pattern,
            snippet,
            template_side,
        )


def note_type_with_updated_templates_and_css(
    old_note_type: NotetypeDict,
    new_note_type: Optional[NotetypeDict],
) -> NotetypeDict:
    """Returns the updated note type with modifications applied to the card templates and css.
    The templates and css of the new note type are used as the base if it is provided.

    The modifications are as follows:
    - The View on AnkiHub button is added to the back side of each template.
    - Contents below the AnkiHub end comments are preserved when the template/css is updated.

    Args:
        old_note_type (NotetypeDict): The old note tpye. The contents below the AnkiHub end comments are preserved.
        new_note_type (Optional[NotetypeDict]): The new note type. If provided, the templates and css are updated
        based on this.

    Returns:
        NotetypeDict: The updated note type.
    """

    updated_templates = []
    new_template_amount = (
        len(new_note_type["tmpls"]) if new_note_type else len(old_note_type["tmpls"])
    )
    for template_idx in range(new_template_amount):
        if template_idx < len(old_note_type["tmpls"]):
            old_template = old_note_type["tmpls"][template_idx]
        else:
            old_template = None

        if new_note_type:
            new_template = new_note_type["tmpls"][template_idx]
            updated_template = copy.deepcopy(new_template)
        else:
            new_template = None
            updated_template = copy.deepcopy(old_template)

        for template_side_name in ["qfmt", "afmt"]:
            updated_template[template_side_name] = _updated_note_type_content(
                old_content=old_template[template_side_name] if old_template else None,
                new_content=new_template[template_side_name] if new_template else None,
                add_view_on_ankihub_snippet=template_side_name == "afmt",
                content_type="html",
            )
        updated_templates.append(updated_template)

    result = copy.deepcopy(old_note_type)
    result["tmpls"] = updated_templates

    result["css"] = _updated_note_type_content(
        old_content=old_note_type["css"],
        new_content=new_note_type["css"] if new_note_type is not None else None,
        add_view_on_ankihub_snippet=False,
        content_type="css",
    )

    return result


def _updated_note_type_content(
    old_content: Optional[str],
    new_content: Optional[str],
    add_view_on_ankihub_snippet: bool,
    content_type: str,
) -> str:
    """Returns updated content with preserved content below ankihub end comment.

    Args:
      old_content: Original content to preserve custom additions from
      new_content: New base content to use, or None to use old_content
      add_view_on_ankihub_snippet: Whether to add AnkiHub view button
      content_type: Either "html" or "css" to determine comment style
    """
    assert old_content is not None or new_content is not None

    if content_type == "html":
        end_comment = ANKIHUB_HTML_END_COMMENT
        end_comment_pattern = ANKIHUB_HTML_END_COMMENT_RE
    else:
        end_comment = ANKIHUB_CSS_END_COMMENT
        end_comment_pattern = ANKIHUB_CSS_COMMENT_RE

    if old_content:
        m = re.search(end_comment_pattern, old_content)
        text_to_migrate = m.group("text_to_migrate") if m else ""
    else:
        text_to_migrate = ""

    # Choose the base for the result
    result = new_content if new_content is not None else old_content

    # Remove end comment and content below it.
    # It will be added back below.
    result = re.sub(end_comment_pattern, "", result)

    if add_view_on_ankihub_snippet:
        result = _template_side_with_view_on_ankihub_snippet(result)

    # Add the AnkiHub end comment and the content below it back.
    return (
        result.rstrip("\n ")
        + "\n\n"
        + end_comment
        + "\n"
        + text_to_migrate.strip("\n ")
    )


# ... undo modifications
def undo_note_type_modfications(note_type_ids: Iterable[NotetypeId]) -> None:
    for mid in note_type_ids:
        note_type = aqt.mw.col.models.get(mid)
        if note_type is None:
            continue

        note_type = note_type_without_ankihub_modifications(note_type)
        aqt.mw.col.models.update_dict(note_type)

        LOGGER.info(
            "Removed AnkiHub modifications from note type.",
            note_type_name=note_type["name"],
            note_type_id=note_type["id"],
        )


def note_type_without_ankihub_modifications(note_type: NotetypeDict) -> NotetypeDict:
    note_type = note_type_without_template_and_style_modifications(note_type)
    remove_ankihub_id_field(note_type)
    return note_type


def remove_ankihub_id_field(note_type: Dict) -> None:
    fields = note_type["flds"]
    field_names = [field["name"] for field in fields]
    if settings.ANKIHUB_NOTE_TYPE_FIELD_NAME not in field_names:
        return
    fields.pop(field_names.index(settings.ANKIHUB_NOTE_TYPE_FIELD_NAME))


def note_type_without_template_and_style_modifications(
    note_type: Dict[str, Any],
) -> NotetypeDict:
    note_type = copy.deepcopy(note_type)
    note_type["css"] = ANKIHUB_CSS_COMMENT_RE.sub("", note_type["css"]).strip()
    for template in note_type["tmpls"]:
        template["qfmt"] = ANKIHUB_HTML_END_COMMENT_RE.sub("", template["qfmt"]).strip()
        template["afmt"] = re.sub(
            r"\n{0,2}" + ANKIHUB_SNIPPET_RE,
            "",
            template["afmt"],
        )
        template["afmt"] = ANKIHUB_HTML_END_COMMENT_RE.sub("", template["afmt"]).strip()

    return note_type


# backup
def create_backup() -> None:
    # has to be called from a background thread
    LOGGER.info("Starting backup...")
    try:
        created: Optional[bool] = None
        if ANKI_INT_VERSION >= 50:
            # if there were no changes since the last backup, no backup is created
            created = _create_backup_with_retry_anki_50()
            LOGGER.info("Backup successful.", created=created)
        else:
            aqt.mw.col.close(downgrade=False)
            aqt.mw.backup()  # type: ignore
            aqt.mw.col.reopen(after_full_sync=False)
            # here we don't know if the backup was created
            LOGGER.info("Backup successful.")
    except Exception as exc:
        LOGGER.info("Backup failed")
        raise exc


def _create_backup_with_retry_anki_50() -> bool:
    """Create a backup and retry once if it raises an exception."""
    # The backup can fail with DBError("Cannot start transaction within a transaction")
    # when aqt.mw.autosave() is called while the backup is running.
    # This can happen for example when an aqt.operations.CollectionOp is executed in another thread. After the
    # CollectionOp is finished, autosave is called, which calls mw.db.begin().
    # When the backup is finished, it also calls mw.db.begin() which raises an exception
    # if begin() was already called and no mw.db.commit() or mw.db.rollback() was called in between.
    # See https://ankihub.sentry.io/issues/3801328076/?project=6546414.
    # There are other ways to handle this problem, but this seems like a safe one in the sense that it doesn't
    # rely on the implementation of the backup function staying the same.
    try:
        created = _create_backup_anki_50()
    except Exception as exc:
        LOGGER.info("Backup failed on the first attempt.", exc_info=exc)

        # retry once
        LOGGER.info("Retrying backup...")
        try:
            created = _create_backup_anki_50()
        except Exception as exc:
            LOGGER.info("Backup failed second time")
            raise exc

    return created


def _create_backup_anki_50() -> bool:
    """Create a backup and return whether it was created or not.
    Works for Anki 2.1.50 and newer."""
    created = aqt.mw.col.create_backup(
        backup_folder=aqt.mw.pm.backupFolder(),
        force=True,
        wait_for_completion=True,
    )
    return created


def truncated_list(values: List[Any], limit: int = 10) -> List[Any]:
    assert limit > 0
    return values[:limit] + ["..."] if len(values) > limit else values


def md5_file_hash(media_path: Path) -> str:
    """Return the md5 hash of the file content of the given media file."""
    with media_path.open("rb") as media_file:
        file_content_hash = hashlib.md5(media_file.read())
    result = file_content_hash.hexdigest()
    return result


def truncate_string(string: str, limit: int) -> str:
    assert limit > 0
    return string[:limit] + "..." if len(string) > limit else string


def is_tag_in_list(tag: str, tags: List[str]) -> bool:
    # Copied from anki.tags.TagManager.in_list in order to be able to use it without instantiating anki.tags.TagManager
    "True if TAG is in TAGS. Ignore case."
    return tag.lower() in [tag.lower() for tag in tags]


def collection_schema() -> int:
    return aqt.mw.col.db.scalar("select scm from col")


@dataclass(frozen=True)
class Resource:
    title: str
    url: str
    usmle_step: int


def mh_tag_to_resource(tag: str) -> Optional[Resource]:
    """Converts a McGrawHill tag to a title and URL for the MH resource preview.

    Example:
    #AK_Step1_v12::#B&B::03_Biochem::03_Amino_Acids::04_Ammonia
    -> ('Ammonia', 'step1-bb-3-3-4')
    """
    try:
        step = int(re.match(r"#AK_Step(\d+)_v12::", tag, re.IGNORECASE).group(1))
        resource_type_str = re.search(r"_v12::#(.+?)::", tag, re.IGNORECASE).group(1)
        resource_slug_str = {"b&b": "bb", "firstaid": "fa"}[resource_type_str.lower()]
        path = re.sub(r".+_v12::#.+?::", "", tag, re.IGNORECASE)
        path_parts = path.split("::")

        # Remove path parts after first path part starting with "*"
        for index, part in enumerate(path_parts):
            if part.startswith("*"):
                path_parts = path_parts[:index]
                break

        if path_parts[-1].lower() == "extra":
            path_parts = path_parts[:-1]

        path_parts = [part.lower() for part in path_parts]
        content_numbers = [
            str(int(re.match(r"\d+", part).group(0))) for part in path_parts
        ]
        slug = f"step{step}-{resource_slug_str}-{'-'.join(content_numbers)}"

        title = re.sub(r"\d+_", "", path_parts[-1])
        title = title.replace("_", " ")
        title = " ".join([word.capitalize() for word in title.split()])
    except (KeyError, AttributeError, IndexError, ValueError):
        # We want to ignore any tags that don't match the expected format
        return None

    return Resource(title=title, url=url_mh_integrations_preview(slug), usmle_step=step)
