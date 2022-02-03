import os

__all__ = ["gui.py"]

SKIP_INIT = os.getenv("SKIP_INIT", False)
if not SKIP_INIT:
    from . import gui
