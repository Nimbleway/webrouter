"""Jina Reader and Search: their endpoints, formats and option mappings.

Kept apart from :mod:`web_router.constants`, as the other newer backends' are,
so that file stops growing. The env var and the provider name stay there,
beside every other backend's.
"""

from __future__ import annotations

from typing import Final

from web_router.types import ExtractFormat, SearchDepth

# --- Reader, the keyless route to a page -----------------------------------------
# A key only raises the rate limit: 20 requests a minute without one, 500 with.
# Verified live, keyless.

JINA_BASE_URL = "https://r.jina.ai"
JINA_EXTRACT_PATH = "/"
JINA_API_KEY_PREFIX = "Bearer "
JINA_ACCEPT = "application/json"
"""Without it the reply is the page itself rather than a JSON envelope.

``Accept`` chooses the envelope and ``X-Return-Format`` chooses what is inside
it; they are independent, and asking only for a format returns bare text.
"""

JINA_RETURN_FORMAT_HEADER = "X-Return-Format"

JINA_EXTRACT_FORMATS: dict[ExtractFormat, str] = {
    # Normalized format -> the X-Return-Format value, which is also the key the
    # body lands under inside `data` -- except for markdown, which arrives as
    # `content`. Verified live: asking for html returns `data.html` and omits
    # `data.content` entirely, so the key has to follow the format.
    "markdown": "markdown",
    "html": "html",
    "text": "text",
}

JINA_BODY_FIELDS: dict[ExtractFormat, str] = {
    "markdown": "content",
    "html": "html",
    "text": "text",
}

JINA_EXTRACT_DEFAULT_FORMAT: ExtractFormat = "markdown"
"""Verified live: with no ``X-Return-Format`` the body arrives as ``data.content``."""

# Reader may answer from its own cache rather than fetching, and says so in an
# unmapped `data.warning` -- verified live: "This is a cached snapshot of the
# original page, consider retry with caching opt-out." That reaches the caller
# through ExtractedDocument.extra, which is where SECURITY.md points for it.
# Not mapped onto a normalized field: no other backend reports anything
# comparable, so there is nothing to normalize it against yet.

# --- Search -------------------------------------------------------------------
# GET https://s.jina.ai/?q=... (s.jina.ai/openapi.json), with the Bearer key
# Reader takes. Unlike Reader, search refuses an anonymous call: checked on
# 2026-11-24, it answers 401 AuthenticationRequiredError without a key.

JINA_SEARCH_BASE_URL: Final = "https://s.jina.ai"
JINA_SEARCH_PATH: Final = "/"

JINA_SEARCH_PARAMETERS = frozenset({"max_results", "search_depth"})
"""What Jina search maps: ``max_results`` as ``num``, and ``search_depth``.

``X-Respond-With: no-content`` is Jina's own knob, and the default here: each
result is a title, a link and a description, at Jina's fixed minimum per
search. ``deep`` drops it, so Jina reads every result's page, billed by the
tokens it returns. There is no size between the two, so ``fast`` raises.
``gl``, ``hl``, ``site`` and the rest are reachable through
``provider_options``, sent as query parameters, but not mapped, so the shared
filters raise.
"""

JINA_SEARCH_NAMES: Final = {"max_results": "num"}

JINA_RESPOND_WITH_HEADER: Final = "X-Respond-With"
JINA_NO_CONTENT: Final = "no-content"

JINA_DEPTH_HEADERS: Final[dict[SearchDepth, dict[str, str]]] = {
    "lite": {JINA_RESPOND_WITH_HEADER: JINA_NO_CONTENT},
    "deep": {},
}
"""The headers each ``search_depth`` sends. ``lite``, also the default, reads no page, so a plain search costs Jina's
fixed minimum; ``deep`` lets Jina read each result's page."""

JINA_DEFAULT_DEPTH: Final[SearchDepth] = "lite"

MSG_JINA_NO_RESULTS: Final = "Jina answered without a data list (fields: {fields})"
"""A 200 body with no ``data`` list is a reply this mapping cannot read, not a search that found nothing."""
