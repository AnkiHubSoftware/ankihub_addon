from typing import TYPE_CHECKING

from .base import ImportTab
if TYPE_CHECKING:
    from .base import ImportDialog
    from ..pathlike.mega import MegaRoot


class MegaTab(ImportTab):

    def __init__(self, dialog: "ImportDialog"):
        self.define_texts()
        ImportTab.__init__(self, dialog)

    def define_texts(self) -> None:
        self.button_text = "Check URL"
        self.import_not_valid_tooltip = "Check if your URL is correct, then press 'check URL'."
        self.empty_input_msg = "Input a url to a Mega shared folder"
        self.while_create_rootpath_msg = "Checking if URL is valid..."
        self.malformed_url_msg = "Not a Mega URL."
        self.root_not_found_msg = "Folder doesn't exist. Maybe check if it's shared?"
        self.is_a_file_msg = "This URL leads to a file. Please write a URL to a folder"

    def on_btn(self) -> None:
        self.update_root_file()

    def create_root_file(self, url: str) -> "MegaRoot":
        from ..pathlike.mega import MegaRoot
        return MegaRoot(url)
