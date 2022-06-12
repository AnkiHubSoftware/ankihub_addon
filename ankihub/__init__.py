import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER = logging.getLogger("ankihub")
LOGGER.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

stdout_handler = logging.StreamHandler(stream=sys.stdout)
stdout_handler.setFormatter(formatter)
LOGGER.addHandler(stdout_handler)

LOG_FILENAME = Path(__file__).parent / "user_files/log.txt"
file_handler = RotatingFileHandler(LOG_FILENAME, maxBytes=2000, backupCount=5)
file_handler.setFormatter(formatter)
LOGGER.addHandler(file_handler)

SKIP_INIT = os.getenv("SKIP_INIT", False)
if not SKIP_INIT:
    # Explicit is better than implicit. (⌐⊙_⊙)
    from . import entry_point

    entry_point.run()
