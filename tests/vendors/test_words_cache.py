"""``catalog.words`` is cached, and returns a value the next caller cannot mutate.

A find tokenizes every endpoint text again for each query, so the cache is what
keeps a second query from paying for the first query's tokenizing.
"""

from __future__ import annotations

from web_router.vendors import catalog


def test_words_is_cached_and_frozen() -> None:
    first = catalog.words("Stripe Payments API")
    assert first is catalog.words("Stripe Payments API")
    assert isinstance(first, frozenset)
    assert "stripe" in first
