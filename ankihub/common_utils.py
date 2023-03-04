"""This module groups utility functions used by both ankihub addon and by ankihub client."""
import re
from typing import List

IMG_NAME_IN_IMG_TAG_REGEX = re.compile(r"<img.*?src=[\"'](.*?)[\"']")


def extract_local_image_paths_from_html(html_content: str) -> List[str]:
    image_paths = re.findall(IMG_NAME_IN_IMG_TAG_REGEX, html_content)

    # Filter out src attributes that are  URLs (e.g. start with http or https)
    return [
        path
        for path in image_paths
        if not any([http_prefix in path for http_prefix in ["http://", "https://"]])
    ]
