import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ADDON_INFO_FILE = PROJECT_ROOT / "addon.json"
MANIFEST_FILE = PROJECT_ROOT / "ankihub/manifest.json"

VERSION_SCRIPT = Path(__file__).parent / "calver.sh"
ADDON_VERSION = subprocess.run(
    [str(VERSION_SCRIPT)], capture_output=True, text=True
).stdout.strip()

addon_properties = json.load(ADDON_INFO_FILE.open("r"))
manifest = {
    "package": addon_properties["module_name"],
    "name": addon_properties["display_name"],
    "ankiweb_id": addon_properties["ankiweb_id"],
    "author": addon_properties["author"],
    "version": ADDON_VERSION,
    "homepage": addon_properties["contact"],
    "conflicts": addon_properties["conflicts"],
}
json.dump(manifest, MANIFEST_FILE.open("w"), indent=4)
