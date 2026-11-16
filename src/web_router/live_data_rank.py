"""Ranking live data sources for a keyword search: the site asked for, the kind of page, then quality.

A query like ``"amazon product"`` names a site and a kind of page. Vendors
label the kind in their own words -- Nimble's "Product Detail Page (PDP)",
Oxylabs' ``amazon/product``, a slug like ``amazon_in_product_reviews`` -- so
each source's kind is read into one shared vocabulary (:data:`KINDS`), and the
query's kind words are matched against that rather than as text: "product"
asks for a product page, and a reviews page that happens to say "product" no
longer counts as one.

:func:`score` adds up what a source has going for it:

- **site** -- a query word that is the site's own name (``amazon`` for
  www.amazon.com) beats one found elsewhere in its name, which beats one
  found only in its description;
- **kind** -- the page is the kind asked for, rather than one that does not say;
- **locale** -- the main site (amazon.com) unless the query names a country,
  then that country's site;
- **quality** -- what makes a source likelier to run as listed, read only
  from what any vendor's source can carry: a schema, a recorded sample, and
  no test or copy in its name.

:func:`relevant` is the gate: every query word that is not a kind or a
country must appear in what the source says about itself, and a source of
another kind than the one asked for is left out. Ties keep the
vendor's order. Kept apart from :mod:`web_router.live_data_search` so the
scoring can be read, and tested, on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from web_router.types_live_data import LiveDataSource

KINDS: Final[dict[str, frozenset[str]]] = {
    "reviews": frozenset({"review", "reviews", "rating", "ratings", "comment", "comments"}),
    "article": frozenset({"article", "articles"}),
    "offers": frozenset({"offer", "offers", "pricing"}),
    "search": frozenset({"search", "serp", "searches"}),
    "category": frozenset({"category", "categories", "clp", "plp", "bestsellers", "collection", "browse"}),
    "product": frozenset({"product", "products", "pdp", "item", "detail", "details"}),
}
"""Each kind of page, and the words that name it in a query, a vendor's label or a source's name.

In order of precedence: a source named for a product's reviews is a reviews
page, not a product page, so the more specific kinds come first.
"""

_KIND_WORDS: Final = frozenset(word for said in KINDS.values() for word in said)
"""Every word that names a kind: none of them is a site word, whichever kind a query is read as."""

COUNTRIES: Final[dict[str, str]] = {
    "uk": "uk", "gb": "uk", "britain": "uk", "england": "uk", "de": "de", "germany": "de",
    "fr": "fr", "france": "fr", "it": "it", "italy": "it", "es": "es", "spain": "es",
    "in": "in", "india": "in", "jp": "jp", "japan": "jp", "ca": "ca", "canada": "ca",
    "au": "au", "australia": "au", "nl": "nl", "netherlands": "nl", "se": "se", "sweden": "se",
    "pl": "pl", "poland": "pl", "br": "br", "brazil": "br", "mx": "mx", "mexico": "mx",
    "ae": "ae", "uae": "ae", "sa": "sa", "sg": "sg", "singapore": "sg", "tr": "tr", "turkey": "tr",
    "be": "be", "belgium": "be", "eg": "eg", "egypt": "eg", "cz": "cz", "ie": "ie", "ireland": "ie",
}  # fmt: skip
"""Country words a query or a source's name may carry, each to the code its site's domain ends in."""

_COUNTRY_WORDS_IN_QUERY: Final = frozenset(COUNTRIES) - {"in", "it", "be"}
"""Country words read as a country in a query: all but the codes that are also English words there."""

_NOT_COUNTRIES: Final = frozenset({"us", "ai", "io", "co", "tv", "me", "ly", "fm", "gg", "to", "cc"})
"""Two-letter domain endings of a site's main edition: the US, and country codes used as generic ones."""

_SECOND_LEVEL: Final = frozenset({"co", "com", "org", "net", "gov", "ac"})
"""Labels that sit before a country code in a domain, as in amazon.co.uk."""

_FILLER: Final = frozenset({"page", "pages", "results", "data", "scraper", "api", "the", "a", "an", "of", "for", "on"})
"""Query words that say nothing about which source: "amazon product page" asks what "amazon product" does."""

_UNFINISHED: Final = frozenset({"test", "tmp", "temp", "copy", "debug", "draft", "deprecated"})
"""Words in a source's name that mark it as someone's experiment rather than the one to run.

Whole words only, a trailing number aside (``copy2``): inside a word they are
part of real names -- latest, contest -- and "old" is a brand (old_navy).
"""

_WORD = re.compile(r"[a-z0-9]+")

# Weights: a site match outranks everything else, then the kind, then locale, then quality.
_SITE_NAMED = 40
_SITE_IN_NAME = 25
_SITE_IN_TEXT = 5
_KIND_SAME = 30
_LOCALE_MATCH = 15
_LOCALE_MAIN = 10
_LOCALE_OTHER = -10
_SCHEMA = 2
_SAMPLE = 2
_UNFINISHED_PENALTY = -15
_FOCUS = 4
"""At most this much for a name the query explains whole: walmart_serp over walmart_store_directory_by_town."""


@dataclass(frozen=True)
class Query:
    """A keyword query read into what it asks for."""

    site_words: tuple[str, ...]
    """Words to find in the source: a site, a brand, anything that is not a kind or a country."""
    kind: str | None
    """The kind of page asked for, when a query word names one."""
    country: str | None
    """The country asked for, when a query word names one."""


