import os

__all__ = ["gui"]

SKIP_INIT = os.getenv("SKIP_INIT", False)
if not SKIP_INIT:
    # Explicit is better than implicit. (⌐⊙_⊙)
    from ankihub import entry_point
    entry_point.run()
