import json
import subprocess
from pathlib import Path

from utils import to_point_version

PROJECT_ROOT = Path(__file__).parent.parent
ADDON_INFO_FILE = PROJECT_ROOT / "addon.json"
MANIFEST_FILE = PROJECT_ROOT / "ankihub/manifest.json"

VERSION_SCRIPT = Path(__file__).parent / "calver.sh"


def generate_manifest():
    addon_properties = json.load(ADDON_INFO_FILE.open("r"))
    addon_version = subprocess.run([str(VERSION_SCRIPT)], capture_output=True, text=True).stdout.strip()
    manifest = {
        "package": addon_properties["ankiweb_id"],
        "name": addon_properties["display_name"],
        "ankiweb_id": addon_properties["ankiweb_id"],
        "author": addon_properties["author"],
        "version": addon_version,
        "homepage": addon_properties["contact"],
        "conflicts": addon_properties["conflicts"],
        "min_point_version": to_point_version(addon_properties["min_anki_version"]),
        "max_point_version": _max_point_version(
            addon_properties.get("max_anki_version"),
            addon_properties.get("tested_anki_version"),
        ),
    }
    json.dump(manifest, MANIFEST_FILE.open("w"), indent=4)

    # append new line so that pre-commit doesn't complain
    with open(MANIFEST_FILE, "a") as f:
        f.write("\n")


def _max_point_version(max_anki_version: str = None, tested_anki_version: str = None) -> int:
    if max_anki_version is not None:
        # A negative max_point_version prevents the add-on from being downloaded on any newer versions.
        return -1 * to_point_version(max_anki_version)
    elif tested_anki_version is not None:
        return to_point_version(tested_anki_version)

    assert False, "Either max_anki_version or tested_anki_version must be specified"


if __name__ == "__main__":
    generate_manifest()
