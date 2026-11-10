"""The errors a vendor call raises: an unknown vendor or endpoint, and a write that was not allowed.

Kept out of :mod:`web_router.exceptions`, which is at its size limit, as
:mod:`web_router.exceptions_budget` is; both import it, never the reverse.
"""

from __future__ import annotations

from web_router.exceptions import WebRouterError


class VendorNotFoundError(WebRouterError):
    """A vendor call named a vendor or an endpoint the catalog does not hold.

    Its own class rather than :class:`ProviderNotFoundError`: vendors are not
    providers of a method, and the message lists what the catalog does hold.
    """

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        super().__init__(message)


class VendorWriteRefusedError(WebRouterError):
    """A vendor call would change data on the vendor's side, and was not allowed to.

    Raised before anything is sent. A read never raises it; a catalog endpoint
    marked ``write``, or a raw request with a method other than GET, HEAD or
    OPTIONS, does until the call passes ``allow_writes=True``.
    """

    def __init__(self, vendor: str, target: str, message: str) -> None:
        self.vendor = vendor
        self.target = target
        super().__init__(message)
