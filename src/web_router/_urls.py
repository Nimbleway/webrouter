"""Find the URLs written in a piece of prose, such as a query."""

from __future__ import annotations

import re

from web_router.constants_urls import URL_BRACKETS, URL_TRAILING_PUNCTUATION

_URL = re.compile(r"https?://[^\s<>\"'`]+")


def url_end(text: str) -> int:
    """Where the URL at the start of ``text`` ends: before the first closing bracket it did not open."""
    opened: list[str] = []
    for i, char in enumerate(text):
        if char in URL_BRACKETS.values():
            opened.append(char)
        elif char in URL_BRACKETS and (not opened or opened.pop() != URL_BRACKETS[char]):
            return i
    return len(text)


def _trimmed(match: str) -> str:
    """``match`` cut at a closing bracket it did not open, then without the punctuation that ends a sentence."""
    return match[: url_end(match)].rstrip(URL_TRAILING_PUNCTUATION)


def urls_in(text: str) -> list[str]:
    """The URLs in ``text``: a closing bracket kept only when the URL opened it, no sentence-ending punctuation."""
    return [_trimmed(match) for match in _URL.findall(text)]
