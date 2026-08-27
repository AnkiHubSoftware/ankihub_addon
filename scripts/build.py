import os
import re
import shutil
import subprocess
from pathlib import Path

from generate_manifest import generate_manifest
from google_api_obfuscate import obfuscate_google_api_key
from protobuf_gen import generate_protobuf

PROJECT_ROOT = Path(__file__).parent.parent
SRC_ROOT = PROJECT_ROOT / "ankihub"
ANKIHUB_LIB_TARGET = SRC_ROOT / "lib"
MEDIA_IMPORT_SRC = PROJECT_ROOT / "media_import/src/media_import"
MEDIA_IMPORT_LIBS = MEDIA_IMPORT_SRC / "libs"
MEDIA_IMPORT_TARGET = SRC_ROOT / "media_import"
MEDIA_IMPORT_REQUIREMENTS = PROJECT_ROOT / "media_import" / "requirements.txt"

MEDIA_EXPORT_SRC = PROJECT_ROOT / "media_export/src"
MEDIA_EXPORT_TARGET = SRC_ROOT / "media_export"

DJANGO_TARGET = ANKIHUB_LIB_TARGET / "django"
WEB_APP_SRC = PROJECT_ROOT / "ankihub_web"
WEB_COMPONENTS_SRC = WEB_APP_SRC / "ankihub" / "templates" / "cotton" / "v1"
WEB_COMPONENTS_TARGET = PROJECT_ROOT / "ankihub" / "django" / "app" / "templates" / "cotton" / "v1"
WEB_CSS_SRC = WEB_APP_SRC / "theme" / "static_src" / "src" / "styles.css"
WEB_CSS_TARGET = PROJECT_ROOT / "tutorial" / "lib" / "vendor" / "tailwind.css"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

OLDEST_ANKI_PYTHON = "3.9"
OLDEST_MODERN_PYTHON = "3.10"

# Each layer installs with the oldest Python that can reach it, so uv refuses a distribution that has
# dropped support for it. They are separate dependency groups so that routing never reads markers.
BUNDLE_LAYERS = (("bundle", OLDEST_ANKI_PYTHON), ("bundle_modern", OLDEST_MODERN_PYTHON))


def locked_group_requirements(group: str) -> str:
    """Resolving the group at build time instead would pick up whatever versions are current then."""
    return subprocess.run(
        [
            "uv",
            "export",
            # Without this, export re-resolves against PyPI and rewrites uv.lock. It also fails on a
            # lockfile out of date with pyproject.toml rather than silently preferring one.
            "--locked",
            # Without it the export carries ANSI escapes that uv cannot re-parse.
            "--color",
            "never",
            "--only-group",
            group,
            "--no-emit-project",
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(requirements: str) -> "set[str]":
    names = set()
    for line in requirements.splitlines():
        if not line.strip() or line.startswith("#") or line[0].isspace():
            continue  # an indented line continues the one above it, carrying --hash
        if line.startswith("--"):
            continue  # --index-url and friends install nothing of their own
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
        if not match:
            # Skipped instead, an editable (-e ../pkg) would install something every check below
            # is then blind to.
            raise SystemExit(f"cannot read {line!r} as a requirement; check what uv export emitted")
        names.add(canonical_name(match.group()))
    return names


subprocess.run("git submodule update --init --recursive", shell=True, cwd=PROJECT_ROOT)

subprocess.run(["uv", "python", "install", *dict(BUNDLE_LAYERS).values()], check=True)

# media_import's requirements are not in uv.lock - the submodule carries its own requirements.txt -
# but they ship in the same artifact, so they install with the same oldest interpreter.
subprocess.run(
    [
        "uv",
        "pip",
        "install",
        "--python",
        OLDEST_ANKI_PYTHON,
        "--no-deps",
        "--target",
        str(MEDIA_IMPORT_LIBS),
        "-r",
        str(MEDIA_IMPORT_REQUIREMENTS),
    ],
    check=True,
)
layer_requirements = {group: locked_group_requirements(group) for group, _ in BUNDLE_LAYERS}

# The layers install into one directory in sequence, so a distribution in both would be
# overwritten by the later, newer-Python copy.
shared = set.intersection(*(requirement_names(text) for text in layer_requirements.values()))
if shared:
    raise SystemExit(
        f"{sorted(shared)} are in more than one bundle layer; the later install would overwrite "
        "the copy resolved for the older Python"
    )

# uv installs over whatever the target already holds, so distributions removed since an
# earlier build would otherwise survive into the artifact. Not ignore_errors: a partial delete
# would leave exactly that behind.
if ANKIHUB_LIB_TARGET.is_symlink():
    # Worktrees set up for running Anki point this at the main checkout's copy, which installing
    # through would write to. rmtree refuses a symlink with an errno-less OSError.
    ANKIHUB_LIB_TARGET.unlink()
elif ANKIHUB_LIB_TARGET.exists():
    shutil.rmtree(ANKIHUB_LIB_TARGET)
for group, python_version in BUNDLE_LAYERS:
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            python_version,
            "--no-deps",
            "--target",
            str(ANKIHUB_LIB_TARGET),
            "-r",
            "-",
        ],
        check=True,
        input=layer_requirements[group],
        text=True,
    )

