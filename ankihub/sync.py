import uuid
from concurrent.futures import Future
from pprint import pformat
from time import sleep
from typing import Dict, Iterable, List, Optional, Set, Tuple

from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from aqt import gui_hooks, mw
from aqt.utils import showInfo, tooltip

from . import LOGGER, constants
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubRequestError, FieldUpdate, NoteUpdate
from .config import config
from .constants import ANKI_MINOR
from .db import AnkiHubDB
from .utils import (
    create_backup,
    create_deck_with_id,
    create_note_type_with_id,
    create_note_with_id,
    get_unique_deck_name,
    lowest_level_common_ancestor_did,
    modify_note_type_templates,
    reset_note_types_of_notes,
)

INTERNAL_TAG_PREFIX = "AnkiHub_"

TAG_FOR_PROTECTING_FIELDS = f"{INTERNAL_TAG_PREFIX}Protect"
TAG_FOR_PROTECTING_ALL_FIELDS = f"{TAG_FOR_PROTECTING_FIELDS}::All"

# top-level tags that are only used by the add-on, but not by the web app
ADDON_INTERNAL_TAGS = [
    TAG_FOR_PROTECTING_FIELDS,
]


def is_internal_tag(tag: str) -> bool:
    return any(
        tag == internal_tag or tag.startswith(f"{internal_tag}::")
        for internal_tag in ADDON_INTERNAL_TAGS
    )


class AnkiHubSync:
    def __init__(self):
        self.importer = AnkiHubImporter()

    def sync_all_decks(self) -> None:
        LOGGER.debug("Trying to sync with AnkiHub.")

        create_backup()

        for ankihub_did, deck_info in config.private_config.decks.items():
            try:
                should_continue = self._sync_deck(ankihub_did)
                if not should_continue:
                    return
            except AnkiHubRequestError as e:
                if self._handle_exception(e, ankihub_did, deck_info):
                    return
                else:
                    raise e

    def _sync_deck(self, ankihub_did: str) -> bool:
        deck = config.private_config.decks[ankihub_did]
        client = AnkiHubClient()
        notes_data = []
        for chunk in client.get_deck_updates(
            uuid.UUID(ankihub_did), since=deck["latest_update"]
        ):
            if mw.progress.want_cancel():
                LOGGER.debug("User cancelled sync.")
                return False

            if chunk.notes:
                notes_data += chunk.notes

        if notes_data:
            self.importer.import_ankihub_deck(
                ankihub_did=ankihub_did,
                notes_data=notes_data,
                deck_name=deck["name"],
                local_did=deck["anki_id"],
                protected_fields=chunk.protected_fields,
                protected_tags=chunk.protected_tags,
            )
            config.save_latest_update(ankihub_did, chunk.latest_update)
        else:
            LOGGER.debug(f"No new updates to sync for {ankihub_did=}")

        return True

    def _handle_exception(
        self, exc: AnkiHubRequestError, ankihub_did: str, deck_info: Dict
    ) -> bool:
        # returns True if the exception was handled

        if "/updates" not in exc.response.url:
            return False

        if exc.response.status_code == 403:
            url_view_deck = f"{constants.URL_VIEW_DECK}{ankihub_did}"
            mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"Please subscribe to the deck <br><b>{deck_info['name']}</b><br>on the AnkiHub website to "
                    "be able to sync.<br><br>"
                    f'Link to the deck: <a href="{url_view_deck}">{url_view_deck}</a><br><br>'
                    f"Note that you also need an active AnkiHub membership.",
                )
            )
            LOGGER.debug(
                "Unable to sync because of user not being subscribed to a deck."
            )
            return True
        elif exc.response.status_code == 404:
            mw.taskman.run_on_main(
                lambda: showInfo(  # type: ignore
                    f"The deck \"{deck_info['name']}\" does not exist on the AnkiHub website. "
                    f"Remove it from the subscribed decks to be able to sync.<br><br>"
                    f"deck id: <i>{ankihub_did}</i>",
                )
            )
            LOGGER.debug("Unable to sync because the deck doesn't exist on AnkiHub.")
            return True
        return False


