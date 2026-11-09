"""The URLs an agent hands the direct tools, screened before anything is sent.

``web_run``'s plans hold to ``url_policy="public_only"`` in the library; the
direct ``extract``, ``browse`` and ``run_template`` tools hold their own URLs to
the same check here, so a page that steers the model cannot have the user's
keyed account hand a vendor a ``file:``, loopback, private or metadata URL.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from web_router._urls import host_of
from web_router.constants import MCP_BROWSER_REFUSED_SCHEMES
from web_router.constants_mcp import MCP_MSG_URL_REFUSED
from web_router.constants_urls import URL_SCHEMES
from web_router.routing.url_policy import public_only


def screen(tool: str, urls: object) -> object:
    """``urls`` as the call should send it, once every URL in it is a public http(s) one.

    A string is one URL -- the library takes ``urls="https://..."`` as a list
    of one -- and any other iterable is read once, as the library reads it (a
    mapping by its keys), and handed back as a list so a one-shot iterator is
    not read empty a second time. A value that is not a string is left for the
    library's validation.

    Raises:
        ValueError: naming each refused host and why, before anything is sent.
    """
    if isinstance(urls, str):
        candidates: list[object] = [urls]
    elif isinstance(urls, Iterable):
        urls = candidates = list(urls)
    else:
        candidates = []
    refused = [f"{host_of(url)} ({why})" for url in candidates if isinstance(url, str) and (why := public_only(url))]
    if refused:
        raise ValueError(MCP_MSG_URL_REFUSED.format(tool=tool, refused="; ".join(refused)))
    return urls


def url_values(inputs: object) -> list[str]:
    """Every URL among a template's inputs, however deep, and inside free text: ids, slugs and words pass.

    A browser drops tabs and line breaks from a URL, so they are dropped first;
    then every word that is a URL is screened on its own: one with ``://``, a
    scheme-relative ``//host``, a scheme followed by ``/`` (``gopher:/host``,
    ``http:/127.0.0.1``), or a scheme the browser guard names (``javascript:``).
    """
    if isinstance(inputs, Mapping):
        return [url for value in inputs.values() for url in url_values(value)]
    if isinstance(inputs, list):
        return [url for value in inputs for url in url_values(value)]
    if not isinstance(inputs, str):
        return []
    return [word for word in inputs.translate(_DROPPED).split() if _is_url(word)]


def _is_url(word: str) -> bool:
    if "://" in word or word.startswith("//"):
        return True
    scheme = _SCHEME.match(word)
    if scheme is None or not (rest := word[scheme.end() :]):
        # "http:" on its own -- a word in a search term -- names no address.
        return False
    return rest.startswith("/") or scheme.group(1).lower() in MCP_BROWSER_REFUSED_SCHEMES | URL_SCHEMES


_DROPPED = {ord(ch): None for ch in "\t\n\r"}
"""What a browser strips from anywhere in a URL before reading it."""

_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
"""A URL's scheme, at the start of a word."""
