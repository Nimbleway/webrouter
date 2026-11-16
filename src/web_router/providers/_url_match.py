"""Which requested URL a backend's page or failure answers, by one rule for every extract path.

A backend may hand a URL back rewritten (case, a trailing slash, a fragment
dropped) or answer under where a redirect landed. The error-page screen names
a failure by the URL it was asked for, and ``auto`` retries and merges by it,
so both read the URL through the same functions here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from urllib.parse import urldefrag, urlsplit, urlunsplit


def bare_url(url: str) -> str:
    """``url`` as a backend may echo what it fetched: scheme and host lower-cased, no fragment, no trailing slash."""
    parts = urlsplit(urldefrag(url).url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


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
