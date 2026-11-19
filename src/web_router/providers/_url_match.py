"""Which requested URL a backend's page or failure answers, by one rule for every extract path.

A backend may hand a URL back rewritten (case, a trailing slash, a fragment
dropped) or answer under where a redirect landed. The error-page screen names
a failure by the URL it was asked for, and ``auto`` retries and merges by it,
so both read the URL through the same functions here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from urllib.parse import quote, urldefrag, urlsplit, urlunsplit

_ASCII_SAFE = "".join(chr(code) for code in range(0x21, 0x7F))
"""Every printable ASCII character: :func:`_encoded` percent-encodes only what lies outside it, and keeps ``%``."""


def bare_url(url: str) -> str:
    """``url`` as a backend may echo what it fetched: scheme and host lower-cased, no fragment, no trailing slash.

    It is also read in the encoded form a fetcher sends: a non-ASCII host as IDNA (``xn--``), and non-ASCII
    characters in the path and query percent-encoded, every escape in upper case, so ``/café`` is ``/caf%C3%A9``.
    """
    parts = urlsplit(urldefrag(url).url)
    netloc = _idna(parts.netloc.lower())
    return urlunsplit((parts.scheme.lower(), netloc, _encoded(parts.path.rstrip("/")), _encoded(parts.query), ""))


def _idna(netloc: str) -> str:
    """``netloc`` with a non-ASCII host as the ASCII a browser sends, or unchanged where IDNA cannot encode it."""
    if netloc.isascii():
        return netloc
    userinfo, at, hostport = netloc.rpartition("@")
    host, colon, port = hostport.partition(":")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return netloc
    return f"{userinfo}{at}{host}{colon}{port}"


def _encoded(text: str) -> str:
    """``text`` with its non-ASCII characters percent-encoded as UTF-8 and every escape upper-cased."""
    return re.sub(r"%[0-9a-fA-F]{2}", lambda escape: escape.group(0).upper(), quote(text, safe=_ASCII_SAFE))


def asked_for(url: str, urls: Sequence[str]) -> set[str]:
    """The requested URLs ``url`` stands for: itself when asked for exactly, else the one a backend rewrote into it."""
    if url in urls:
        return {url}
    # A rewrite that could be either of two URLs asked for proves neither was read.
    matches = {asked for asked in urls if bare_url(asked) == bare_url(url)}
    return matches if len(matches) == 1 else set()


def owners(answered: Sequence[str], urls: Sequence[str], also_claimed: Iterable[str] = ()) -> list[str | None]:
    """The requested URL each of ``answered`` is for, or ``None`` where it cannot be told.

    A URL is matched as :func:`asked_for` reads it, and when exactly one
    answer matches nothing and exactly one requested URL is unclaimed -- by
    the answers or by ``also_claimed``, the URLs the backend reported failed --
    that redirect is the unclaimed URL's. So one requested URL owns its own
    page, or the one redirect it led to, never a second unrelated page.
    """
    matched = [next(iter(found)) if (found := asked_for(url, urls)) else None for url in answered]
    claimed = {owner for owner in matched if owner} | {o for url in also_claimed for o in asked_for(url, urls)}
    unclaimed = [url for url in urls if url not in claimed]
    if matched.count(None) == 1 and len(unclaimed) == 1:
        matched[matched.index(None)] = unclaimed[0]
    return matched
