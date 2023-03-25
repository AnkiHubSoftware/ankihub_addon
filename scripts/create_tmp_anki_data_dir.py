import os
import pathlib
import shutil


def run():
    addon_repo = pathlib.Path(__file__).parent.parent.absolute()
    addon_code = addon_repo / "ankihub"

    if "TEMPORARY_ANKI_BASE" not in os.environ:
        print("TEMPORARY_ANKI_BASE environment variable not set. Aborting.")

    anki_base_dst_path = pathlib.Path(os.environ["TEMPORARY_ANKI_BASE"])
    print(f"Using {anki_base_dst_path} as Anki base directory")

    shutil.rmtree(anki_base_dst_path, ignore_errors=True)
    print(f"Cleaned up Anki base destination: {anki_base_dst_path}")

    anki_base_src_path = addon_repo / "tests" / "test_data" / "Anki2"
    shutil.copytree(anki_base_src_path, anki_base_dst_path)
    print(f"Copied Anki {anki_base_src_path} to {anki_base_dst_path}")

    test_profile_id = "d1659f4e-4839-4498-8859-51d92576e1cc"
    profile_config_path = addon_repo / "tests" / "test_data" / test_profile_id
    profiles_dir_path = addon_code / "user_files"
    try:
        shutil.copytree(profile_config_path, profiles_dir_path / test_profile_id)
        print(f"Copied {profile_config_path} to {profiles_dir_path}")
    except FileExistsError:
        print(f"User files for profile {test_profile_id} already exist")

    addon_dst = anki_base_dst_path / "addons21" / "ankihub"
    addon_dst.symlink_to(addon_code)
    print(f"Linked the add-on from {addon_code} to {addon_dst}")


if __name__ == "__main__":
    run()
