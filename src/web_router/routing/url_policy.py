"""``url_policy``: which URLs a routed call or a plan may fetch, or hand a backend as a URL.

Experimental. A pre-dispatch hostname check: it reads the URL as written --
a literal address in any spelling a browser accepts is judged as the address
it is -- and it neither resolves DNS nor follows redirects, so a public name
that points at a private address is not caught here. Off by default: cloud
backends fetch from their own networks and cannot reach a caller's private
hosts, and a backend registered to fetch locally may be meant for an intranet.

- ``None`` (the default) admits any ``http`` or ``https`` URL.
- ``"public_only"`` also refuses loopback, link-local and cloud metadata
  hosts, and private, shared (CGNAT) and reserved addresses, and any URL a
  browser could read a different host from (a backslash, ``@`` or ``%`` in
  the authority; whitespace or a control character anywhere) -- for a server
  where a prompt could steer a locally fetching backend at its own network.
  For a cloud backend it mainly protects third parties.

It holds the URLs :meth:`~web_router.WebRouter.plan` finds in a prompt, before
any candidate is built, and the URLs a router sets in ``params``, through the
merge rule -- only those: a URL the caller passes to ``extract()`` or
``browse()`` directly is not held to it. A refused URL is never fetched and
never passed as ``urls`` or ``start_url``, but it is not cut from the prompt:
the prompt's text, the URL in it, is still what a search, answer or research
backend gets as its query and a browse agent as its task. The MCP browser
tools have their own guard, a different one: it resolves DNS and follows
redirects, but admits loopback and private hosts.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from typing import Literal, get_args
from urllib.parse import urlsplit

from web_router._hosts import is_loopback, is_metadata, is_private, numeric_host
from web_router._urls import host_of
from web_router.constants_urls import (
    AUTHORITY_REFUSED,
    LOCALHOST_SUFFIX,
    MSG_URL_AMBIGUOUS,
    MSG_URL_LOOPBACK,
    MSG_URL_METADATA,
    MSG_URL_NO_HOST,
    MSG_URL_POLICY,
    MSG_URL_PRIVATE,
    MSG_URL_SCHEME,
    MSG_URL_UNPARSEABLE,
    URL_REFUSED_KEY,
    URL_SCHEMES,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.merge_urls import UrlCheck

UrlPolicy = Literal["public_only"]
"""The URL policies a router takes; ``None`` is none."""


def _scheme_refusal(url: str) -> tuple[str | None, str]:
    """Why ``url`` is no http(s) URL, or ``None``; and its host as written."""
    try:
        parts = urlsplit(url)
        scheme, host = parts.scheme.lower(), parts.hostname or ""
    except ValueError:
        return MSG_URL_UNPARSEABLE, ""
    if scheme not in URL_SCHEMES:
        return MSG_URL_SCHEME.format(scheme=scheme), host
    if not host:
        return MSG_URL_NO_HOST, host
    return None, host


def any_web_url(url: str) -> str | None:
    """With no policy: why ``url`` is refused -- only when it is not an http(s) URL -- or ``None``."""
    return _scheme_refusal(url)[0]


def _ambiguous(url: str) -> bool:
    """Whether a browser's WHATWG parse could read ``url``'s host differently from :func:`urllib.parse.urlsplit`.

    Refused rather than reconciled: whitespace or a control character anywhere
    (a browser strips some of them), and in the authority a backslash (a
    browser reads it as ``/``, ending the host early), an ``@`` (userinfo, the
    classic host confusion) or a ``%`` (a browser decodes a percent-encoded host).
    """
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return True
    _, sep, rest = url.partition(":")
    authority = rest[2:] if sep and rest.startswith("//") else rest
    for end in "/?#":
        authority = authority.split(end, 1)[0]
    return any(ch in authority for ch in AUTHORITY_REFUSED)


def _as_browser_host(host: str) -> str | None:
    """``host`` as a browser connects to it, or ``None`` when it does not map.

    A non-ASCII name is mapped by IDNA (full-width digits, ideographic dots), then -- for every host -- one
    trailing dot is dropped and the name lowercased, so ``127.0.0.1。`` is judged as ``127.0.0.1``.
    """
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    return host.removesuffix(".").lower()


def public_only(url: str) -> str | None:
    """Why ``url`` is refused under ``"public_only"``, or ``None`` when its host may be public."""
    if _ambiguous(url):
        return MSG_URL_AMBIGUOUS
    why, written = _scheme_refusal(url)
    if why is not None:
        return why
    host = _as_browser_host(written)
    if host is None:
        return MSG_URL_AMBIGUOUS
    if is_metadata(host):
        return MSG_URL_METADATA
    if is_loopback(host) or host.endswith(LOCALHOST_SUFFIX):
        return MSG_URL_LOOPBACK
    if is_private(host):
        return MSG_URL_PRIVATE
    if numeric_host(host) and not _canonical_address(host):
        # 010.0.0.1: a browser reads octal, macOS's getaddrinfo decimal; 127.0.0.09, no octal, decimal there too.
        # No public URL needs such a spelling, whether or not inet_aton reads it.
        return MSG_URL_AMBIGUOUS
    return None


def _canonical_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


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
