"""Find the URLs written in a piece of prose, such as a query."""

from __future__ import annotations

import re

from web_router.constants_urls import URL_TRAILING_PUNCTUATION

_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def urls_in(text: str) -> list[str]:
    """The URLs in ``text``, without the punctuation that ends a sentence around them."""
    return [url.rstrip(URL_TRAILING_PUNCTUATION) for url in _URL.findall(text)]
