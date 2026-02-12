"""This module contains utility functions used by both ankihub addon and by ankihub client."""

import re
from typing import Set

# Regex to find the name of image files inside an <img> tag in HTML
# excluding the ones that start with http:// or https://
IMG_NAME_IN_IMG_TAG_REGEX = re.compile(r"(?i)<img.*?src=[\"'](?!http://|https://)(.+?)[\"']")
# Regex to find the name of sound files inside a [sound] tag (specific to Anki)
# excluding the ones that start with http:// or https://
SOUND_NAME_IN_SOUND_TAG_REGEX = re.compile(r"(?i)\[sound:(?!http://|https://)(.+?)\]")
# Regex to find CSS import statements and url() references
CSS_IMPORT_REGEX = re.compile(r"(?i)(?:@import\s+[\"'](.+?)[\"'])")
# Regex to find CSS url() references
CSS_URL_REGEX = re.compile(r"(?i)(?:url\(\s*[\"']([^\"]+)[\"'])")


def local_media_names_from_html(html_content: str) -> Set[str]:
    image_names = re.findall(IMG_NAME_IN_IMG_TAG_REGEX, html_content)
    sound_names = re.findall(SOUND_NAME_IN_SOUND_TAG_REGEX, html_content)
    css_import_names = re.findall(CSS_IMPORT_REGEX, html_content)
    css_url_names = re.findall(CSS_URL_REGEX, html_content)
    all_names = set(image_names + sound_names + css_import_names + css_url_names)
    return all_names
