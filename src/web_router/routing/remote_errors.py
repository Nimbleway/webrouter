"""The remote router's errors: each names the service by scheme and host, never its key.

A timeout is ``router_timeout``; a transport error, a status other than 200 or
a decision naming a ``schema`` other than 1 is ``router_error``; an answer that
is not an uncompressed ``application/json`` decision within 64 KiB on the
wire -- ``schema`` missing included -- is ``router_invalid``. Either way the
call runs on the static order and the decision record says why.
"""

from __future__ import annotations

from web_router.constants_remote import MSG_REMOTE_FAILED
from web_router.exceptions import WebRouterError
from web_router.routing._runner import InvalidAnswer, TimedOut


class RemoteRouterError(WebRouterError):
    """The router service gave no usable decision. It names the service by scheme and host, never its key."""

    def __init__(self, host: str, why: str) -> None:
        self.host = host
        self.why = why
        super().__init__(MSG_REMOTE_FAILED.format(host=host, why=why))


class RemoteRouterTimeout(RemoteRouterError, TimedOut):
    """The service did not answer within the call's deadline: recorded as ``router_timeout``."""


class RemoteRouterInvalid(RemoteRouterError, InvalidAnswer):
    """The service answered, but not with a well-formed decision: recorded as ``router_invalid``."""