class AnkiHubImporter:
    def __init__(self):
        self.num_notes_updated = 0
        self.num_notes_created = 0

    def import_ankihub_deck(
        self,
        ankihub_did: str,
        notes_data: List[NoteUpdate],
        deck_name: str,  # name that will be used for a deck if a new one gets created
        local_did: Optional[  # did that new notes should be put into if importing not for the first time
            DeckId
        ] = None,
        protected_fields: Optional[
            Dict[int, List[str]]
        ] = None,  # will be fetched from api if not provided
        protected_tags: Optional[
            List[str]
        ] = None,  # will be fetched from api if not provided
    ) -> Optional[DeckId]:
        """
        Used for importing an ankihub deck and updates to an ankihub deck
        When no local_did is provided this function assumes that the deck gets installed for the first time
        Returns id of the deck future cards should be imported into - the local_did - if the import was sucessful
        else it returns None
        """

        LOGGER.debug(f"Importing ankihub deck {deck_name=} {local_did=}")

        remote_note_types = fetch_remote_note_types_based_on_notes_data(notes_data)

        if protected_fields is None:
            protected_fields = {}
            client = AnkiHubClient()
            try:
                protected_fields = client.get_protected_fields(uuid.UUID(ankihub_did))
            except AnkiHubRequestError as e:
                if not e.response.status_code == 404:
                    raise e

        if protected_tags is None:
            protected_tags = []
            client = AnkiHubClient()
            try:
                protected_tags = client.get_protected_tags(uuid.UUID(ankihub_did))
            except AnkiHubRequestError as e:
                if not e.response.status_code == 404:
                    raise e

        anki_deck_id = self._import_ankihub_deck_inner(
            ankihub_did=ankihub_did,
            notes_data=notes_data,
            deck_name=deck_name,
            remote_note_types=remote_note_types,
            protected_fields=protected_fields,
            protected_tags=protected_tags,
            local_did=local_did,
        )
        return anki_deck_id

    def _import_ankihub_deck_inner(
        self,
        ankihub_did: str,
        notes_data: List[NoteUpdate],
        deck_name: str,  # name that will be used for a deck if a new one gets created
        remote_note_types: Dict[NotetypeId, NotetypeDict],
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
        local_did: DeckId = None,  # did that new notes should be put into if importing not for the first time
    ) -> DeckId:

        first_import_of_deck = local_did is None

        local_did = adjust_deck(deck_name, local_did)
        adjust_note_types(remote_note_types)
        reset_note_types_of_notes_based_on_notes_data(notes_data)

        dids: Set[DeckId] = set()  # set of ids of decks notes were imported into
        for note_data in notes_data:
            LOGGER.debug(f"Trying to update or create note:\n {pformat(note_data)}")
            note = self.update_or_create_note(
                anki_nid=NoteId(note_data.anki_nid),
                mid=NotetypeId(note_data.mid),
                ankihub_nid=str(note_data.ankihub_note_uuid),
                fields=note_data.fields,
                tags=note_data.tags,
                anki_did=local_did,
                protected_fields=protected_fields,
                protected_tags=protected_tags,
            )
            dids_for_note = set(c.did for c in note.cards())
            dids = dids | dids_for_note

        if first_import_of_deck:
            local_did = self._cleanup_first_time_deck_import(dids, local_did)

        db = AnkiHubDB()
        anki_nids = [NoteId(note_data.anki_nid) for note_data in notes_data]
        db.save_notes_from_nids(ankihub_did=ankihub_did, nids=anki_nids)

        return local_did

    def _cleanup_first_time_deck_import(
        self, dids_cards_were_imported_to: Iterable[DeckId], created_did: DeckId
    ) -> Optional[DeckId]:
        dids = set(dids_cards_were_imported_to)

        # remove "Custom Study" decks from dids
        dids = {did for did in dids if not mw.col.decks.is_filtered(did)}

        # if there is a single deck where all the existing cards were before the import,
        # move the new cards there (from the newly created deck) and remove the created deck
        # takes subdecks into account
        if (dids_wh_created := dids - set([created_did])) and (
            (common_ancestor_did := lowest_level_common_ancestor_did(dids_wh_created))
        ) is not None:
            cids = mw.col.find_cards(f'deck:"{mw.col.decks.name(created_did)}"')
            mw.col.set_deck(cids, common_ancestor_did)
            LOGGER.debug(
                f"Moved new cards to common ancestor deck {common_ancestor_did=}"
            )

            mw.col.decks.remove([created_did])
            LOGGER.debug(f"Removed created deck {created_did=}")
            return common_ancestor_did

        return created_did

    def update_or_create_note(
        self,
        anki_nid: NoteId,
        ankihub_nid: str,
        fields: List[FieldUpdate],
        tags: List[str],
        mid: NotetypeId,
        anki_did: DeckId,  # only relevant for newly created notes
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
    ) -> Note:
        try:
            note = mw.col.get_note(id=anki_nid)
            fields.append(
                FieldUpdate(
                    name=constants.ANKIHUB_NOTE_TYPE_FIELD_NAME,
                    order=len(fields),
                    value=ankihub_nid,
                )
            )
            if self.prepare_note(
                note,
                ankihub_nid,
                fields,
                tags,
                protected_fields,
                protected_tags,
            ):
                mw.col.update_note(note)
                self.num_notes_updated += 1
                LOGGER.debug(f"Updated note: {anki_nid=}")
            else:
                LOGGER.debug(f"No changes, skipping {anki_nid=}")
        except NotFoundError:
            note_type = mw.col.models.get(NotetypeId(mid))
            note = mw.col.new_note(note_type)
            self.prepare_note(
                note,
                ankihub_nid,
                fields,
                tags,
                protected_fields,
                protected_tags,
            )
            note = create_note_with_id(note, anki_id=anki_nid, anki_did=anki_did)
            self.num_notes_created += 1
            LOGGER.debug(f"Created note: {anki_nid=}")
        return note

    def prepare_note(
        self,
        note: Note,
        ankihub_nid: str,
        fields: List[FieldUpdate],
        tags: List[str],
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
    ) -> bool:
        """
        Updates the note with the given fields and tags (taking protected fields and tags into account)
        Sets the ankihub_id field to the given ankihub_id
        Returns True if note was changed and False otherwise
        """

        LOGGER.debug("Preparing note...")

        if TAG_FOR_PROTECTING_ALL_FIELDS in note.tags:
            LOGGER.debug("Skipping note because it is protected by a tag.")
            return False

        changed_ankihub_id_field = self._prepare_ankihub_id_field(
            note, ankihub_nid=ankihub_nid
        )
        changed_fields = self._prepare_fields(
            note, fields=fields, protected_fields=protected_fields
        )
        changed_tags = self._prepare_tags(
            note,
            tags=tags,
            protected_tags=protected_tags,
        )
        changed = changed_ankihub_id_field or changed_fields or changed_tags

        LOGGER.debug(f"Prepared note. {changed=}")
        return changed

    def _prepare_ankihub_id_field(self, note: Note, ankihub_nid: str) -> bool:
        if note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME] != ankihub_nid:
            LOGGER.debug(
                f"AnkiHub id of note {note.id} will be changed from {note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME]} "
                f"to {ankihub_nid}",
            )
            note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME] = ankihub_nid
            return True
        return False

    def _prepare_fields(
        self,
        note: Note,
        fields: List[FieldUpdate],
        protected_fields: Dict[int, List[str]],
    ) -> bool:
        changed = False
        fields_protected_by_tags = get_fields_protected_by_tags(note)
        for field in fields:
            protected_fields_for_model = protected_fields.get(
                mw.col.models.get(note.mid)["id"], []
            )
            if field.name in protected_fields_for_model:
                LOGGER.debug(
                    f"Field {field.name} is protected by the protected_fields for the model, skipping."
                )
                continue

            if field.name in fields_protected_by_tags:
                LOGGER.debug(f"Field {field.name} is protected by a tag, skipping.")
                continue

            if note[field.name] != field.value:
                LOGGER.debug(
                    f'Field: "{field.name}" will be changed from:\n'
                    f"{note[field.name]}\n"
                    "to:\n"
                    f"{field.value}"
                )
                note[field.name] = field.value
                changed = True
        return changed

    def _prepare_tags(
        self,
        note: Note,
        tags: List[str],
        protected_tags: List[str],
    ) -> bool:
        changed = False
        prev_tags = note.tags
        note.tags = updated_tags(
            cur_tags=note.tags, incoming_tags=tags, protected_tags=protected_tags
        )
        if set(prev_tags) != set(note.tags):
            LOGGER.debug(
                f"Tags were changed from {prev_tags} to {note.tags}.",
            )
            changed = True

        return changed


