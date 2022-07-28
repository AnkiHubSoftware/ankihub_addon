import os
import shutil
import subprocess
from pathlib import Path

from generate_manifest import generate_manifest
from google_api_obfuscate import obfuscate_google_api_key

PROJECT_ROOT = Path(__file__).parent.parent
MEDIA_IMPORT_SRC = PROJECT_ROOT / "media_import/src/media_import"
MEDIA_IMPORT_LIBS = MEDIA_IMPORT_SRC / "libs"
MEDIA_IMPORT_TARGET = PROJECT_ROOT / "ankihub/media_import"
MEDIA_IMPORT_REQUIREMENTS = PROJECT_ROOT / "media_import" / "requirements.txt"

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
        "--no-user",
    ],
    check=True,
)

generate_manifest()

shutil.rmtree(MEDIA_IMPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_IMPORT_SRC, MEDIA_IMPORT_TARGET)

obfuscate_google_api_key(GOOGLE_API_KEY, MEDIA_IMPORT_TARGET)
