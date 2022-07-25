from typing import Any, Dict, List, Tuple, Union, Optional
import random
import requests
import json
import re

from Crypto.Cipher import AES
from Crypto.Util import Counter
from mega.errors import RequestError as MegaReqError
from mega.crypto import a32_to_str, base64_to_a32, base64_url_decode, decrypt_attr, decrypt_key

from .base import RootPath, FileLike
from .errors import *

"""
Mega node (file/folder) datatype:
h: str - id of the node
p: str - id of its parent node
u: str - user id
t: int[0-3] - node type. 0: file, 1: folder
a: str - encrypted dictionary of attributes
    {n: str - name, c: str - hash? maybe based on creation time }
k: str - file key. Use shared folder's key to create the real file's key
s: int - file size
ts: int - timestamp
"""


class Mega:
    def __init__(self) -> None:
        self.sequence_num = random.randint(0, 0xFFFFFFFF)
        self.REGEXP = {
            "file": [
                r"mega.(?:io|nz|co\.nz)/file/[0-z-_]+#[0-z-_]+",
                r"mega.(?:io|nz|co\.nz)/#![0-z-_]+[!#][0-z-_]+",
                r"mega.(?:io|nz|co\.nz)/folder/[0-z-_]+#[0-z-_]+(?:/file/[0-z-_]+)+",
                r"mega.(?:io|nz|co\.nz)/#F![0-z-_]+[!#][0-z-_]+(?:/file/[0-z-_]+)+"
            ],
            "folder": [
                r"mega.(?:io|nz|co\.nz)/folder/([0-z-_]+)#([0-z-_]+)(?:/folder/([0-z-_]+))*",
                r"mega.(?:io|nz|co\.nz)/#F!([0-z-_]+)[!#]([0-z-_]+)(?:/folder/([0-z-_]+))*"
            ]
        }
        self.URL_PATTERNS: Dict[str, list] = {"file": [], "folder": []}
        for type in self.REGEXP:
            for regexp in self.REGEXP[type]:
                self.URL_PATTERNS[type].append(re.compile(regexp))

    def api_request(self, data: Union[dict, list], root_folder: Optional[str]) -> dict:
        params: Dict[str, Any] = {
            "id": self.sequence_num
        }
        if root_folder:
            params["n"] = root_folder
        self.sequence_num += 1

        # ensure input data is a list
        if not isinstance(data, list):
            data = [data]

        url = r"https://g.api.mega.co.nz/cs"
        response = requests.post(
            url,
            params=params,
            data=json.dumps(data)
        )
        json_resp = json.loads(response.text)

        try:
            if isinstance(json_resp, list):
                int_resp = json_resp[0] if isinstance(json_resp[0],
                                                      int) else None
            elif isinstance(json_resp, int):
                int_resp = json_resp

        except IndexError:
            int_resp = None
        if int_resp is not None:
            raise MegaReqError(int_resp)
        return json_resp[0]

    def download_file(self, root_folder: str, file_id: str, file_key: Tuple[int, ...]) -> bytes:
        file_data = self.api_request({
            'a': 'g',
            'g': 1,
            'n': file_id
        }, root_folder)

        k = self.xor_key(file_key)
        iv = file_key[4:6] + (0, 0)

        # Seems to happens sometime... When this occurs, files are
        # inaccessible also in the official also in the official web app.
        # Strangely, files can come back later.
        if 'g' not in file_data:
            raise MegaReqError('File not accessible anymore')
        file_url = file_data['g']
        encrypted_file = requests.get(file_url).content

        k_str = a32_to_str(k)
        counter = Counter.new(128, initial_value=((iv[0] << 32) + iv[1]) << 64)
        aes = AES.new(k_str, AES.MODE_CTR, counter=counter)
        file = aes.decrypt(encrypted_file)

        return file

    def list_files(self, id: str) -> List[dict]:
        data = [{"a": "f", "c": 1, "ca": 1, "r": 1}]
        try:
            nodes = self.api_request(data, id)["f"]
        except MegaReqError as e:
            if e.code in (-8, -9, -13):
                raise RootNotFoundError(e.code)
            elif e.code in (-1, -3):
                raise ServerError(e.code)
            elif e.code in (-4, -17):
                raise RateLimitError(e.code)
            else:
                raise RequestError(e.code, e.message)
        return nodes

    def parse_url(self, url: str) -> Tuple[str, str, Optional[str]]:
        "Returns (public_handle, key, id) if valid. If not returns None. If not subfolder, id=None. "
        def get_m(patterns: list) -> Optional[re.Match]:
            for pattern in patterns:
                m = re.search(pattern, url)
                if m:
                    return m
            return None
        m = get_m(self.URL_PATTERNS["file"])
        if m:  # The order between checking file and folder should not be reversed
            raise IsAFileError()
        m = get_m(self.URL_PATTERNS["folder"])
        if not m:
            raise MalformedURLError()
        matches = m.groups()
        public_handle = matches[0]
        key = matches[1]
        if len(matches) > 2:
            id = matches[-1]
        else:
            id = None

        if (id and len(id) != 8) or len(public_handle) != 8 or len(key) != 22:
            raise MalformedURLError()

        return (public_handle, key, id)

    def decrypt_node_key(self, key_data: str, shared_key: str) -> Tuple[int, ...]:
        encrypted_key = base64_to_a32(key_data.split(":")[1])
        return decrypt_key(encrypted_key, shared_key)

    def decrypt_attribute(self, attrs_data: str, key: Tuple[int, ...], is_file: bool = True) -> Dict[str, Any]:
        if is_file:
            key = self.xor_key(key)
        return decrypt_attr(base64_url_decode(attrs_data), key)

    def xor_key(self, key: Tuple[int, ...]) -> Tuple[int, ...]:
        return (key[0] ^ key[4], key[1] ^ key[5], key[2] ^ key[6], key[3] ^ key[7])