def get_fields_protected_by_tags(note: Note) -> List[str]:
    field_names_from_tags = [
        tag[len(prefix) :]
        for tag in note.tags
        if tag.startswith((prefix := f"{TAG_FOR_PROTECTING_FIELDS}::"))
    ]

    # Both a field and the field with underscores replaced with spaces should be protected.
    # This makes it possible to protect fields with spaces in their name because tags cant contain spaces.
    standardized_field_names_from_tags = [
        field.replace("_", " ") for field in field_names_from_tags
    ]
    standardized_field_names_from_note = [
        field.replace("_", " ") for field in note.keys()
    ]

    result = [
        field
        for field in standardized_field_names_from_note
        if field in standardized_field_names_from_tags
    ]

    return result


def adjust_deck(deck_name: str, local_did: Optional[DeckId] = None) -> DeckId:
    unique_name = get_unique_deck_name(deck_name)
    if local_did is None:
        local_did = DeckId(mw.col.decks.add_normal_deck_with_name(unique_name).id)
        LOGGER.debug(f"Created deck {local_did=}")
    elif mw.col.decks.name_if_exists(local_did) is None:
        # recreate deck if it was deleted
        create_deck_with_id(unique_name, local_did)
        LOGGER.debug(f"Recreated deck {local_did=}")

    return local_did


