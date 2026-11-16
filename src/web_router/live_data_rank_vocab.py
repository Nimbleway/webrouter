"""The words and weights the live data ranking reads: kinds of page, countries, and what each match is worth.

Kept apart from :mod:`web_router.live_data_rank`, which reads queries and
scores sources with them, so the vocabulary can be extended, and the weights
tuned, in one place.
"""

from __future__ import annotations

import re
from typing import Final

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

KIND_WORDS: Final = frozenset(word for said in KINDS.values() for word in said)
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

COUNTRY_WORDS_IN_QUERY: Final = frozenset(COUNTRIES) - {"in", "it", "be"}
"""Country words read as a country in a query: all but the codes that are also English words there."""

NOT_COUNTRIES: Final = frozenset({"us", "ai", "io", "co", "tv", "me", "ly", "fm", "gg", "to", "cc"})
"""Two-letter domain endings of a site's main edition: the US, and country codes used as generic ones."""

SECOND_LEVEL: Final = frozenset({"co", "com", "org", "net", "gov", "ac"})
"""Labels that sit before a country code in a domain, as in amazon.co.uk."""

FILLER: Final = frozenset(
    {"page", "pages", "results", "data", "scraper", "api", "the", "a", "an", "of", "for", "on", "in", "it", "be"}
)
"""Query words that say nothing about which source: "amazon product page" asks what "amazon product" does.

"in", "it" and "be" among them: in a query they are English words, not the
country codes :data:`COUNTRY_WORDS_IN_QUERY` leaves out.
"""

UNFINISHED: Final = frozenset({"test", "tmp", "temp", "copy", "debug", "draft", "deprecated"})
"""Words in a source's name that mark it as someone's experiment rather than the one to run.

Whole words only, a trailing number aside (``copy2``): inside a word they are
part of real names -- latest, contest -- and "old" is a brand (old_navy).
"""

_WORD = re.compile(r"[a-z0-9]+")

# Weights: a site match outranks everything else, then the kind, then locale, then quality.
SITE_NAMED = 40
SITE_IN_NAME = 25
SITE_IN_TEXT = 5
KIND_SAME = 30
LOCALE_MATCH = 15
LOCALE_MAIN = 10
LOCALE_OTHER = -10
SCHEMA = 2
SAMPLE = 2
UNFINISHED_PENALTY = -15
FOCUS = 4
"""At most this much for a name the query explains whole: walmart_serp over walmart_store_directory_by_town."""


def words_of(text: str) -> list[str]:
    """``text``'s words, lower-cased: runs of letters and digits."""
    return _WORD.findall(text.casefold())


__all__: list[str] = [
    "COUNTRIES",
    "COUNTRY_WORDS_IN_QUERY",
    "FILLER",
    "FOCUS",
    "KINDS",
    "KIND_SAME",
    "KIND_WORDS",
    "LOCALE_MAIN",
    "LOCALE_MATCH",
    "LOCALE_OTHER",
    "NOT_COUNTRIES",
    "SAMPLE",
    "SCHEMA",
    "SECOND_LEVEL",
    "SITE_IN_NAME",
    "SITE_IN_TEXT",
    "SITE_NAMED",
    "UNFINISHED",
    "UNFINISHED_PENALTY",
    "words_of",
]