mega = Mega()


class MegaRoot(RootPath):
    raw: str
    name: str
    files: List["FileLike"]

    public_handle: str
    shared_key: str
    id: Optional[str]

    def __init__(self, url: str) -> None:
        self.raw = url
        (public_handle, key, id) = mega.parse_url(url)
        self.public_handle = public_handle
        self.shared_key = base64_to_a32(key)
        self.id = id
        self.get_data()

    def get_data(self) -> None:
        """Sets self.name and self.files"""
        nodes = mega.list_files(self.public_handle)
        if self.id:
            root_id = self.id
        else:
            root_id = nodes[0]["h"]
        for node in nodes:
            if node["h"] == root_id:
                key = mega.decrypt_node_key(node["k"], self.shared_key)
                attrs = mega.decrypt_attribute(node["a"], key, is_file=False)
                self.name = attrs["n"]
                break
        if not self.name:  # This shouldn't happen.
            raise RequestError(msg="Couldn't find the subfolder.")
        self.files = []
        self.search_files(nodes, root_id, recursive=True)

    def search_files(self, nodes: List[Dict[str, Any]], id: str, recursive: bool) -> None:
        for node in nodes:
            if node["p"] != id:  # Node is not in this folder 'id'
                continue
            if node["t"] == 1:  # Is folder
                self.search_files(nodes, node["h"], recursive)
            if node["t"] != 0:  # Not a file. Special node.
                continue
            key = mega.decrypt_node_key(node["k"], self.shared_key)
            attrs = mega.decrypt_attribute(node["a"], key)
            name = attrs["n"]
            if not '.' in name:
                continue
            ext = name.split(".")[-1]
            if not self.has_media_ext(ext):
                continue
            file = MegaFile(root=self, id=node["h"], key=key,
                            name=attrs["n"], ext=ext, size=node["s"])
            self.files.append(file)


class MegaFile(FileLike):
    id: str  # A string that can identify the file
    name: str
    extension: str
    size: int

    key: Tuple[int, ...]
    root: MegaRoot

    def __init__(self, root: MegaRoot, id: str, key: Tuple[int, ...], name: str, ext: str, size: int) -> None:
        self.root = root
        self.id = id
        self.key = key
        self.name = name
        self.extension = ext
        self.size = size

    def read_bytes(self) -> bytes:
        return mega.download_file(self.root.public_handle, self.id, self.key)

    def is_identical(self, file: FileLike) -> bool:
        return file.size == self.size
