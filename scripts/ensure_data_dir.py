import os
import pathlib


def run():
    addon_repo = pathlib.Path(__file__).parent.parent.absolute()
    addon_code = addon_repo / "ankihub"

    if "ANKI_BASE" not in os.environ:
        print("ANKI_BASE environment variable not set. Aborting.")

    anki_base_dst_path = pathlib.Path(os.environ["ANKI_BASE"])
    print(f"Using {anki_base_dst_path} as Anki base directory")

    addons21_dir = anki_base_dst_path / "addons21"
    addons21_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created addons21 directory: {addons21_dir} (if it didn't exist already)")

    addon_dst = addons21_dir / "ankihub"
    if addon_dst.exists():
        os.unlink(addon_dst)
        print(f"Removed existing symlink to add-on code at {addon_dst}")

    addon_dst.symlink_to(addon_code)
    print(f"Linked the add-on from {addon_code} to {addon_dst} ")


if __name__ == "__main__":
    run()
