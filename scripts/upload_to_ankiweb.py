import json
import os
import time
from argparse import ArgumentParser
from pathlib import Path
from zipfile import ZipFile

from selenium.webdriver.common.by import By
from utils import to_version_str
from webbot import Browser
from webdriver_manager.chrome import ChromeDriverManager

webdriver_path = Path(ChromeDriverManager().install())


def upload(
    ankiweb_username: str,
    ankiweb_password: str,
    build_file_path: str,
    description_file_path: str,
    support_url: str,
    show_window: bool = False,
) -> None:
    with ZipFile(build_file_path) as zipf:
        zipf.extract("manifest.json")

    with open("manifest.json") as f:
        manifest_dict = json.load(f)
    os.unlink("manifest.json")

    # use webbot to upload the add-on
    web = Browser(showWindow=show_window, driverPath=webdriver_path)

    web.go_to("https://ankiweb.net/account/login")
    time.sleep(2)

    web.type(ankiweb_username, into="Email")
    web.type(ankiweb_password, into="Password")
    web.press(web.Key.ENTER)
    time.sleep(2)

    print("Url after login:", web.get_current_url())

    if manifest_dict["ankiweb_id"]:
        # update existing addon
        web.go_to(f'https://ankiweb.net/shared/upload?id={manifest_dict["ankiweb_id"]}')
    else:
        # upload new addon
        web.go_to("https://ankiweb.net/shared/upload")

    time.sleep(2)

    web.type(manifest_dict["name"], into="title")
    web.type(support_url, into="Support Page")
    web.type(
        str(Path(build_file_path).absolute()),
        xpath="//input[@type='file']",
    )

    with open(description_file_path) as f:
        description = f.read()

    # web.type doesn't handle unicode characters correctly, so we use javascript
    driver = web.driver
    description_field = driver.find_element(By.TAG_NAME, "textarea")
    escaped_description = json.dumps(description)
    driver.execute_script(
        # the event is needed, otherwise the description change will be ignored
        f"""
        arguments[0].value = {escaped_description}
        arguments[0].dispatchEvent(new Event('input'));
        """,
        description_field,
        escaped_description,
    )

    if min_point_version := manifest_dict["min_point_version"]:
        xpath = '//div[@class="form-inline"]//input[1]'
        web.type("", xpath=xpath)
        time.sleep(0.3)
        web.type(to_version_str(min_point_version), xpath=xpath)

    if max_point_version := manifest_dict["max_point_version"]:
        xpath = '//div[@class="form-inline"]//input[2]'
        web.type("", xpath=xpath)
        time.sleep(0.3)
        web.type(to_version_str(max_point_version), xpath=xpath)

    web.click("Save")

    # check if upload was successful
    for _ in range(5):
        time.sleep(1)
        url = web.get_current_url()
        if "/shared/info/" in url:
            break
    else:
        print("Url after failed upload attempt:", web.get_current_url())
        print(web.get_page_source())
        raise RuntimeError("Upload failed")


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("build_file", type=str)
    parser.add_argument("description_file", type=str)
    parser.add_argument("support_url", type=str)
    args = parser.parse_args()

    upload(
        ankiweb_username=os.environ["ANKI_USERNAME"],
        ankiweb_password=os.environ["ANKI_PASSWORD"],
        build_file_path=args.build_file,
        description_file_path=args.description_file,
        support_url=args.support_url,
    )
