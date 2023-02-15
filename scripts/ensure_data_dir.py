import os
import pathlib


def run():
    addon_repo = pathlib.Path(__file__).parent.parent.absolute()
    addon_code = addon_repo / "ankihub"

    anki_base = os.environ.get("ANKI_BASE")
    print(f"Using {anki_base} as Anki base directory")
    if not anki_base:
        print("ANKI_BASE environment variable not set. Aborting.")
        return
    data_dir_dst = pathlib.Path(anki_base)
    addons21_dir = data_dir_dst / "addons21"
    addons21_dir.mkdir(parents=True, exist_ok=True)

    addon_dst = addons21_dir / "ankihub"
    if not addon_dst.exists():
        addon_dst.symlink_to(addon_code)
        print(f"Linked the add-on from {addon_code} to {addon_dst}")


if __name__ == "__main__":
    run()
