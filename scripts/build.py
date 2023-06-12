import os
import shutil
import subprocess
from pathlib import Path

import _aqt
import anki
from generate_manifest import generate_manifest
from google_api_obfuscate import obfuscate_google_api_key

PROJECT_ROOT = Path(__file__).parent.parent
MEDIA_IMPORT_SRC = PROJECT_ROOT / "media_import/src/media_import"
MEDIA_IMPORT_LIBS = MEDIA_IMPORT_SRC / "libs"
MEDIA_IMPORT_TARGET = PROJECT_ROOT / "ankihub/media_import"
MEDIA_IMPORT_REQUIREMENTS = PROJECT_ROOT / "media_import" / "requirements.txt"

MEDIA_EXPORT_SRC = PROJECT_ROOT / "media_export/src"
MEDIA_EXPORT_TARGET = PROJECT_ROOT / "ankihub/media_export"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

subprocess.run("git submodule update --init --recursive", shell=True, cwd=PROJECT_ROOT)

subprocess.run(
    [
        "python3",
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--target",
        str(MEDIA_IMPORT_LIBS),
        "-r",
        str(MEDIA_IMPORT_REQUIREMENTS),
        "--no-user",  # needed for gitpod because it adds --user automatically and it conflicts with --target
    ],
    check=True,
)

generate_manifest()

shutil.rmtree(MEDIA_IMPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_IMPORT_SRC, MEDIA_IMPORT_TARGET)

shutil.rmtree(MEDIA_EXPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_EXPORT_SRC, MEDIA_EXPORT_TARGET)

obfuscate_google_api_key(GOOGLE_API_KEY, MEDIA_IMPORT_TARGET)


def create_init_file(module):
    module_path = Path(module.__path__[0])
    init_file_path = module_path / "__init__.py"
    init_file_path.touch(exist_ok=True)


# This makes mypy type checking work on Anki versions >= 2.1.55
create_init_file(anki)
create_init_file(_aqt)
