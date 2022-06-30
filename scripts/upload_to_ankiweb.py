import json
import os
import time
from argparse import ArgumentParser
from pathlib import Path
from zipfile import ZipFile

from webbot import Browser
from webdriver_manager.chrome import ChromeDriverManager, ChromeType

webdriver_path = Path(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())


def upload(
    ankiweb_username,
    ankiweb_password,
    build_file_path,
    repo_owner,
    repo_name,
    show_window=False,
):

    # unzip and read in manifest.json
    with ZipFile(build_file_path) as zipf:
        zipf.extract("manifest.json")

    with open("manifest.json") as f:
        manifest_dict = json.load(f)
    os.unlink("manifest.json")

    # use webbot to upload the add-on
    web = Browser(showWindow=show_window, driverPath=webdriver_path)
    web.go_to("https://ankiweb.net/shared/upload")
    web.type(ankiweb_username, into="username")
    web.type(ankiweb_password, into="password")
    web.press(web.Key.ENTER)

    if manifest_dict["ankiweb_id"]:
        # update existing addon
        web.go_to(f'https://ankiweb.net/shared/upload?id={manifest_dict["ankiweb_id"]}')
    else:
        # upload new addon
        web.go_to("https://ankiweb.net/shared/upload")

    web.type(manifest_dict["name"], into="title")
    web.type(
        f"https://github.com/{repo_owner}/{repo_name}/issues",
        into="support url",
    )

    web.type(
        str(Path(build_file_path).absolute()),
        id="v21file0",
    )

    # # copy description from ankiweb_description.html
    # with open(Path(addon_dir) / "ankiweb_description.html") as f:
    #     description = f.read()
    # # ... this is slow (can take 5 seconds or more)

    description = "test"  # XXX
    web.type(description, id="desc")

    # optional values
    def enter_optional_value(dict_, key, into="", id=""):
        if dict_.get(key) is not None:
            web.type(dict_[key], into=into, id=id)

    enter_optional_value(manifest_dict, "min_point_version", id="minVer0")
    enter_optional_value(manifest_dict, "max_point_version", id="maxVer0")

    if manifest_dict["ankiweb_id"]:
        web.click("Update")
    else:
        web.click("Upload")

    # check if upload was successful
    for _ in range(5):
        time.sleep(1)
        url = web.get_current_url()
        if "/shared/info/" in url:
            break
    else:
        raise RuntimeError("Upload failed")


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("build_file", type=str)
    parser.add_argument("repo_owner", type=str)
    parser.add_argument("repo_name", type=str)
    args = parser.parse_args()

    upload(
        ankiweb_username=os.environ["ANKI_USERNAME"],
        ankiweb_password=os.environ["ANKI_PASSWORD"],
        build_file_path=args.build_file,
        repo_owner=args.repo_owner,
        repo_name=args.repo_name,
    )