# A compiled extension is built for one interpreter and one platform; this artifact ships to every
# combination Anki runs on. abi3 covers only the interpreter half - protobuf-py-ext's wheel is
# cp310-abi3-manylinux_2_17_x86_64, unloadable on Windows, macOS or ARM. So what earns a place is the
# package falling back without it: protobuf-py catches the failed import, and nothing imports
# playhouse at all, so its copies are dropped instead of kept.
KEPT_EXTENSION_PACKAGES = ("protobuf_ext",)
DROPPED_EXTENSION_PACKAGES = ("playhouse",)

unaudited = []
for pattern in ("*.so", "*.pyd"):
    for extension in ANKIHUB_LIB_TARGET.rglob(pattern):
        package = extension.relative_to(ANKIHUB_LIB_TARGET).parts[0]
        if package in KEPT_EXTENSION_PACKAGES:
            continue
        if package in DROPPED_EXTENSION_PACKAGES:
            extension.unlink()
        else:
            unaudited.append(str(extension.relative_to(ANKIHUB_LIB_TARGET)))
if unaudited:
    raise SystemExit(
        f"{sorted(unaudited)} are compiled extensions this artifact would ship to platforms they "
        "cannot load on; confirm the package falls back without them, then list it above"
    )

shutil.rmtree(ANKIHUB_LIB_TARGET / "bin", ignore_errors=True)
# Remove large unused files from the Django package
for path in DJANGO_TARGET.rglob("locale/*"):
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
shutil.rmtree(DJANGO_TARGET / "contrib" / "admin" / "static", ignore_errors=True)
shutil.rmtree(DJANGO_TARGET / "contrib" / "gis", ignore_errors=True)

# A requirement whose marker was false for the interpreter or platform building it installs nothing
# and reports nothing, so its absence here is the only signal there is. Reading dist-info rather than
# files catches one that never installed, not one whose files a prune above removed. It does mean the
# build host has to be one every exported requirement applies to - Linux, for the current lock.
missing = set().union(*(requirement_names(text) for text in layer_requirements.values()))
missing -= {
    name for path in ANKIHUB_LIB_TARGET.glob("*.dist-info") for name in [canonical_name(path.name.split("-")[0])]
}
if missing:
    raise SystemExit(f"{sorted(missing)} are in the lock but not in the finished bundle")


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
# Replace :root with :host as tutorial runs in a shadow root
web_css = re.sub(r":root\[(.*?)\]", r":host([\1])", web_css)
web_css = web_css.replace(":root", ":host")
web_css = web_css.replace(".dark", ":host(.dark)")
WEB_CSS_TARGET.write_text(web_css, encoding="utf-8")
subprocess.run([shutil.which("npm"), "install"], cwd=PROJECT_ROOT / "tutorial", check=True)
subprocess.run([shutil.which("npm"), "run", "build"], cwd=PROJECT_ROOT / "tutorial", check=True)

generate_manifest()

shutil.rmtree(MEDIA_IMPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_IMPORT_SRC, MEDIA_IMPORT_TARGET)

shutil.rmtree(MEDIA_EXPORT_TARGET, ignore_errors=True)
shutil.copytree(MEDIA_EXPORT_SRC, MEDIA_EXPORT_TARGET)

obfuscate_google_api_key(GOOGLE_API_KEY, MEDIA_IMPORT_TARGET)

generate_protobuf(PROJECT_ROOT, SRC_ROOT)
