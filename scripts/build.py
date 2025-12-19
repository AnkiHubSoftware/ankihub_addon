import os
import shutil
import subprocess
from pathlib import Path

from generate_manifest import generate_manifest
from google_api_obfuscate import obfuscate_google_api_key

PROJECT_ROOT = Path(__file__).parent.parent
ANKIHUB_LIB_TARGET = PROJECT_ROOT / "ankihub/lib"
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
        "uv",
        "pip",
        "install",
        "--no-deps",
        "--target",
        str(MEDIA_IMPORT_LIBS),
        "-r",
        str(MEDIA_IMPORT_REQUIREMENTS),
    ],
    check=True,
)
subprocess.run(
    [
        "uv",
        "pip",
        "install",
        "--target",
        str(ANKIHUB_LIB_TARGET),
        "--group",
        "bundle",
    ],
    check=True,
)
shutil.rmtree(ANKIHUB_LIB_TARGET / "bin", ignore_errors=True)
subprocess.run([shutil.which("npm"), "install"], cwd=PROJECT_ROOT / "tutorial", check=True)
subprocess.run([shutil.which("npm"), "run", "build"], cwd=PROJECT_ROOT / "tutorial", check=True)
generate_manifest()

shutil.rmtree(MEDIA_IMPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_IMPORT_SRC, MEDIA_IMPORT_TARGET)

shutil.rmtree(MEDIA_EXPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_EXPORT_SRC, MEDIA_EXPORT_TARGET)

obfuscate_google_api_key(GOOGLE_API_KEY, MEDIA_IMPORT_TARGET)
