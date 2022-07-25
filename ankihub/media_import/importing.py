from concurrent.futures import Future
from typing import Callable, Dict, List, NamedTuple, Sequence, NamedTuple, Optional
from requests.exceptions import RequestException
from datetime import datetime, timedelta
import unicodedata
import traceback

from anki.media import media_paths_from_col_path
from aqt import mw
from aqt.utils import askUserDialog

from .pathlike import FileLike, RootPath, LocalRoot
from .pathlike.errors import AddonError


class ImportResult(NamedTuple):
    logs: List[str]
    success: bool


class ImportInfo():
    """Handles files_list count and their size"""
    files: list
    tot: int
    prev: int

    tot_size: int
    size: int
    prev_time: datetime
    prev_file_size: int

    def __init__(self, files: list) -> None:
        self.files = files
        self.tot = len(files)
        self.prev = self.tot
        self.diff = 0
        self.calculate_size()
        self.tot_size = self.size

    def update_count(self) -> int:
        """ Returns `curr - prev`, then updates prev to curr """
        self.diff = self.prev - self.curr
        self.prev = self.curr
        return self.diff

    def calculate_size(self) -> None:
        self.size = 0
        for file in self.files:
            self.size += file.size
        self.prev_time = datetime.now()
        self.prev_file_size = 0

    def update_size(self, file: FileLike) -> None:
        self.size -= file.size
        self.prev_file_size = file.size
        self.prev_time = datetime.now()

    @property
    def remaining_time_str(self) -> str:
        if not self.prev_file_size and self.prev_time:
            return ""
        timedelta = (datetime.now() - self.prev_time)
        estimate = timedelta * self.size / self.prev_file_size
        return self._format_timedelta(estimate)

    @property
    def size_str(self) -> str:
        return self._size_str(self.tot_size - self.size)

    @property
    def tot_size_str(self) -> str:
        return self._size_str(self.tot_size)

    @property
    def curr(self) -> int:
        return len(self.files)

    @property
    def left(self) -> int:
        return self.tot - self.curr

    def _format_timedelta(self, timedelta: timedelta) -> str:
        tot_secs = timedelta.seconds
        units = [60, 60*60, 60*60*24]
        seconds = tot_secs % units[0]
        minutes = (tot_secs % units[1]) // units[0]
        hours = (tot_secs % units[2]) // units[1]
        days = tot_secs // units[2]

        if days:
            time_str = f"{days}d {hours}h"
        elif hours:
            time_str = f"{hours}h {minutes}m"
        elif minutes:
            time_str = f"{minutes}m {seconds}s"
        else:
            time_str = f"{seconds}s"
        return time_str

    def _size_str(self, size: float) -> str:
        """Prints size of imported files."""
        for unit in ['Bytes', 'KB', 'MB', 'GB']:
            if size < 1000:
                return "%3.1f%s" % (size, unit)
            size = size / 1000
        return "%.1f%s" % (size, 'TB')


def import_media(src: RootPath, on_done: Callable[[ImportResult], None]) -> None:
    """
    Import media from a directory, and its subdirectories. 
    """
    logs: List[str] = []

    try:
        _import_media(logs, src, on_done)
    except Exception as err:
        tb = traceback.format_exc()
        print(tb)
        print(str(err))
        logs.append(tb)
        logs.append(str(err))
        res = ImportResult(logs, success=False)
        on_done(res)


