import os
from argparse import ArgumentParser
from pathlib import Path


def setup_addon_symlink(anki_base_path: Path) -> None:
    addon_repo = Path(__file__).parent.parent.absolute()
    addon_code = addon_repo / "ankihub"

    print(f"Using {anki_base_path} as Anki base directory")

    addons21_dir = anki_base_path / "addons21"
    addons21_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created addons21 directory: {addons21_dir} (if it didn't exist already)")

    addon_dst = addons21_dir / "ankihub"
    ankiwebview_inspector = Path(
        "/Users/andrewsanchez/Projects/anki21-addon-ankiwebview-inspector/src"
    )
    inspector_dst = addons21_dir / "inspector"
    if inspector_dst.is_symlink():
        os.remove(inspector_dst)
        inspector_dst.symlink_to(ankiwebview_inspector)
        print(f"Removed existing symlink: {inspector_dst}")

    if addon_dst.is_symlink():
        os.remove(addon_dst)
        print(f"Removed existing symlink: {addon_dst}")

    addon_dst.symlink_to(addon_code)
    print(f"Linked the add-on from {addon_code} to {addon_dst} ")


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument(
        "anki_base_path",
        type=str,
        help="Path to the Anki base directory (where addons21 is located)",
    )
    args = parser.parse_args()
    setup_addon_symlink(Path(args.anki_base_path))
