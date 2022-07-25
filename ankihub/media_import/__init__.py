
import sys
from pathlib import Path
core_dir = Path(__file__).resolve().parent / "core"
sys.path.append(str(core_dir))
libs_dir = Path(__file__).resolve().parent / "libs"
sys.path.append(str(libs_dir))

try:
    import Crypto.Cipher
except:
    from . import install_libs


import_dialog = None

def open_import_dialog() -> None:
    from .dialog import ImportDialog

    global import_dialog
    if import_dialog is None:
        import_dialog = ImportDialog()
    if not import_dialog.isVisible():
        import_dialog.show()
    import_dialog.activateWindow()