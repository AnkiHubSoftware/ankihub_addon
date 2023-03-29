"""This module contains utility functions used by both ankihub addon and by ankihub client."""
import re
from typing import Set

IMG_NAME_IN_IMG_TAG_REGEX = re.compile(r"<img.*?src=[\"'](.*?)[\"']")


def local_image_names_from_html(html_content: str) -> Set[str]:
    image_names = re.findall(IMG_NAME_IN_IMG_TAG_REGEX, html_content)

    # Filter out src attributes that are  URLs (e.g. start with http or https)
    return {
        name
        for name in image_names
        if not any([http_prefix in name for http_prefix in ["http://", "https://"]])
    }
