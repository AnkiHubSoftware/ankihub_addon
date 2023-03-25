# Obsfucate api key in a basic way
# to prevent bots from grabbing keys.
# Not intended to prevent manual grabbing.
# Never use this for important api keys!

import random
import sys
from pathlib import Path


def obfuscate_google_api_key(google_api_key: str, dest_path: Path) -> None:
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    code = ""
    chosen = random.sample(range(0, len(chars)), len(google_api_key))
    for idx, char in enumerate(chars):
        try:
            i = chosen.index(idx)
            code += f"{char}='{google_api_key[i]}'\n"
        except ValueError:
            r = random.randint(0, len(chars) - 1)  # includes end number
            code += f"{char}='{chars[r]}'\n"

    code += "def get_google_api_key() -> str:\n"
    code += "  return ''"

    for i in chosen:
        code += "+"
        code += f"{chars[i]}"

    out_file = Path(dest_path) / "google_api_key.py"
    out_file.write_text(code)


if __name__ == "__main__":
    google_api_key = sys.argv[1]
    dest_path = sys.argv[2]
    obfuscate_google_api_key(google_api_key, dest_path)
