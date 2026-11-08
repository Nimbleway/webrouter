"""How the merge rule shows a URL a router wrote: its scheme and host, never its path, query or credentials.

Experimental. Apart from :mod:`.merge_params` so each stays small. A URL is
never written into a note, a record or a ``repr`` whole:
:func:`web_router._urls.host_of` keeps its scheme and host.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import overload

from web_router._urls import host_of
from web_router.constants_merge import URL_IN_TEXT

UrlCheck = Callable[[str], str | None]
"""A URL policy: why a URL is refused, or ``None`` when it is allowed."""

URL_KEYS = frozenset({"urls", "start_url"})


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
