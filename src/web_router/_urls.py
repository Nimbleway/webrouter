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
from web_router.constants_urls import URL_BRACKETS, URL_PROSE_STOPS, URL_TRAILING_PUNCTUATION

_URL = re.compile(rf"https?://[^\s<>\"'`{URL_PROSE_STOPS}]+", re.IGNORECASE)
_SCHEME = re.compile(r"https?://", re.IGNORECASE)


def _url_end(text: str, start: int = 0) -> int:
    """Where the URL at ``start`` in ``text`` ends: before the first closing bracket it did not open."""
    opened: list[str] = []
    for i in range(start, len(text)):
        char = text[i]
        if char in URL_BRACKETS.values():
            opened.append(char)
        elif char in URL_BRACKETS and (not opened or opened.pop() != URL_BRACKETS[char]):
            return i
    return len(text)


def urls_in(text: str) -> list[str]:
    """The URLs in ``text``: a closing bracket kept only when the URL opened it, no sentence-ending punctuation."""
    found: list[str] = []
    for run in _URL.finditer(text):
        chunk, resume = run.group(), 0
        # A run can hold more than one URL: in ``[https://a](https://b)`` it goes on through the link's target,
        # which the ``]`` cuts off. Each scheme after the URL before it starts another; each character is read
        # once, so a prompt of many such fragments costs no more than its length.
        for scheme in _SCHEME.finditer(chunk):
            if scheme.start() < resume:
                continue
            url = chunk[scheme.start() : _url_end(chunk, scheme.start())].rstrip(URL_TRAILING_PUNCTUATION)
            found.append(url)
            resume = scheme.start() + len(url)
    return found


def host_of(url: str) -> str:
    """What a note or a record shows of ``url``: its scheme and host, never its path, query or credentials.

    A scheme-relative URL (``//host/path``) shows as ``//host``.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return URL_HIDDEN
    if not host or not (parts.scheme or url.startswith("//")):
        return URL_HIDDEN
    prefix = f"{parts.scheme}{':' if parts.scheme else ''}//"
    if ":" in host:  # an IPv6 literal, shown in its brackets
        try:
            # A scope id (``%eth0``) names the machine's own interface, never shown.
            return f"{prefix}[{ipaddress.IPv6Address(host.partition('%')[0])}]"
        except ValueError:
            return URL_HIDDEN
    if not host.isascii():  # shown as the ASCII a browser maps it to, so it is never cut at its first letter
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return URL_HIDDEN
    kept = re.match(HOST_CHARS, host)
    return f"{prefix}{kept.group(0)}" if kept else URL_HIDDEN


def shown(params: Mapping[str, Any]) -> dict[str, Any]:
    """``params`` as a record or a ``repr`` shows them: each URL cut to its scheme and host."""
    out = dict(params)
    if "urls" in out:
        out["urls"] = [host_of(u) for u in out["urls"]]
    if "start_url" in out:
        out["start_url"] = host_of(out["start_url"])
    return out
