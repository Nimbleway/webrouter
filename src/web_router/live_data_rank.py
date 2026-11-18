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
scoring can be read, and tested, on its own; the vocabulary and weights are
:mod:`web_router.live_data_rank_vocab`, and reading a domain's site and
country is :mod:`web_router.live_data_rank_site`.
"""

from __future__ import annotations

from dataclasses import dataclass

from web_router.live_data_rank_site import country_of, locale_score, site_score
from web_router.live_data_rank_vocab import (
    COUNTRIES,
    COUNTRY_WORDS_IN_QUERY,
    FILLER,
    FOCUS,
    KIND_SAME,
    KIND_WORDS,
    KINDS,
    SAMPLE,
    SCHEMA,
    UNFINISHED,
    UNFINISHED_PENALTY,
    words_of,
)
from web_router.types_live_data import LiveDataSource


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
    words = [word for word in words_of(query) if word not in FILLER]
    named = _kinds_in(words)
    # "search amazon products": beside another kind, "search" is the verb, not the page asked for.
    kind = next((name for name in named if name != "search"), named[0] if named else None)
    country = next((COUNTRIES[word] for word in words if word in COUNTRY_WORDS_IN_QUERY), None)
    site = tuple(word for word in words if word not in KIND_WORDS and word not in COUNTRY_WORDS_IN_QUERY)
    return Query(site_words=site, kind=kind, country=country)


def kind_of(source: LiveDataSource) -> str | None:
    """The kind of page ``source`` reads, from its name and title, else from its vendor's label.

    The name first: it is the one each source was given on its own, where a
    label is picked from a few, and often loosely -- a template named for a
    product's reviews can be labelled a product page.
    """
    for text in (_name_and_title(source), source.entity_type):
        found = next(iter(_kinds_in(words_of(text or ""))), None)
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
        return query.country is not None and country_of(source) == query.country
    if query.kind is not None and kind_of(source) not in (query.kind, None):
        return False
    text = haystack(source)
    return all(word in text for word in query.site_words)


def score(source: LiveDataSource, query: Query) -> int:
    """How well ``source`` answers ``query``: higher first. Only meaningful for a :func:`relevant` source."""
    return (
        site_score(source, query.site_words)
        + _kind(source, query)
        + locale_score(source, query.country)
        + _quality(source)
        + _focus(source, query)
    )


def haystack(source: LiveDataSource) -> str:
    """Everything ``source`` says about itself, lower-cased: name, site, kind, title and description."""
    parts = (source.name, source.domain, source.entity_type, _title(source), source.description)
    return " ".join(part for part in parts if part).casefold()


# -- helpers ------------------------------------------------------------------


def _kinds_in(words: list[str]) -> list[str]:
    """Every kind ``words`` name, in :data:`KINDS`' order of precedence."""
    return [name for name, said in KINDS.items() if said.intersection(words)]


def _title(source: LiveDataSource) -> str | None:
    """The source's display title, when its vendor gives one."""
    title = source.extra.get("title") or source.extra.get("display_name")
    return title if isinstance(title, str) else None


def _name_and_title(source: LiveDataSource) -> str:
    return " ".join(part for part in (source.name, _title(source)) if part)


def _kind(source: LiveDataSource, query: Query) -> int:
    if query.kind is None:
        return 0
    # A source of another kind is not relevant at all; one that does not say which ranks below the kind asked.
    return KIND_SAME if kind_of(source) == query.kind else 0


def _focus(source: LiveDataSource, query: Query) -> int:
    """A tie-break: the share of the name's words the query explains, as its site, kind or country.

    Dates and generated suffixes -- any word with a digit -- are not counted either way.
    """
    words = [word for word in words_of(source.name) if not any(char.isdigit() for char in word)]
    if not words:
        return 0
    asked = set(query.site_words) | (KINDS[query.kind] if query.kind else frozenset())
    explained = sum(1 for word in words if word in asked or (query.country and COUNTRIES.get(word) == query.country))
    return FOCUS * explained // len(words)


def _quality(source: LiveDataSource) -> int:
    """What the source shows of itself, on fields any vendor fills alike.

    Not ``published``, nor a vendor's own catalogue flags: Nimble's public
    listing rows carry no version, and only Nimble says public or private, so
    either would rank one vendor's sources above another's equal ones.
    """
    points = 0
    if source.input_schema:
        points += SCHEMA
    if source.samples:
        points += SAMPLE
    if UNFINISHED.intersection(word.rstrip("0123456789") for word in words_of(source.name)):
        points += UNFINISHED_PENALTY
    return points


__all__: list[str] = ["COUNTRIES", "KINDS", "Query", "haystack", "kind_of", "read_query", "relevant", "score"]
