"""The URLs an agent hands the direct tools, screened before anything is sent.

``web_run``'s plans hold to ``url_policy="public_only"`` in the library; the
direct ``extract``, ``browse`` and ``run_template`` tools hold their own URLs to
the same check here, so a page that steers the model cannot have the user's
keyed account hand a vendor a ``file:``, loopback, private or metadata URL.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from web_router._urls import host_of
from web_router.constants import MCP_BROWSER_REFUSED_SCHEMES
from web_router.constants_mcp import MCP_MSG_URL_REFUSED
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


def url_values(inputs: Mapping[str, Any]) -> list[str]:
    """A template's inputs that are URLs -- an id, a slug or a search term is not screened.

    A URL is a value with ``://`` in it, or one that starts with a scheme a URL
    needs no ``//`` for: ``file:/etc/passwd``, ``data:``, ``javascript:``.
    """
    values = [item for value in inputs.values() for item in (value if isinstance(value, list) else [value])]
    return [value for value in values if isinstance(value, str) and ("://" in value or _bare_scheme(value))]


def _bare_scheme(value: str) -> bool:
    scheme, sep, _ = value.partition(":")
    return bool(sep) and scheme.strip().lower() in MCP_BROWSER_REFUSED_SCHEMES
