from hashlib import md5
from typing import List, Union, Optional
from pathlib import Path

from .base import RootPath, FileLike
from .errors import RootNotFoundError, IsAFileError, MalformedURLError


class LocalRoot(RootPath):
    raw: str
    name: str
    files: List["FileLike"]

    path: Path

    def __init__(self, path: Union[str, Path], recursive: bool = True) -> None:
        self.raw = str(path)
        try:
            if isinstance(path, str):
                self.path = Path(path)
            else:
                self.path = path
            if not self.path.is_dir():
                if self.path.is_file():
                    raise IsAFileError()
                else:
                    raise RootNotFoundError()
        except OSError:
            raise MalformedURLError()
        self.name = self.path.name
        self.files = self.list_files(recursive=recursive)

    def list_files(self, recursive: bool) -> List["FileLike"]:
        files: List["FileLike"] = []
        self.search_files(files, self.path, recursive)
        return files

    def search_files(self, files: List["FileLike"], src: Path, recursive: bool) -> None:
        for path in src.iterdir():
            if path.is_file():
                if len(path.suffix) > 1 and self.has_media_ext(path.suffix[1:]):
                    files.append(LocalFile(path))
            elif recursive and path.is_dir():
                self.search_files(files, path, recursive=True)


class LocalFile(FileLike):
    key: str  # == str(path)
    name: str
    extension: str

    _size: Optional[int]
    _md5: Optional[str]
    path: Path

    def __init__(self, path: Path):
        self.key = str(path)
        self.name = path.name
        self.extension = path.suffix[1:]
        self.path = path
        self._size = None
        self._md5 = None

    @property
    def size(self) -> int:  # type: ignore
        if not self._size:
            self._size = self.path.stat().st_size
        return self._size

    @property
    def md5(self) -> str:
        if not self._md5:  # Cache result
            self._md5 = md5(self.read_bytes()).hexdigest()
        return self._md5

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()

    def is_identical(self, file: FileLike) -> bool:
        try:
            return file.size == self.size and file.md5 == self.md5  # type: ignore
        except AttributeError:
            return file.size == self.size
