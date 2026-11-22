"""Oxylabs' Web Scraper API as a live data backend: where its sources are listed, and what a run sends and reads.

The Web Scraper API is the one Oxylabs' extract already calls, with
``OXYLABS_USERNAME`` and ``OXYLABS_PASSWORD``; Oxylabs' docs now call it
"Classic", beside the newer Web API that ``OXYLABS_WEB_API_KEY`` reaches. Its
host and endpoint are extract's, in :mod:`web_router.constants`.
"""

from __future__ import annotations

from typing import Final

OXYLABS_CLASSIC_DATA_DIR: Final = "data"
OXYLABS_CLASSIC_DATA_FILE: Final = "oxylabs_classic_sources.json"
"""The sources Oxylabs parses, read from its docs by ``scripts/oxylabs_classic.py``: the API lists none itself."""

OXYLABS_CLASSIC_SOURCE: Final = "source"
OXYLABS_CLASSIC_PARSE: Final = "parse"
OXYLABS_CLASSIC_CONTEXT: Final = "context"
"""The array a source's ``context:`` parameters are sent in, each as ``{"key": ..., "value": ...}``."""
OXYLABS_CLASSIC_TRANSPORT_PARAMS: Final = frozenset(
    {OXYLABS_CLASSIC_SOURCE, OXYLABS_CLASSIC_PARSE, "callback_url", "storage_type", "storage_url"}
)
"""Set here or never sent: a run is one parsed, synchronous answer, not a webhook or a bucket upload."""

OXYLABS_CLASSIC_CONTENT: Final = "content"
"""Where a result carries its parsed page."""
OXYLABS_CLASSIC_API: Final = "web_scraper_api"
"""A listed source's ``extra["api"]``: which of Oxylabs' two APIs runs it."""

OXYLABS_PARSE_STATUS: Final = "parse_status_code"
OXYLABS_PARSE_NOTES: Final = ("_errors", "_warnings")
"""Where a page parsed in part lists what its parser could not read; kept out of the record."""
OXYLABS_PARSED: Final = frozenset({12000, 12004, 12005})
"""Parse statuses that carry fields, seen live: 12000 parsed in full; 12004 parsed in part, a broken field holding
an error string and ``_errors`` listing each; 12005 parsed with some fields missing, ``_warnings`` naming them.
12003, a source with no parser, carries none."""

OXYLABS_CLASSIC_SOURCE_DESCRIPTION: Final = "Oxylabs' {name} scraper (Web Scraper API), parsed: {docs}"

MSG_OXYLABS_NOT_PARSED: Final = "Oxylabs did not parse {source!r}: parse_status_code {code} ({url})"
"""A page fetched but not parsed: its fields are not records, so the run is a failure, not an empty answer."""

MSG_OXYLABS_NO_CREDENTIALS: Final = (
    "Oxylabs live data needs a credential: OXYLABS_USERNAME and OXYLABS_PASSWORD (the Web Scraper API, which"
    " extract uses too) or OXYLABS_WEB_API_KEY (the Web API), or the same in provider_config['oxylabs'] as"
    " 'username' and 'api_key', or 'web_api_key'."
)
"""Neither of Oxylabs' two credentials is set: either one serves live data."""

OXYLABS_WEB_API_FAILURE: Final = "oxylabs web_api"
"""The key a listing's ``failures`` names the Web API under when it could not answer and the Web Scraper API did."""

LOG_OXYLABS_WEB_API_LEFT_OUT: Final = "oxylabs: the Web API's sources are left out of the listing: %s"
"""Logged when the Web API's listing fails beside the Web Scraper API's, whose sources are still returned."""

MSG_OXYLABS_ONE_BASE_URL: Final = (
    "Oxylabs live data has both credentials and one base_url: the Web Scraper API and the Web API are on different"
    " hosts, so one override would send one API's credential to the other's host. Drop base_url, or keep one"
    " credential: 'username' and 'api_key', or 'web_api_key'."
)
"""A host override with both credentials set: each API has a host of its own, so one override cannot serve both."""
