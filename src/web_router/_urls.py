"""Find the URLs written in a piece of prose, such as a query, and show one as its scheme and host only.

Two URL-in-text patterns exist on purpose: :data:`_URL` here finds URLs to fetch, and
:data:`~web_router.constants_merge.URL_IN_TEXT` finds them in a router's words to redact.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from web_router.constants_merge import HOST_CHARS, URL_HIDDEN
from web_router.constants_urls import URL_BRACKETS, URL_TRAILING_PUNCTUATION

_URL = re.compile(r"https?://[^\s<>\"'`]+")


def _url_end(text: str) -> int:
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
    return match[: _url_end(match)].rstrip(URL_TRAILING_PUNCTUATION)


def urls_in(text: str) -> list[str]:
    """The URLs in ``text``: a closing bracket kept only when the URL opened it, no sentence-ending punctuation."""
    return [_trimmed(match) for match in _URL.findall(text)]


def host_of(url: str) -> str:
    """What a note or a record shows of ``url``: its scheme and host, never its path, query or credentials."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return URL_HIDDEN
    if parts.scheme and host and ":" in host:  # an IPv6 literal, shown in its brackets
        try:
            return f"{parts.scheme}://[{ipaddress.IPv6Address(host)}]"
        except ValueError:
            return URL_HIDDEN
    kept = re.match(HOST_CHARS, host or "")
    return f"{parts.scheme}://{kept.group(0)}" if parts.scheme and kept else URL_HIDDEN


def shown(params: Mapping[str, Any]) -> dict[str, Any]:
    """``params`` as a record or a ``repr`` shows them: each URL cut to its scheme and host."""
    out = dict(params)
    if "urls" in out:
        out["urls"] = [host_of(u) for u in out["urls"]]
    if "start_url" in out:
        out["start_url"] = host_of(out["start_url"])
    return out
