import os
import re
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

DJANGO_TARGET = ANKIHUB_LIB_TARGET / "django"
WEB_APP_SRC = PROJECT_ROOT / "ankihub_web"
WEB_COMPONENTS_SRC = WEB_APP_SRC / "ankihub" / "templates" / "cotton" / "v1"
WEB_COMPONENTS_TARGET = PROJECT_ROOT / "ankihub" / "django" / "app" / "templates" / "cotton" / "v1"
WEB_CSS_SRC = WEB_APP_SRC / "theme" / "static_src" / "src" / "styles.css"
WEB_CSS_TARGET = PROJECT_ROOT / "tutorial" / "lib" / "vendor" / "tailwind.css"

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
# Remove large unused files from the Django package
for path in DJANGO_TARGET.rglob("locale/*"):
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
shutil.rmtree(DJANGO_TARGET / "contrib" / "admin" / "static", ignore_errors=True)
shutil.rmtree(DJANGO_TARGET / "contrib" / "gis", ignore_errors=True)


shutil.rmtree(WEB_COMPONENTS_TARGET, ignore_errors=True)
shutil.copytree(WEB_COMPONENTS_SRC, WEB_COMPONENTS_TARGET)
WEB_CSS_TARGET.parent.mkdir(exist_ok=True)
web_css = WEB_CSS_SRC.read_text(encoding="utf-8")
# Point Tailwind to the templates for class generation
tailwind_sources = """@source "../**/*.{ts,js,html}";
@source "../../../ankihub/django/app/templates/";
@source "../../../ankihub/django/app/templates/cotton/v1";
@source "../../../ankihub/django/app/templates/cotton/v1/**";
@source "../../../ankihub/gui/tutorial.py";
"""
web_css = re.sub("@source .*", tailwind_sources, web_css)
WEB_CSS_TARGET.write_text(web_css, encoding="utf-8")
subprocess.run([shutil.which("npm"), "install"], cwd=PROJECT_ROOT / "tutorial", check=True)
subprocess.run([shutil.which("npm"), "run", "build"], cwd=PROJECT_ROOT / "tutorial", check=True)

generate_manifest()

shutil.rmtree(MEDIA_IMPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_IMPORT_SRC, MEDIA_IMPORT_TARGET)

shutil.rmtree(MEDIA_EXPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_EXPORT_SRC, MEDIA_EXPORT_TARGET)

obfuscate_google_api_key(GOOGLE_API_KEY, MEDIA_IMPORT_TARGET)
