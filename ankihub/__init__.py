import logging.config
import os
from . import settings

logging.config.dictConfig(settings.LOGGING)
LOGGER = logging.getLogger("ankihub")

SKIP_INIT = os.getenv("SKIP_INIT", False)
LOGGER.debug(f"SKIP_INIT: {SKIP_INIT}")
if not SKIP_INIT:
    # Explicit is better than implicit. (⌐⊙_⊙)
    from . import entry_point

    entry_point.run()