def updated_tags(
    cur_tags: List[str], incoming_tags: List[str], protected_tags: List[str]
) -> List[str]:

    # get subset of cur_tags that are protected
    # by being equal to a protected tag or by containing a protected tag
    # protected_tags can't contain "::" (this is enforced when the user chooses them in the webapp)
    protected = set(
        tag
        for tag in cur_tags
        if any(protected_tag in tag.split("::") for protected_tag in protected_tags)
    )

    # keep addon internal tags
    internal = [tag for tag in cur_tags if is_internal_tag(tag)]

    result = list(set(protected) | set(internal) | set(incoming_tags))
    return result


def fetch_remote_note_types_based_on_notes_data(
    notes_data: List[NoteUpdate],
) -> Dict[NotetypeId, NotetypeDict]:
    remote_mids = set(NotetypeId(note_data.mid) for note_data in notes_data)
    result = fetch_remote_note_types(remote_mids)
    return result


def fetch_remote_note_types(
    mids: Iterable[NotetypeId],
) -> Dict[NotetypeId, NotetypeDict]:
    client = AnkiHubClient()
    result = {mid: client.get_note_type(mid) for mid in mids}
    return result


def adjust_note_types(remote_note_types: Dict[NotetypeId, NotetypeDict]) -> None:
    # can be called when installing a deck for the first time and when synchronizing with AnkiHub

    LOGGER.debug("Beginning adjusting note types...")

    create_missing_note_types(remote_note_types)
    rename_note_types(remote_note_types)
    ensure_local_and_remote_fields_are_same(remote_note_types)
    modify_note_type_templates(remote_note_types.keys())

    LOGGER.debug("Adjusted note types.")


def create_missing_note_types(
    remote_note_types: Dict[NotetypeId, NotetypeDict]
) -> None:
    missings_mids = set(
        mid for mid in remote_note_types.keys() if mw.col.models.get(mid) is None
    )
    for mid in missings_mids:
        LOGGER.debug(f"Missing note type {mid}")
        new_note_type = remote_note_types[mid]
        create_note_type_with_id(new_note_type, mid)
        LOGGER.debug(f"Created missing note type {mid}")


def rename_note_types(remote_note_types: Dict[NotetypeId, NotetypeDict]) -> None:
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = mw.col.models.get(mid)
        if local_note_type["name"] != remote_note_type["name"]:
            local_note_type["name"] = remote_note_type["name"]
            mw.col.models.ensure_name_unique(local_note_type)
            mw.col.models.update_dict(local_note_type)
            LOGGER.debug(f"Renamed note type {mid=} to {local_note_type['name']}")


