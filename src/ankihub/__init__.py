import os

SKIP_INIT = os.getenv("SKIP_INIT", False)
if not SKIP_INIT:
    # Explicit is better than implicit. (⌐⊙_⊙)
    from . import entry_point

    entry_point.run()
