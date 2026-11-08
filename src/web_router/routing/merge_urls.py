"""How the merge rule shows a URL a router wrote: its scheme and host, never its path, query or credentials.

Experimental. Apart from :mod:`.merge_params` so each stays small. A URL is
never written into a note, a record or a ``repr`` whole.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, overload
from urllib.parse import urlsplit

from web_router.constants_merge import HOST_CHARS, URL_HIDDEN, URL_IN_TEXT

UrlCheck = Callable[[str], str | None]
"""A URL policy: why a URL is refused, or ``None`` when it is allowed."""

URL_KEYS = frozenset({"urls", "start_url"})


def host_of(url: str) -> str:
    """What a note or a record shows of ``url``: its scheme and host, never its path, query or credentials.

    A scheme-relative URL (``//host/path``) shows as ``//host``.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return URL_HIDDEN
    kept = re.match(HOST_CHARS, host or "")
    if kept is None or not (parts.scheme or url.startswith("//")):
        return URL_HIDDEN
    return f"{parts.scheme}{':' if parts.scheme else ''}//{kept.group(0)}"


def _hide(match: re.Match[str]) -> str:
    """The whole matched URL, and whatever ran on after it, as its scheme and host only."""
    return host_of(match.group(0))


@overload
def hide_urls(text: str) -> str: ...
@overload
def hide_urls(text: None) -> None: ...
def hide_urls(text: str | None) -> str | None:
    """``text`` with every URL in it cut to its scheme and host: for anything a router wrote, ids included."""
    return None if text is None else re.sub(URL_IN_TEXT, _hide, text)


def shown(params: Mapping[str, Any]) -> dict[str, Any]:
    """``params`` as a record or a ``repr`` shows them: each URL cut to its scheme and host."""
    out = dict(params)
    if "urls" in out:
        out["urls"] = [host_of(u) for u in out["urls"]]
    if "start_url" in out:
        out["start_url"] = host_of(out["start_url"])
    return out