def ensure_local_and_remote_fields_are_same(
    remote_note_types: Dict[NotetypeId, NotetypeDict]
) -> None:
    def field_tuples(flds: List[Dict]) -> List[Tuple[int, str]]:
        return [(field["ord"], field["name"]) for field in flds]

    note_types_with_field_conflicts: List[Tuple[NotetypeDict, NotetypeDict]] = []
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = mw.col.models.get(mid)

        if not field_tuples(local_note_type["flds"]) == field_tuples(
            remote_note_type["flds"]
        ):
            LOGGER.debug(
                f'Fields of local note type "{local_note_type["name"]}" differ from remote note type.\n'
                f"local:\n{pformat(field_tuples(local_note_type['flds']))}\n"
                f"remote:\n{pformat(field_tuples(remote_note_type['flds']))}"
            )
            note_types_with_field_conflicts.append((local_note_type, remote_note_type))

    for local_note_type, remote_note_type in note_types_with_field_conflicts:
        LOGGER.debug(f"Adjusting fields of {local_note_type['name']}...")

        local_note_type["flds"] = adjust_field_ords(
            local_note_type["flds"], remote_note_type["flds"]
        )
        LOGGER.debug(
            f"Fields after adjusting ords:\n{pformat(field_tuples(local_note_type['flds']))}"
        )

        mw.col.models.update_dict(local_note_type)
        LOGGER.debug(
            f"Fields after updating the model:\n"
            f"{pformat(field_tuples(mw.col.models.get(local_note_type['id'])['flds']))}"
        )


def adjust_field_ords(
    cur_model_flds: List[Dict], new_model_flds: List[Dict]
) -> List[Dict]:
    # This makes sure that when fields get added or are moved field contents end up
    # in the field with the same name as before.
    # This is relevant because people can protect fields.
    # Note that the result will have exactly the same set of field names as the new_model,
    # just the ords will be adjusted.
    for fld in new_model_flds:
        if (
            cur_ord := next(
                (_fld["ord"] for _fld in cur_model_flds if _fld["name"] == fld["name"]),
                None,
            )
        ) is not None:
            fld["ord"] = cur_ord
        else:
            # it's okay to assign this to multiple fields because the
            # backend assigns new ords equal to the fields index
            fld["ord"] = len(new_model_flds) - 1
    return new_model_flds


def reset_note_types_of_notes_based_on_notes_data(notes_data: List[NoteUpdate]) -> None:
    """Set the note type of notes back to the note type they have in the remote deck if they have a different one"""
    nid_mid_pairs = [
        (NoteId(note_data.anki_nid), NotetypeId(note_data.mid))
        for note_data in notes_data
    ]
    reset_note_types_of_notes(nid_mid_pairs)


def sync_with_progress() -> None:

    sync = AnkiHubSync()

    def sync_with_ankihub_after_delay():

        # sync_with_ankihub creates a backup before syncing and creating a backup requires to close
        # the collection in Anki versions lower than 2.1.50.
        # When other add-ons try to access the collection while it is closed they will get an error.
        # Many add-ons are added to the profile_did_open hook so we can wait until they will probably finish
        # and sync then.
        # Another way to deal with that is to tell users to set the sync_on_startup option to false and
        # to sync manually.
        if ANKI_MINOR < 50:
            sleep(3)

        sync.sync_all_decks()

    def on_done(future: Future):
        if exc := future.exception():
            LOGGER.debug("Unable to sync.")
            raise exc
        else:
            total = sync.importer.num_notes_created + sync.importer.num_notes_updated
            if total == 0:
                tooltip("AnkiHub: No new updates")
            else:
                tooltip(
                    f"AnkiHub: Synced {total} note{'' if total == 1 else 's'}.",
                    parent=mw,
                )
            mw.reset()

    if config.private_config.token:
        mw.taskman.with_progress(
            lambda: sync_with_ankihub_after_delay(),
            label="Synchronizing with AnkiHub",
            on_done=on_done,
            parent=mw,
            immediate=True,
        )
    else:
        LOGGER.debug("Skipping sync due to no token.")


def setup_sync_on_startup() -> None:
    def on_profile_open():
        # syncing with AnkiHub during sync with AnkiWeb causes an error,
        # this is why we have to wait until the AnkiWeb sync is done if there is one
        if not mw.can_auto_sync():
            sync_with_progress()
        else:

            def on_sync_did_finish():
                sync_with_progress()
                gui_hooks.sync_did_finish.remove(on_sync_did_finish)

            gui_hooks.sync_did_finish.append(on_sync_did_finish)

    gui_hooks.profile_did_open.append(on_profile_open)