def _import_media(logs: List[str], src: RootPath, on_done: Callable[[ImportResult], None]) -> None:

    def log(msg: str) -> None:
        print(f"Media Import: {msg}")
        logs.append(msg)

    def finish_import(msg: str, success: bool) -> None:
        log(msg)
        result = ImportResult(logs, success)
        on_done(result)

    # 1. Get the name of all media files.
    files_list = src.files
    info = ImportInfo(files_list)
    log(f"{info.tot} media files found.")

    # 2. Normalize file names
    unnormalized = find_unnormalized_name(files_list)
    if len(unnormalized):
        finish_import(f"{len(unnormalized)} files have invalid file names: {unnormalized}",
                      success=False)
        return

    # 3. Make sure there isn't a name conflict within new files.
    if name_conflict_exists(files_list):
        finish_import("There are multiple files with same filename.",
                      success=False)
        return

    if info.update_count():
        log(f"{info.diff} files were skipped because they are identical.")

    # 4. Check collection.media if there is a file with same name.
    name_conflicts = name_exists_in_collection(files_list)
    if len(name_conflicts):
        msg = f"{len(name_conflicts)} files have the same name as existing media files:"
        log(msg)
        file_names_str = ""
        for file in name_conflicts:
            file_names_str += file.name + "\n"
        log(file_names_str + "-"*16)
        ask_msg = msg + "\nDo you want to import the rest of the files?"
        diag = askUserDialog(
            ask_msg, buttons=["Abort Import", "Continue Import"])
        mw.progress.finish()
        if diag.run() == "Abort Import":
            finish_import("Aborted import due to name conflict with existing media",
                          success=False)
            return
        mw.progress.start(parent=mw, label="Importing media", immediate=True)
    if info.update_count():
        diff = info.diff - len(name_conflicts)
        log(f"{diff} files were skipped because they already exist in collection.")

    if info.curr == 0:
        finish_import(
            f"{info.tot} media files were imported", success=True)
        return

    # 5. Add media files in chunk in background.
    log(f"{info.curr} media files will be processed.")
    info.calculate_size()
    MAX_ERRORS = 5
    error_cnt = 0  # Count of errors in succession

    def add_next_file(fut: Optional[Future], file: Optional[FileLike]) -> None:
        nonlocal error_cnt
        if fut is not None:
            try:
                fut.result()  # Check if add_media raised an error
                error_cnt = 0
            except (AddonError, RequestException) as err:
                error_cnt += 1
                log("-"*16 + "\n" + str(err) + "\n" + "-"*16)
                if error_cnt < MAX_ERRORS:
                    if info.left < 10:
                        log(f"{info.left} files were not imported.")
                        for file in files_list:
                            log(file.name)
                    finish_import(f"{info.left} / {info.tot} media files were imported.",
                                  success=False)
                    return
                else:
                    files_list.append(file)

        # Last file was added
        if len(files_list) == 0:
            finish_import(f"{info.tot} media files were imported.",
                          success=True)
            return
        # Abort import
        if mw.progress.want_cancel():
            finish_import(f"Import aborted.\n{info.left} / {info.tot} media files were imported.",
                          success=False)
            return

        progress_msg = (f"Adding media files ({info.left} / {info.tot})\n"
                        f"{info.size_str}/{info.tot_size_str} "
                        f"({info.remaining_time_str} left)")
        mw.progress.update(label=progress_msg,
                           value=info.left,
                           max=info.tot)

        file = files_list.pop(0)
        info.update_size(file)
        mw.taskman.run_in_background(
            add_media, lambda fut: add_next_file(fut, file),
            args={"file": file})

    add_next_file(None, None)


def find_unnormalized_name(files: Sequence[FileLike]) -> List[FileLike]:
    """Returns list of files whose names are not normalized."""
    unnormalized = []
    for file in files:
        name = file.name
        normalized_name = unicodedata.normalize("NFC", name)
        if name != normalized_name:
            unnormalized.append(file)
    return unnormalized


def name_conflict_exists(files_list: List[FileLike]) -> bool:
    """Returns True if there are different files with the same name.
       And removes identical files from files_list so only one remains. """
    file_names: Dict[str, FileLike] = {}  # {file_name: file_path}
    identical: List[int] = []

    for idx, file in enumerate(files_list):
        name = file.name
        if name in file_names:
            if file.is_identical(file_names[name]):
                identical.append(idx)
            else:
                return True
        else:
            file_names[name] = file
    for idx in sorted(identical, reverse=True):
        files_list.pop(idx)
    return False


def name_exists_in_collection(files_list: List[FileLike]) -> List[FileLike]:
    """Returns list of files whose names conflict with existing media files.
       And remove files if identical file exists in collection. """
    media_dir = LocalRoot(media_paths_from_col_path(
        mw.col.path)[0], recursive=False)
    collection_file_paths = media_dir.files
    collection_files = {file.name: file for file in collection_file_paths}

    name_conflicts: List[FileLike] = []
    to_pop: List[int] = []

    for idx, file in enumerate(files_list):
        if file.name in collection_files:
            to_pop.append(idx)
            if not file.is_identical(collection_files[file.name]):
                name_conflicts.append(file)

    for idx in sorted(to_pop, reverse=True):
        files_list.pop(idx)
    return name_conflicts


def add_media(file: FileLike) -> None:
    """
        Tries to add media with the same basename.
        But may change the name if it overlaps with existing media.
        Therefore make sure there isn't an existing media with the same name!
    """
    new_name = mw.col.media.write_data(file.name, file.read_bytes())
    assert new_name == file.name  # TODO: write an error dialogue?
