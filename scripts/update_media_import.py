import os
import shutil
import subprocess
from pathlib import Path

google_api_key = os.getenv("GOOGLE_API_KEY")

PROJECT_ROOT = Path(__file__).parent.parent
MEDIA_IMPORT_SRC = PROJECT_ROOT / "media_import/src/addon"
MEDIA_IMPORT_TARGET = PROJECT_ROOT / "ankihub/media_import"

API_KEY_OBFUSCATE_SCRIPT = Path(__file__).parent / "google_api_obfuscate.py"

shutil.rmtree(MEDIA_IMPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_IMPORT_SRC, MEDIA_IMPORT_TARGET)
subprocess.run(
    [
        "python3",
        str(API_KEY_OBFUSCATE_SCRIPT),
        google_api_key,
        str(MEDIA_IMPORT_TARGET),
    ]
)
