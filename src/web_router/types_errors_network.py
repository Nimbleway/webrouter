"""Whether a backend call failed because this machine could not reach the backend's host, and which host that was.

A leaf below :mod:`web_router.types_errors`, which reads these kinds from here: a proxy that refused the host, a
name that did not resolve, a connection refused. These are the caller's network, not the backend failing, so
another backend whose host is allowed may still answer.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable, Iterator
from ipaddress import ip_address
from typing import Final, Literal
from urllib.parse import urlsplit

NetworkKind = Literal["network_blocked", "unreachable"]
"""The two kinds of :data:`~web_router.types_errors.ProviderErrorKind` that this module reads."""

ERROR_KIND_NETWORK_BLOCKED: Final[NetworkKind] = "network_blocked"
ERROR_KIND_UNREACHABLE: Final[NetworkKind] = "unreachable"
NETWORK_KINDS: Final = frozenset({ERROR_KIND_NETWORK_BLOCKED, ERROR_KIND_UNREACHABLE})
"""The kinds that say this machine's network kept the call from the backend: allow the host, or use another."""

PROXY_AUTH_STATUS: Final = 407
"""A proxy's own refusal: it wants credentials before it will pass the call on."""

_PROXY_ERROR_NAMES: Final = ("ProxyError", "ProxyConnectionError")
"""How the transports' own type names for a proxy's refusal end: httpx's, httpcore's, requests' and urllib3's
``ProxyError``, aiohttp's ``ClientProxyConnectionError``. Matched at the end, so a provider's own config error
that merely mentions a proxy is not one."""

_LOCALHOST: Final = "localhost"

_CAUSE_DEPTH: Final = 6
"""How far down an error's causes to read: an SDK's error, httpx's, httpcore's, then the socket's own."""


def cause_chain(exc: BaseException | None) -> Iterator[BaseException]:
    """``exc`` and the errors it carries as its cause (else its context), at most :data:`_CAUSE_DEPTH`, each once."""
    seen: set[int] = set()
    while exc is not None and len(seen) < _CAUSE_DEPTH and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)


def network_kind(chain: Iterable[BaseException], status: int | None) -> NetworkKind | None:
    """``network_blocked`` for a proxy's refusal, ``unreachable`` for a host that did not resolve or answer, else none.

    ``chain`` is the error and the errors it carries as its cause, as an SDK wraps the transport's own error.
    """
    if status == PROXY_AUTH_STATUS:
        return ERROR_KIND_NETWORK_BLOCKED
    links = list(chain)
    if any(type(link).__name__.endswith(_PROXY_ERROR_NAMES) for link in links):
        return ERROR_KIND_NETWORK_BLOCKED
    if any(isinstance(link, socket.gaierror) for link in links):
        return ERROR_KIND_UNREACHABLE
    if any(isinstance(link, ConnectionRefusedError) for link in links) and _public_host(request_host(links[0])):
        return ERROR_KIND_UNREACHABLE
    return None


def _public_host(host: str | None) -> bool:
    if not host or host.lower().rstrip(".") == _LOCALHOST:
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local)


def request_host(exc: BaseException | None) -> str | None:
    """The host a failed request was sent to, read from the first error in ``exc``'s chain that carries its request."""
    for link in cause_chain(exc):
        try:
            request = getattr(link, "request", None)
        except RuntimeError:  # httpx raises when an error was built without its request
            continue
        url = getattr(request, "url", None)
        host = getattr(url, "host", None) or (urlsplit(url).hostname if isinstance(url, str) else None)
        if host:
            return str(host)
    return None
