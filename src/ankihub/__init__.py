import os

__all__ = ["main"]

SKIP_INIT = os.getenv("SKIP_INIT", False)
if not SKIP_INIT:
    from . import main
