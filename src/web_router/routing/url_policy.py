"""``url_policy``: which URLs a routed call or a plan may send to a backend.

Experimental. A pre-dispatch hostname check: it reads the URL as written --
a literal address in any spelling a browser accepts is judged as the address
it is -- and it neither resolves DNS nor follows redirects, so a public name
that points at a private address is not caught here. Off by default: cloud
backends fetch from their own networks and cannot reach a caller's private
hosts, and a backend registered to fetch locally may be meant for an intranet.

- ``None`` (the default) admits any ``http`` or ``https`` URL.
- ``"public_only"`` also refuses loopback, link-local and cloud metadata
  hosts, and private, shared (CGNAT) and reserved addresses -- for a server
  where a prompt could steer a locally fetching backend at its own network.
  For a cloud backend it mainly protects third parties.

It holds the URLs :meth:`~web_router.WebRouter.plan` finds in a prompt, before
any candidate is built, and the URLs a router sets in ``params``, through the
merge rule. The MCP browser tools keep their own, stricter guard.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, get_args
from urllib.parse import urlsplit

from web_router._hosts import is_loopback, is_metadata, is_private
from web_router.constants_urls import (
    LOCALHOST_SUFFIX,
    MSG_URL_LOOPBACK,
    MSG_URL_METADATA,
    MSG_URL_POLICY,
    MSG_URL_PRIVATE,
    MSG_URL_SCHEME,
    MSG_URL_UNPARSEABLE,
    URL_REFUSED_KEY,
    URL_SCHEMES,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.merge import UrlCheck
from web_router.routing.merge_params import host_of

UrlPolicy = Literal["public_only"]
"""The URL policies a router takes; ``None`` is none."""


def _scheme_refusal(url: str) -> tuple[str | None, str]:
    """Why ``url`` is no http(s) URL, or ``None``; and its host, lowercased, without a trailing dot."""
    try:
        parts = urlsplit(url)
        scheme, host = parts.scheme.lower(), (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        return MSG_URL_UNPARSEABLE, ""
    if scheme not in URL_SCHEMES or not host:
        return MSG_URL_SCHEME.format(scheme=scheme or ""), host
    return None, host


def any_web_url(url: str) -> str | None:
    """With no policy: why ``url`` is refused -- only when it is not an http(s) URL -- or ``None``."""
    return _scheme_refusal(url)[0]


def public_only(url: str) -> str | None:
    """Why ``url`` is refused under ``"public_only"``, or ``None`` when its host may be public."""
    why, host = _scheme_refusal(url)
    if why is not None:
        return why
    if is_metadata(host):
        return MSG_URL_METADATA
    if is_loopback(host) or host.endswith(LOCALHOST_SUFFIX):
        return MSG_URL_LOOPBACK
    if is_private(host):
        return MSG_URL_PRIVATE
    return None


def url_check(policy: UrlPolicy | None) -> UrlCheck:
    """The check for ``policy``, as the merge rule takes it.

    Raises:
        ConfigurationError: for a policy that is neither ``None`` nor one of :data:`UrlPolicy`.
    """
    if policy is None:
        return any_web_url
    if policy not in get_args(UrlPolicy):
        known = ", ".join(repr(name) for name in get_args(UrlPolicy))
        raise ConfigurationError(MSG_URL_POLICY.format(known=known, got=policy))
    return public_only


def screened(urls: Sequence[str], check: UrlCheck) -> tuple[list[str], dict[str, str]]:
    """``urls`` the check admits, in order; and each refused one, keyed by its scheme and host, with why."""
    kept: list[str] = []
    refused: dict[str, str] = {}
    for url in urls:
        why = check(url)
        if why is None:
            kept.append(url)
        else:
            refused[URL_REFUSED_KEY.format(host=host_of(url))] = why
    return kept, refused
