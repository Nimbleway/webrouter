"""Tokenizing the texts a vendor search compares."""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> frozenset[str]:
    """Lowercase words, plurals folded: "monitors" and "monitor" are one word to a search.

    Not cached: a caller passes query and page text, which must not outlive the
    request. The catalog's own endpoint text is tokenized once, in
    ``VendorCatalog._terms``.
    """
    return frozenset(
        word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        for word in _WORD.findall(text.lower())
    )
