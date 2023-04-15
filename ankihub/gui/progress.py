"""Modifies to Anki's progress dialog (aqt.progress)."""
from typing import Callable, Optional

from anki.hooks import wrap
from aqt import sip
from aqt.progress import ProgressManager

from .. import LOGGER


def setup():
    # This patch prevents some unnecessary errors when Anki
    # tries to close the progress window when it is already closed.
    # Users reported errors like this:
    # TypeError: isdeleted() argument 1 must be sip.simplewrapper, not None after ProgressManager._closeWin was called.
    # See for example https://github.com/ankipalace/ankihub_addon/issues/227
    # Not sure how the value can be None, maybe it's caused by another add-on.
    ProgressManager._closeWin = wrap(  # type: ignore
        old=ProgressManager._closeWin,
        new=_with_patched_isdeleted,
        pos="around",
    )


def _with_patched_isdeleted(
    self: ProgressManager, _old: Callable[[ProgressManager], None]
):
    original_is_deleted = sip.isdeleted

    def patched_isdeleted(x: Optional[sip.simplewrapper]) -> bool:
        if x is None:
            LOGGER.warning("sip.isdeleted was called with None")
            return True

        return original_is_deleted(x)

    sip.isdeleted = patched_isdeleted  # type: ignore
    try:
        _old(self)
    finally:
        sip.isdeleted = original_is_deleted
