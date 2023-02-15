import os
import shutil
import pathlib


def run():
    addon_repo = pathlib.Path(__file__).parent.parent.absolute()
    addon_code = addon_repo / "ankihub"

    data_dir_dst = pathlib.Path(os.environ.get("ANKI_BASE_ROOT", "/tmp")) / "Anki2"
    data_dir_src = addon_repo / "tests" / "test_data" / "Anki2"

    test_profile_id = "d1659f4e-4839-4498-8859-51d92576e1cc"
    config_for_profile = addon_repo / "tests" / "test_data" / test_profile_id

    shutil.rmtree(data_dir_dst, ignore_errors=True)
    print(f"Cleaned up Anki base destination: {data_dir_dst}")

    shutil.copytree(data_dir_src, data_dir_dst)
    print(f"Copied Anki {data_dir_src} to {data_dir_dst}")

    try:
        shutil.copytree(config_for_profile, addon_code / "user_files" / test_profile_id)
        print(f"Copied {config_for_profile} to {addon_code / 'user_files'}")
    except FileExistsError:
        print(f"User files for profile {test_profile_id} already exist")

    addon_dst = data_dir_dst / "addons21" / "ankihub"
    addon_dst.symlink_to(addon_code)
    print(f"Linked the add-on from {addon_code} to {addon_dst}")


if __name__ == "__main__":
    run()
