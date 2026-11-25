"""The hops LiteParse's fetch may take, and the caller headers each one gets.

Kept apart from :mod:`web_router.providers.liteparse` so that file stays short.

:func:`~web_router.routing.url_policy.public_as_written` reads the host as
written, so a public-looking name that points at ``127.0.0.1`` passes it.
LiteParse fetches from here, so it looks each name up once and connects to that
answer; a second lookup, as :func:`~web_router.routing.url_policy.public_only`
makes, could get a different one.
"""

from __future__ import annotations

import socket
from typing import Any

import httpx

from web_router._hosts import host_address, is_loopback, is_metadata, is_private
from web_router.constants import PROVIDER_LITEPARSE
from web_router.constants_liteparse import (
    MSG_LITEPARSE_PRIVATE_HOP,
    MSG_LITEPARSE_PRIVATE_NAME,
    MSG_LITEPARSE_UNRESOLVED,
)
from web_router.constants_urls import MSG_URL_LOOPBACK, MSG_URL_METADATA, MSG_URL_PRIVATE
from web_router.exceptions import ProviderError
from web_router.routing.url_policy import any_web_url, public_as_written
from web_router.types_errors import ERROR_KIND_TARGET

Pinned = tuple[httpx.URL, dict[str, str], dict[str, Any]]
"""A hop as sent: the URL to request, its headers and its request extensions."""


def pinned(url: str, hop: str, headers: dict[str, str]) -> Pinned:
    """``hop`` as LiteParse requests it: to the address its name was checked against, when ``url`` looks public.

    The name is looked up once, and the request goes to that answer with the name kept as ``Host`` and as the TLS
    server name (httpcore verifies the certificate against it), so httpx never looks it up again: a name cannot
    answer the check with a public address and the download with a private one. Refused when any answer is not
    public, or the name does not resolve here. Refusing ``url`` itself is no fault of the file's, so a chain may
    still try it; a refused redirect hop is the target's.
    """
    target, sent = httpx.URL(hop), headers_for(headers, url, hop)
    host = target.raw_host.decode("ascii")
    if public_as_written(url) is not None or host_address(host) is not None:
        return target, sent, {}  # a private URL asked for directly, or a literal address judged as written
    why, address = _resolved(host)
    if why is not None:
        if hop == url:
            raise ProviderError(PROVIDER_LITEPARSE, MSG_LITEPARSE_PRIVATE_NAME.format(url=url, why=why))
        raise _private_hop(url, why)
    pinned_headers = {**sent, "Host": target.netloc.decode("ascii")}
    return target.copy_with(host=address), pinned_headers, {"sni_hostname": host}


def next_hop(url: str, hop: str, response: httpx.Response, *, follow: bool) -> str | None:
    """Where a redirect points, or ``None`` when ``response`` is the file; raises for a hop it may not take.

    A public URL may not redirect to a private host: a caller handing in a
    public link never meant this machine to fetch its own network.
    """
    if not (follow and response.is_redirect):
        return None
    # Joined to ``hop``, not ``response.url``: a pinned request's URL names the address, not the host.
    target = str(httpx.URL(hop).join(response.headers["location"]))
    why = public_as_written(target) if public_as_written(url) is None else any_web_url(target)
    if why is not None:
        raise _private_hop(url, why)
    return target


def headers_for(headers: dict[str, str], url: str, hop: str) -> dict[str, str]:
    """``headers`` while the fetch keeps ``url``'s scheme and host; none once a redirect leaves either."""
    u, h = httpx.URL(url), httpx.URL(hop)
    return headers if (u.scheme, u.netloc) == (h.scheme, h.netloc) else {}


def _resolved(host: str) -> tuple[str | None, str]:
    """Why ``host`` may not be fetched, or ``None``; and the address to fetch it from, an IPv4 one when it has one."""
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return MSG_LITEPARSE_UNRESOLVED, ""
    addresses = [str(answer[4][0]).split("%", 1)[0].lower() for answer in answers]
    for address in addresses:
        if is_metadata(address):
            return MSG_URL_METADATA, address
        if is_loopback(address):
            return MSG_URL_LOOPBACK, address
        if is_private(address):
            return MSG_URL_PRIVATE, address
    # IPv4 first: one address is pinned, so the fallback a lookup's other answers gave is gone.
    return None, next((address for address in addresses if ":" not in address), addresses[0])


def _private_hop(url: str, why: str) -> ProviderError:
    return ProviderError(PROVIDER_LITEPARSE, MSG_LITEPARSE_PRIVATE_HOP.format(url=url, why=why), kind=ERROR_KIND_TARGET)
