"""This module contains utility functions used by both ankihub addon and by ankihub client."""
import re
from typing import Set

IMG_NAME_IN_IMG_TAG_REGEX = re.compile(r"<img.*?src=[\"'](.*?)[\"']")
SOUND_NAME_IN_SOUND_TAG_REGEX = re.compile(r"\[sound:(.*?)\]")


def local_media_names_from_html(html_content: str) -> Set[str]:
    image_names = re.findall(IMG_NAME_IN_IMG_TAG_REGEX, html_content)
    sound_names = re.findall(SOUND_NAME_IN_SOUND_TAG_REGEX, html_content)
    all_names = image_names + sound_names

    # Filter out links to media hosted on the web
    result = {
        name
        for name in all_names
        if not any([http_prefix in name for http_prefix in ["http://", "https://"]])
    }
    return result
