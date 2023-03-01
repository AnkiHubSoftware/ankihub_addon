"""This module groups utility functions used by both ankihub addon and by ankihub client."""
import re

def extract_local_image_paths_from_html(html_content: str) -> List[str]:
    image_paths = re.findall(r'<img.*?src="(.*?)"', html_content)

    # Filter out src attributes that are  URLs (e.g. start with http or https)
    return [
        path
        for path in image_paths
        if not any([http_prefix in path for http_prefix in ["http://", "https://"]])
    ]