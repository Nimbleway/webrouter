"""Apify's API: the paths web-router's live data provider calls, from https://docs.apify.com/api/v2."""

from __future__ import annotations

import re
from typing import Final

from web_router.types import ExtractFormat

APIFY_BASE_URL: Final = "https://api.apify.com"

APIFY_STORE_PATH: Final = "/v2/store"
"""The public Store: ``limit`` (default and max 1000) and ``offset``; items under ``data.items``."""

APIFY_ACTOR_PATH: Final = "/v2/actors/{actor}"
"""One Actor, by id or ``username~name``. Carries ``exampleRunInput`` but not the input schema."""

APIFY_DEFAULT_BUILD_PATH: Final = "/v2/actors/{actor}/builds/default"
"""The Actor's default build, where the input schema is: ``actorDefinition.input``, or the ``inputSchema`` string."""

APIFY_RUN_PATH: Final = "/v2/actors/{actor}/run-sync-get-dataset-items"
"""Run and wait, up to 300 s; answers 201 with the dataset items as a JSON array, or 408 past the limit."""

APIFY_ITEMS_FORMAT: Final = "json"

APIFY_ACTOR_NAME: Final = re.compile(r"[A-Za-z0-9._-]+(~[A-Za-z0-9._-]+)?")
"""An Actor id or ``username~name``: anything else (``?``, ``#``, a space) would change the request's path."""

# -- Search and extract, over Apify's own Server Actors -----------------------------------------------------------
# Not run-sync: each of these Server Actors keeps a multi-tenant server warm at <owner>--<name>.apify.actor, so a
# call is one HTTP request with the same APIFY_TOKEN as a Bearer header, and no Actor run starts per call.
# Read from each Actor's server interface OpenAPI on 2026-11-23 and checked live the same day.

APIFY_WEB_SEARCH_BASE_URL: Final = "https://apify--web-search.apify.actor"
"""apify/web-search: one search engine results page, organic results only, as JSON."""

APIFY_WEB_SEARCH_PATH: Final = "/"
"""``POST /`` with a JSON body; ``GET /`` takes the same fields in the query string, arrays comma-joined."""

APIFY_WEB_SEARCH_PARAM_NAMES: Final = {
    "max_results": "maxResults",
    "include_domains": "allowedDomains",
    "exclude_domains": "blockedDomains",
}
"""Normalized parameter -> Web Search field, renamed only. ``country``, ``locale`` and ``recency`` are translated."""

APIFY_WEB_SEARCH_PARAMETERS: Final = frozenset(
    {*APIFY_WEB_SEARCH_PARAM_NAMES, "country", "locale", "recency", "search_depth"}
)
"""``effort`` stays unmapped: Web Search has one tier."""

APIFY_WEB_SEARCH_MAX_DOMAINS: Final = 10
"""``allowedDomains`` and ``blockedDomains`` take at most 10 each; a longer list is a 400, so it is refused here."""

APIFY_WEB_FETCH_BASE_URL: Final = "https://apify--web-fetch.apify.actor"
"""apify/web-fetch: one URL, through Apify's unblocker, converted to the formats asked for."""

APIFY_WEB_FETCH_PATH: Final = "/"

APIFY_WEB_FETCH_FORMATS: Final[dict[ExtractFormat, str]] = {"markdown": "markdown", "html": "html", "text": "text"}
"""Normalized format -> the ``formats`` value, one for one; the body comes back under the same key."""

APIFY_WEB_FETCH_DEFAULT_FORMAT: Final[ExtractFormat] = "markdown"
"""What Web Fetch returns for an HTML or PDF page when ``formats`` is omitted."""

MSG_APIFY_NO_RESULTS: Final = "Apify Web Search answered without a results list (fields: {fields})"
"""A 200 body with no list is a reply this mapping cannot read, not a search that found nothing."""

MSG_APIFY_NO_CONTENT: Final = "Apify Web Fetch returned no content in the requested format"

MSG_APIFY_TOO_MANY_DOMAINS: Final = (
    "Apify Web Search takes at most {limit} domains per list; got {count} in {field}. Shorten the list, or name "
    "another provider -- see https://docs.webrouter.net/providers#search."
)
