"""This module contains utility functions used by both ankihub addon and by ankihub client."""
import re
from typing import Set

# Regex to find the name of image files inside an <img> tag in HTML
# excluding the ones that start with http:// or https://
IMG_NAME_IN_IMG_TAG_REGEX = re.compile(
    r"<img.*?src=[\"'](?!http://|https://)(.+?)[\"']"
)
# Regex to find the name of sound files inside a [sound] tag (specific to Anki)
# excluding the ones that start with http:// or https://
SOUND_NAME_IN_SOUND_TAG_REGEX = re.compile(r"\[sound:(?!http://|https://)(.+?)\]")


def local_media_names_from_html(html_content: str) -> Set[str]:
    image_names = re.findall(IMG_NAME_IN_IMG_TAG_REGEX, html_content)
    sound_names = re.findall(SOUND_NAME_IN_SOUND_TAG_REGEX, html_content)
    all_names = set(image_names + sound_names)
    return all_names
