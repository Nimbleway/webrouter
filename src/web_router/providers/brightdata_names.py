"""Readable names for Bright Data's scrapers, so a run names one the way every other vendor's source is named.

Bright Data identifies a scraper by a dataset id (``gd_l7q7dkf244hwjntr0``)
and each scraper collects in one or more ways: by a page's URL, and often by
discovery -- a keyword, a category URL, a UPC. Here each (scraper, way) is one
source, named from the scraper's own name:

- ``amazon_products`` -- collect by URL, every scraper's base method;
- ``amazon_products_by_keyword`` -- the ``discover_by_keyword`` method;
- a name two scrapers share gets the site appended
  (``sephora_products_sephora_fr``), and if that still clashes, the dataset id.

A final name that still clashes once the method is added gets the dataset id
too. A dataset id is still accepted as a source, meaning that scraper's URL method.

Names follow the catalogue as it is now: when Bright Data adds a scraper with a
name already taken, both gain their site, and the old name stops resolving
(the not-found error lists the names that replaced it). The ``gd_`` dataset id
is the name that never changes, so store that one.
Checked against Bright Data's published catalogue on 2026-11-09: 1,301
scrapers give 1,297 distinct base names before the site rule.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from web_router.constants_live_data_vendors import BRIGHTDATA_DEFAULT_METHOD, BRIGHTDATA_DISCOVER_PREFIX

_NOT_WORD = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """``"Amazon products"`` -> ``"amazon_products"``."""
    return _NOT_WORD.sub("_", text.lower()).strip("_")


@dataclass(frozen=True)
class Scraper:
    """One runnable source: which dataset, which collection method, and the catalogue entry it came from."""

    name: str
    dataset_id: str
    method: str
    item: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def discovers(self) -> bool:
        return self.method != BRIGHTDATA_DEFAULT_METHOD

    @property
    def discover_by(self) -> str:
        """The ``discover_by`` query value for a discovery method: ``keyword`` for ``discover_by_keyword``."""
        return self.method.removeprefix(BRIGHTDATA_DISCOVER_PREFIX)


def methods_of(item: dict[str, Any]) -> list[str]:
    """A scraper's collection methods, URL first: the keys of the management API's ``scrapers``."""
    found = item.get("scrapers")
    names = set(found) if isinstance(found, dict) else set()
    return [BRIGHTDATA_DEFAULT_METHOD, *sorted(names - {BRIGHTDATA_DEFAULT_METHOD})]


def dataset_id(item: dict[str, Any]) -> str:
    """The scraper's id, the management API's ``id``."""
    return str(item.get("id") or "")


def _base_names(items: list[dict[str, Any]]) -> list[str]:
    bases = [slug(str(item.get("name") or dataset_id(item))) for item in items]
    clashing = {base for base, count in Counter(bases).items() if count > 1}
    bases = [
        f"{base}_{slug(str(item.get('domain') or ''))}" if base in clashing else base
        for base, item in zip(bases, items, strict=True)
    ]
    clashing = {base for base, count in Counter(bases).items() if count > 1}
    return [
        f"{base}_{dataset_id(item).lower()}" if base in clashing else base
        for base, item in zip(bases, items, strict=True)
    ]


def _named(base: str, method: str) -> str:
    return base if method == BRIGHTDATA_DEFAULT_METHOD else f"{base}_{method.removeprefix('discover_')}"


def plain_name(scraper: Scraper) -> str:
    """The name a source has while nothing else in the catalogue shares it: before any site or id is appended."""
    return _named(slug(str(scraper.item.get("name") or scraper.dataset_id)), scraper.method)


def scrapers(items: list[dict[str, Any]]) -> list[Scraper]:
    """Every runnable source in a catalogue, in catalogue order, each scraper's URL method first.

    A final name two sources share -- a scraper named "Foo by keyword" beside
    "Foo"'s keyword discovery -- gets each one's dataset id appended, so
    neither is lost from the listing.
    """
    found: list[Scraper] = []
    for base, item in zip(_base_names(items), items, strict=True):
        for method in methods_of(item):
            found.append(Scraper(name=_named(base, method), dataset_id=dataset_id(item), method=method, item=item))
    clashing = {name for name, count in Counter(scraper.name for scraper in found).items() if count > 1}
    return [
        replace(scraper, name=f"{scraper.name}_{scraper.dataset_id.lower()}") if scraper.name in clashing else scraper
        for scraper in found
    ]
