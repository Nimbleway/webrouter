"""The URLs an agent hands the direct tools, screened before anything is sent.

``web_run``'s plans hold to ``url_policy="public_only"`` in the library; the
direct ``extract``, ``browse`` and ``run_template`` tools hold their own URLs to
the same check here, so a page that steers the model cannot have the user's
keyed account hand a vendor a ``file:``, loopback, private or metadata URL.
"""

from __future__ import annotations

from collections.abc import Mapping

from web_router._urls import host_of
from web_router.constants import MCP_BROWSER_REFUSED_SCHEMES
from web_router.constants_mcp import MCP_MSG_URL_REFUSED
from web_router.constants_urls import URL_SCHEMES
from web_router.routing.url_policy import public_only


def screen(tool: str, urls: object) -> None:
    """Refuse the call before anything is sent when a URL in ``urls`` is not a public http(s) one.

    A string is one URL -- the library takes ``urls="https://..."`` as a list of one.
    Anything that is not a string is left for the library's validation.
    """
    candidates = [urls] if isinstance(urls, str) else urls if isinstance(urls, list) else []
    refused = [f"{host_of(url)} ({why})" for url in candidates if isinstance(url, str) and (why := public_only(url))]
    if refused:
        raise ValueError(MCP_MSG_URL_REFUSED.format(tool=tool, refused="; ".join(refused)))


def url_values(inputs: object) -> list[str]:
    """Every URL among a template's inputs, however deep -- an id, a slug or a search term is not screened.

    A URL is a value with ``://`` in it, a scheme-relative ``//host/...``, or
    one word that starts with a scheme a browser reads without ``//``:
    ``file:/etc/passwd``, ``http:/127.0.0.1/``, ``javascript:``.
    """
    if isinstance(inputs, Mapping):
        return [url for value in inputs.values() for url in url_values(value)]
    if isinstance(inputs, list):
        return [url for value in inputs for url in url_values(value)]
    return [inputs] if isinstance(inputs, str) and _looks_like_url(inputs) else []


def _looks_like_url(value: str) -> bool:
    """``://`` anywhere, ``//host``, or one word opening with a scheme a browser reads without ``//``."""
    scheme, sep, _ = value.strip().partition(":")
    one_word = bool(sep) and not any(ch.isspace() for ch in value.strip())
    return "://" in value or value.lstrip().startswith("//") or (one_word and scheme.lower() in _SCHEMES)


_SCHEMES = MCP_BROWSER_REFUSED_SCHEMES | URL_SCHEMES
"""A search term such as ``http: 404`` has a space in it, so it is not taken for one of these."""