def read_query(query: str) -> Query:
    """``query`` split into the site words, the kind of page and the country it asks for."""
    words = [word for word in _words(query) if word not in _FILLER]
    named = [name for name, said in KINDS.items() if said.intersection(words)]
    # "search amazon products": beside another kind, "search" is the verb, not the page asked for.
    kind = next((name for name in named if name != "search"), named[0] if named else None)
    country = next((COUNTRIES[word] for word in words if word in _COUNTRY_WORDS_IN_QUERY), None)
    site = tuple(word for word in words if word not in _KIND_WORDS and word not in _COUNTRY_WORDS_IN_QUERY)
    return Query(site_words=site, kind=kind, country=country)


def kind_of(source: LiveDataSource) -> str | None:
    """The kind of page ``source`` reads, from its name and title, else from its vendor's label.

    The name first: it is the one each source was given on its own, where a
    label is picked from a few, and often loosely -- a template named for a
    product's reviews can be labelled a product page.
    """
    for text in (_name_and_title(source), source.entity_type):
        found = _kind_in(_words(text or ""))
        if found:
            return found
    return None


def relevant(source: LiveDataSource, query: Query) -> bool:
    """Whether ``source`` answers ``query`` at all.

    Every site word must appear in what it says about itself, and when the
    query asks for a kind of page, the source must be that kind or not say
    which, with or without site words: a search results page is no answer to
    "amazon product" or "product", and a source that does not say its kind is
    one to both, ranked below those that do.

    A query with no site word and no kind asks only for a country, and is
    answered by that country's sources ("uk"); one with not even a country --
    only filler words, as in "page data" -- names nothing to match, and
    matches no source rather than the whole catalogue.
    """
    if not query.site_words and query.kind is None:
        return query.country is not None and _country_of(source) == query.country
    if query.kind is not None and kind_of(source) not in (query.kind, None):
        return False
    text = haystack(source)
    return all(word in text for word in query.site_words)


def score(source: LiveDataSource, query: Query) -> int:
    """How well ``source`` answers ``query``: higher first. Only meaningful for a :func:`relevant` source."""
    return (
        _site(source, query) + _kind(source, query) + _locale(source, query) + _quality(source) + _focus(source, query)
    )


def haystack(source: LiveDataSource) -> str:
    """Everything ``source`` says about itself, lower-cased: name, site, kind, title and description."""
    title = source.extra.get("title") or source.extra.get("display_name")
    parts = (source.name, source.domain, source.entity_type, title if isinstance(title, str) else None)
    return " ".join(part for part in (*parts, source.description) if part).casefold()


# -- helpers ------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _kind_in(words: list[str]) -> str | None:
    present = set(words)
    return next((name for name, said in KINDS.items() if present & said), None)


def _name_and_title(source: LiveDataSource) -> str:
    title = source.extra.get("title") or source.extra.get("display_name")
    return " ".join(part for part in (source.name, title if isinstance(title, str) else None) if part)


def _site_name(domain: str | None) -> tuple[str | None, str | None]:
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
    if len(rest) >= 2 and rest[-1] in _SECOND_LEVEL and len(ending) == 2:
        rest = rest[:-1]
    return rest[-1], ending


def _site(source: LiveDataSource, query: Query) -> int:
    if not query.site_words:
        return 0
    site, _ = _site_name(source.domain)
    name = set(_words(source.name)) | set(_words(source.domain or ""))
    total = 0
    for word in query.site_words:
        if word == site or (site is None and _words(source.name)[:1] == [word]):
            total += _SITE_NAMED
        elif word in name:
            total += _SITE_IN_NAME
        else:
            total += _SITE_IN_TEXT
    return total // len(query.site_words)


def _kind(source: LiveDataSource, query: Query) -> int:
    if query.kind is None:
        return 0
    # A source of another kind is not relevant at all; one that does not say which ranks below the kind asked.
    return _KIND_SAME if kind_of(source) == query.kind else 0


def _country_of(source: LiveDataSource) -> str | None:
    """The country edition ``source`` reads, from its domain's ending, else a country code in its name.

    Only a two-letter ending names a country, and not every one: .com, .app,
    .ai or .tv is a site's main edition.
    """
    _, ending = _site_name(source.domain)
    if ending:
        return COUNTRIES.get(ending, ending) if len(ending) == 2 and ending not in _NOT_COUNTRIES else None
    # A slug like amazon_in_product_reviews names its country second.
    words = _words(source.name)
    return COUNTRIES.get(words[1]) if len(words) > 1 and len(words[1]) == 2 else None


def _locale(source: LiveDataSource, query: Query) -> int:
    country = _country_of(source)
    if query.country is not None:
        return _LOCALE_MATCH if country == query.country else _LOCALE_OTHER
    return _LOCALE_MAIN if country is None else 0


def _focus(source: LiveDataSource, query: Query) -> int:
    """A tie-break: the share of the name's words the query explains, as its site, kind or country.

    Dates and generated suffixes -- any word with a digit -- are not counted either way.
    """
    words = [word for word in _words(source.name) if not any(char.isdigit() for char in word)]
    if not words:
        return 0
    asked = set(query.site_words) | (KINDS[query.kind] if query.kind else frozenset())
    explained = sum(1 for word in words if word in asked or (query.country and COUNTRIES.get(word) == query.country))
    return _FOCUS * explained // len(words)


def _quality(source: LiveDataSource) -> int:
    """What the source shows of itself, on fields any vendor fills alike.

    Not ``published``, nor a vendor's own catalogue flags: Nimble's public
    listing rows carry no version, and only Nimble says public or private, so
    either would rank one vendor's sources above another's equal ones.
    """
    points = 0
    if source.input_schema:
        points += _SCHEMA
    if source.samples:
        points += _SAMPLE
    if _UNFINISHED.intersection(word.rstrip("0123456789") for word in _words(source.name)):
        points += _UNFINISHED_PENALTY
    return points


__all__: list[str] = ["COUNTRIES", "KINDS", "Query", "haystack", "kind_of", "read_query", "relevant", "score"]
