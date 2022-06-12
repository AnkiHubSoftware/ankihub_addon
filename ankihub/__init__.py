import logging
import os
import sys

LOGGER = logging.getLogger("ankihub")
LOGGER.setLevel(logging.DEBUG)
stdout_handler = logging.StreamHandler(stream=sys.stdout)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
stdout_handler.setFormatter(formatter)
LOGGER.addHandler(stdout_handler)


SKIP_INIT = os.getenv("SKIP_INIT", False)
if not SKIP_INIT:
    # Explicit is better than implicit. (⌐⊙_⊙)
    from . import entry_point

    entry_point.run()
