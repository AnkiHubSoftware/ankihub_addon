from typing import Optional


class AddonError(Exception):
    """All Exceptions defined in this addon is a subclass of this class"""

    def __init__(self, code: Optional[int] = None, msg: Optional[str] = None):
        super().__init__()
        self.code = code
        self.msg = msg

    def __str__(self) -> str:
        class_name = self.__class__.__name__
        errmsg = class_name
        if self.code is not None:
            errmsg += (f"<{self.code}>")
        if self.msg:
            errmsg += (f": {self.msg}")
        return errmsg


class MalformedURLError(AddonError):
    """The URL doesn't pass regex search. """
    pass


class RootNotFoundError(AddonError):
    """The URL looks fine, but doesn't point to a valid location."""
    pass


class IsAFileError(AddonError):
    """Expected a directory, but is a file instead. """
    pass


class RateLimitError(AddonError):
    """Rate Limit Exceeded. """
    pass


class ServerError(AddonError):
    """Nothing wrong with the request, but with the server."""
    pass


class RequestError(AddonError):
    """Other various errors that happened during request."""
    pass
