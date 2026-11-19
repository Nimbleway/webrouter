"""Where a live data source reads: its site's name and country edition, and what that is worth to a query.

A domain is read into a site name and an ending (``www.amazon.co.uk`` is
``amazon`` and ``uk``); the ending says which country's edition a source
reads, else the main site. Part of :mod:`web_router.live_data_rank`.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from web_router.live_data_rank_vocab import (
    COUNTRIES,
    LOCALE_MAIN,
    LOCALE_MATCH,
    LOCALE_OTHER,
    NOT_COUNTRIES,
    SECOND_LEVEL,
    SITE_IN_NAME,
    SITE_IN_TEXT,
    SITE_NAMED,
    words_of,
)
from web_router.types_live_data import LiveDataSource


def site_name(domain: str | None) -> tuple[str | None, str | None]:
    """A domain's site name and its ending: ``www.amazon.co.uk`` is ``("amazon", "uk")``; ``amazon`` alone has none."""
    if not domain:
        return None, None
    # Some vendors write the site as a URL: https://www.aldi.us/ is www.aldi.us.
    host = urlsplit(domain).hostname if "//" in domain else domain.split("/")[0]
    labels = [label for label in (host or "").casefold().split(".") if label and label != "www"]
    if len(labels) < 2:
        return (labels[0] if labels else None), None
    ending = labels[-1]
    rest = labels[:-1]
    if len(rest) >= 2 and rest[-1] in SECOND_LEVEL and len(ending) == 2:
        rest = rest[:-1]
    return rest[-1], ending


def site_score(source: LiveDataSource, site_words: tuple[str, ...]) -> int:
    """How well ``site_words`` name ``source``'s site: its own name, else in its name, else in its text."""
    if not site_words:
        return 0
    site, _ = site_name(source.domain)
    name = set(words_of(source.name)) | set(words_of(source.domain or ""))
    total = 0
    for word in site_words:
        if word == site or (site is None and _own_name(source)[:1] == [word]):
            total += SITE_NAMED
        elif word in name:
            total += SITE_IN_NAME
        else:
            total += SITE_IN_TEXT
    return total // len(site_words)


def country_of(source: LiveDataSource) -> str | None:
    """The country edition ``source`` reads, from its domain's ending, else a country code in its name.

    Only a two-letter ending names a country, and not every one: .com, .app,
    .ai or .tv is a site's main edition.
    """
    _, ending = site_name(source.domain)
    if ending:
        return COUNTRIES.get(ending, ending) if len(ending) == 2 and ending not in NOT_COUNTRIES else None
    # A slug like amazon_in_product_reviews names its country second.
    named = _own_name(source)
    return COUNTRIES.get(named[1]) if len(named) > 1 and len(named[1]) == 2 else None


def locale_score(source: LiveDataSource, asked: str | None) -> int:
    """The country edition asked for, else the main site, over the others."""
    country = country_of(source)
    if asked is not None:
        return LOCALE_MATCH if country == asked else LOCALE_OTHER
    return LOCALE_MAIN if country is None else 0


def _own_name(source: LiveDataSource) -> list[str]:
    """The words of ``source``'s name after its owner's: an Apify actor is ``owner~amazon_uk_product``."""
    return words_of(source.name.rpartition("~")[2])


__all__: list[str] = ["country_of", "locale_score", "site_name", "site_score"]
