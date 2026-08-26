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

# The oldest Python that any Anki version reaching the modern-only layer can be running.
OLDEST_MODERN_PYTHON = "3.10"

# Install each layer with the oldest Python that can reach it, so uv refuses a distribution that
# has dropped support for it. Separate dependency groups, so routing never reads markers.
BUNDLE_LAYERS = (("bundle", OLDEST_ANKI_PYTHON), ("bundle_modern", OLDEST_MODERN_PYTHON))


def locked_group_requirements(group: str) -> str:
    """A dependency group's exact contents, as recorded in uv.lock.

    Resolving the group at build time instead would pick up whatever versions are current then.
    """
    return subprocess.run(
        [
            "uv",
            "export",
            # Without this, export re-resolves against PyPI and rewrites uv.lock. It also fails
            # when the lockfile is out of date with pyproject.toml rather than preferring one.
            "--locked",
            # Without --color never the export carries ANSI escapes that uv cannot re-parse.
            "--color",
            "never",
            "--only-group",
            group,
            "--no-emit-project",
        ],
        check=True,
        stdout=subprocess.PIPE,  # stderr stays on the console so uv explains its own failures
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(requirements: str) -> "set[str]":
    names = set()
    for line in requirements.splitlines():
        if not line.strip() or line.startswith("#") or line[0].isspace():
            continue  # blank, a comment, or an indented --hash continuation
        if line.startswith("--"):
            continue  # --index-url and friends configure the install; they add no distribution
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
        if not match:
            # Skipping silently is what makes this worth raising over: an editable (-e ../pkg)
            # installs something that every check below would then be blind to.
            raise SystemExit(f"cannot read {line!r} as a requirement; check what uv export emitted")
        names.add(canonical_name(match.group()))
    return names


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
    # Worktrees set up for running Anki point this at the main checkout's copy, and installing
    # through it would write there instead. rmtree refuses a symlink with an errno-less OSError.
    ANKIHUB_LIB_TARGET.unlink()
elif ANKIHUB_LIB_TARGET.exists():
    shutil.rmtree(ANKIHUB_LIB_TARGET)
subprocess.run(["uv", "python", "install", *dict(BUNDLE_LAYERS).values()], check=True)
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

# A compiled extension is built for one interpreter and one platform, and this one artifact ships to
# every combination Anki runs on. abi3 answers only the first half: protobuf-py-ext's wheel is
# cp310-abi3-manylinux_2_17_x86_64, so it still cannot load on Windows, macOS or ARM. What makes an
# extension safe to vendor is therefore the package treating it as optional, not its tag - and both
# of these do. protobuf-py catches the failed import and falls back to pure Python, confirmed on
# macOS arm64, so its extension is kept for the platform it does load on. Nothing imports playhouse
# at all, so its 2 MB are dropped. Anything else fails the build rather than being kept or deleted
# on a guess: kept, it breaks every platform but the builder; deleted, it breaks whatever needed it.
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

# Every distribution the lock exported has to have arrived. A requirement whose marker was false for
# the interpreter or platform building it installs nothing and reports nothing, so its absence here
# is the only signal there is. This reads dist-info, so it catches a distribution that never
# installed rather than one whose files a prune above removed while leaving its metadata. It does
# mean the build host has to be one every exported requirement applies to, which for the current
# lock means Linux, as every workflow producing an artifact uses.
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
