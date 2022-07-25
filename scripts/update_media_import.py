import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
MEDIA_IMPORT_SRC = PROJECT_ROOT / "media_import/src"
MEDIA_IMPORT_TARGET = PROJECT_ROOT / "ankihub/media_import"

shutil.rmtree(MEDIA_IMPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_IMPORT_SRC, MEDIA_IMPORT_TARGET)
