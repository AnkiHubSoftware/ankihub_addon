from concurrent.futures import Future
from typing import TYPE_CHECKING, Optional
from requests.exceptions import ConnectionError, Timeout, RequestException  # type: ignore
import math

from aqt import mw
from aqt.qt import *
from aqt.utils import tooltip

from ..pathlike import RootPath
from ..pathlike.errors import *
from ..importing import import_media

if TYPE_CHECKING:
    from ..dialog import ImportDialog


def qlabel(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextInteractionFlags(Qt.TextBrowserInteraction)
    return label


def small_qlabel(text: str) -> QLabel:
    label = qlabel(text)
    font = label.font()
    if font.pointSize() != -1:
        font_size = math.floor(font.pointSize() * 0.92)
        font.setPointSize(font_size)
    else:
        font_size = math.floor(font.pixelSize() * 0.92)
        font.setPixelSize(font_size)
    label.setFont(font)
    return label


class ImportTab(QWidget):
    dialog: "ImportDialog"
    valid_path: bool
    rootpath: Optional[RootPath]

    # Messages that differ by tab. Define them in subclasses.
    button_text: str
    import_not_valid_tooltip: str
    empty_input_msg: str
    while_create_rootpath_msg: str
    malformed_url_msg: str
    root_not_found_msg: str
    is_a_file_msg: str

    def __init__(self, dialog: "ImportDialog"):
        QWidget.__init__(self, dialog)
        self.dialog = dialog
        self.valid_path = False
        self.rootpath: Optional[RootPath] = None
        self.setup()

    def define_texts(self) -> None:
        pass

    def setup(self) -> None:
        self.main_layout = QVBoxLayout(self)
        main_layout = self.main_layout
        self.setLayout(self.main_layout)

        main_grid = QGridLayout(self)
        main_layout.addLayout(main_grid)

        import_label = qlabel("Import:")
        main_grid.addWidget(import_label, 0, 0)

        path_input = QLineEdit()
        self.path_input = path_input
        path_input.setMinimumWidth(200)
        path_input.textEdited.connect(self.on_input_change)
        main_grid.addWidget(path_input, 0, 2)

        btn = QPushButton(self.button_text)
        self.btn = btn
        btn.clicked.connect(self.on_btn)
        main_grid.addWidget(btn, 0, 3)

        sub_text = small_qlabel(self.empty_input_msg)
        self.sub_text = sub_text
        sub_text.setWordWrap(True)
        main_grid.addWidget(sub_text, 1, 2)

        main_layout.addStretch(1)

    def on_import(self) -> None:
        if not self.valid_path:
            tooltip(self.import_not_valid_tooltip)
            return
        if self.rootpath.raw != self.path_input.text():
            self.update_root_file()
            return
        mw.progress.start(
            parent=mw, label="Starting import", immediate=True)
        import_media(self.rootpath, self.dialog.finish_import)

    def on_input_change(self) -> None:
        return

    def on_btn(self) -> None:
        return

    def clear_path(self) -> None:
        self.path_input.setText("")
        self.sub_text.setText(self.empty_input_msg)
        self.rootpath = None
        self.valid_path = False

    def create_root_file(self, url: str) -> RootPath:
        pass

    def update_root_file(self) -> None:
        self.valid_path = False
        url = self.path_input.text()
        if url == "":
            self.sub_text.setText(self.empty_input_msg)
            return

        self.sub_text.setText(self.while_create_rootpath_msg)

        def on_done(fut: Future) -> None:
            curr_url = self.path_input.text()
            if not url == curr_url:
                return
            self.rootpath = None
            try:
                self.rootpath = fut.result()
                self.valid_path = True
                file_cnt = len(self.rootpath.files)
                self.sub_text.setText(
                    f"{file_cnt} files found in '{self.rootpath.name}'")
            except MalformedURLError:
                self.sub_text.setText(self.malformed_url_msg)
            except RootNotFoundError:
                self.sub_text.setText(self.root_not_found_msg)
            except IsAFileError:
                self.sub_text.setText(self.is_a_file_msg)
            except RateLimitError as err:
                self.sub_text.setText(
                    "Rate limit exceeded. Please try again tomorrow. ({err.code})")
            except ServerError as err:
                self.sub_text.setText(
                    "Maybe the server is down? Please try again later. ({err.code})")
            except RequestError as err:
                self.sub_text.setText(str(err))
            # Network Errors from requests module
            except Timeout as err:
                err_type = err.__class__.__name__
                self.sub_text.setText(
                    f"Network error<{err_type}: Can't connect to server.")
            except ConnectionError as err:
                err_type = err.__class__.__name__
                self.sub_text.setText(
                    f"Network error<{err_type}>: Please check if your network is connected.")
            except RequestException as err:  # Catch all other errors from requests
                err_type = err.__class__.__name__
                self.sub_text.setText(
                    f"Network error<{err_type}>: Something went wrong.")
            # Local File Errors
            except PermissionError as err:
                self.sub_text.setText(str(err))

        mw.taskman.run_in_background(
            self.create_root_file, on_done, {"url": url})
