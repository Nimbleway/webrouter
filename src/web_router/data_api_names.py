"""Which data APIs a search query names, and which of their read endpoints it asks for.

Discovery is on for every search, so a match has to be one the query asked
for. A query must name the data API -- its id ("datadog", "google ads" for
``google-ads``) or a distinctive word of its display name ("Edgar", "Crunchbase")
-- and say what it wants from it: another query word in an endpoint's id or job,
or, for an API named by its id, anywhere in an endpoint's name or summary. A
query that names an API by its id and nothing else lists its first read
endpoints. Everyday searches name no data API, so they list none.
"""

from __future__ import annotations

import functools
import itertools
import re
from collections import Counter

from web_router.constants_data_api import (
    DATA_API_COMMON_NAME_SHARE,
    DATA_API_GENERIC_NAME_WORDS,
    DATA_API_NAME_MIN_LENGTH,
)
from web_router.constants_vendors import VENDOR_FIND_STOP_WORDS
from web_router.vendors.catalog import VendorCatalog, words
from web_router.vendors.schema import Vendor, VendorEndpoint

_TOKEN = re.compile(r"[a-z0-9]+")
_ID_BONUS = 10
"""How far naming an API by its id ranks it above one named only by a word of its display name."""


def _slug(vendor: Vendor) -> str:
    return vendor.vendor.replace("-", "")


def _name_words(vendor: Vendor) -> set[str]:
    return words(vendor.name) | words(vendor.vendor.replace("-", " "))


@functools.lru_cache(maxsize=4)
def _naming_words(catalog: VendorCatalog) -> dict[str, frozenset[str]]:
    """Per data API, the words of its display name that name it alone: long enough and not shared by many."""
    shared = Counter(word for vendor in catalog.all() for word in _name_words(vendor))
    return {
        vendor.vendor: frozenset(
            word
            for word in _name_words(vendor)
            if len(word) >= DATA_API_NAME_MIN_LENGTH
            and shared[word] < DATA_API_COMMON_NAME_SHARE
            and word not in DATA_API_GENERIC_NAME_WORDS
        )
        for vendor in catalog.all()
    }


def named_endpoints(catalog: VendorCatalog, query: str) -> list[tuple[Vendor, VendorEndpoint]]:
    """The read endpoints of the data APIs ``query`` names, best first; empty when it names none."""
    tokens = _TOKEN.findall(query.lower())
    ids = set(tokens) | {first + second for first, second in itertools.pairwise(tokens)}
    query_words = words(query) - VENDOR_FIND_STOP_WORDS
    naming = _naming_words(catalog)
    scored: list[tuple[int, int, str, Vendor, VendorEndpoint]] = []
    for vendor in catalog.all():
        by_id = _slug(vendor) in ids
        named = query_words & naming[vendor.vendor]
        if by_id:
            named |= query_words & words(vendor.vendor.replace("-", " "))
        if not named and not by_id:
            continue
        asked = query_words - named
        for position, endpoint in enumerate(vendor.endpoints):
            if endpoint.kind != "read":
                continue
            strong = asked & words(f"{endpoint.id} {endpoint.job or ''}")
            weak = asked & words(f"{endpoint.name} {endpoint.summary}")
            if not strong and not (by_id and (weak or not asked)):
                continue
            score = _ID_BONUS * by_id + 3 * len(strong) + len(weak)
            scored.append((-score, position, endpoint.id, vendor, endpoint))
    scored.sort(key=lambda row: row[:3])
    return [(vendor, endpoint) for *_, vendor, endpoint in scored]
