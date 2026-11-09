"""Constants for live data: its messages, its MCP tools, and the names it had before the rename.

Kept out of :mod:`web_router.constants`, which is at its size limit; that
module imports the MCP entries back for its tool tables. Nothing here imports
:mod:`web_router.constants`, so the two cannot import each other in a loop.
"""

from __future__ import annotations

from typing import Any, Final, get_args

from web_router.types import ExtractFormat, Provider

# -- messages ---------------------------------------------------------------

MSG_LIVE_DATA_FAILED: Final = "live data run failed: {error}"

MSG_LIVE_DATA_SOURCES_FAILED: Final = "listing live data sources failed: {error}"

MSG_LIVE_DATA_SOURCE_GET_FAILED: Final = "fetching live data source {name!r} failed: {error}"

MSG_LIVE_DATA_SOURCE_NOT_FOUND: Final = "the backend has no live data source named {name!r}"
"""An unknown source name, as a backend reports it -- Nimble with a 404 on describe and on run."""

MSG_LIVE_DATA_BAD_STATUS: Final = "the backend fetched nothing for source {source!r} and reported status {status!r}"

MSG_LIVE_DATA_PARSING_FAILED: Final = "the backend fetched the page but source {source!r} failed to parse it: {error}"

MSG_LIVE_DATA_PAGE_FAILED: Final = (
    "the site answered source {source!r} with HTTP {status}, so there is no page to parse"
)
"""A fetch the site refused: the envelope says ``success`` and the page body is an error page.

Verified on a recorded response: a Nimble run on a page that does not exist
comes back ``status: success`` with ``status_code: 404`` and a "record" whose
title is the error page's. A record parsed from an error page is not the
record the source promises, so the run is a failure.
"""

MSG_LIVE_DATA_NO_RECORD: Final = "the backend fetched the page but answered without a record for source {source!r}"
"""Stands in when a fetched page carries no parsed record and no reason."""

MSG_LIVE_DATA_BAD_PAGE: Final = (
    "a page needs a limit and an offset of 0 or more, not limit={limit!r}, offset={offset!r}"
)

MSG_LIVE_DATA_SEARCH_CAPPED: Final = (
    "searching {provider}'s live data sources for {query!r} stopped after {read} sources; "
    "the matches come from those alone, so `total` is unset"
)

MSG_LIVE_DATA_DEPRECATED_FIELD: Final = "`{old}` is deprecated and will be removed: use `{new}`."

MSG_LIVE_DATA_DEPRECATED_NAME: Final = (
    "`{old}` is deprecated and will be removed: use `{new}`. Templates are now live data sources."
)

# -- MCP --------------------------------------------------------------------

MCP_TOOL_LIVE_DATA_SOURCES: Final = "live_data_sources"
MCP_TOOL_LIVE_DATA_SOURCE: Final = "live_data_source"
MCP_TOOL_LIVE_DATA: Final = "live_data"
MCP_METHOD_LIVE_DATA: Final = "live_data"
"""The key the capabilities resource lists live data providers under; the three tools share one registry."""

MCP_LIVE_DATA_SOURCES_PROPERTIES: dict[str, dict[str, Any]] = {
    "query": {
        "type": "string",
        "description": (
            "Keywords to find a source by -- a site, a brand, the kind of page ('amazon product', 'linkedin "
            "jobs'). Searched by the vendor's own search where it has one, else matched here on name, site "
            "and description. Unset lists the catalogue in order."
        ),
    },
    "limit": {"type": "integer", "minimum": 1, "description": "How many sources to list. Unset, the backend's page."},
    "offset": {"type": "integer", "minimum": 0, "description": "How many to skip first, for the next page."},
}

MCP_LIVE_DATA_SOURCE_PROPERTIES: dict[str, dict[str, Any]] = {
    "name": {"type": "string", "description": "The source's name, as `live_data_sources` lists it."},
}

MCP_LIVE_DATA_PROPERTIES: dict[str, dict[str, Any]] = {
    "source": {"type": "string", "description": "The source's name, as `live_data_sources` lists it."},
    "inputs": {
        "type": "object",
        "description": (
            "The source's inputs, named by its input_schema from `live_data_source` -- a URL for some, an id, "
            "a slug or a search term for others. Omit it only for a source whose schema names nothing."
        ),
    },
    "format": {
        "type": "string",
        "enum": list(get_args(ExtractFormat)),
        "description": (
            "Also return the page the records came from, in this representation. Unset returns the records "
            "alone, which is usually all you need."
        ),
    },
}

MCP_DESC_LIVE_DATA_SOURCES = (
    "List a vendor's live data sources: ready-made scrapers for one site each, turning one kind of "
    "page -- a retailer's product page, a job listing, a search results page -- into records with "
    "fixed fields, fetched fresh on every run. Returns in about a second, with each source's name, "
    "site, what it extracts, the names of the inputs a run must pass (`required_inputs`) and one input "
    "the vendor recorded a run with (`sample_inputs`). Call it before `live_data` to find a source for "
    "the site at hand; a catalogue can be long, so pass `query` with the site or the kind of page, and "
    "page with `limit` and `offset`. The answer's `search` says whether the vendor searched ('native') "
    "or the catalogue was matched here ('local'). Then "
    "`live_data_source` for the full schemas and samples."
)

MCP_DESC_LIVE_DATA_SOURCE = (
    "Describe one live data source: its input_schema, which says exactly what `live_data` must pass "
    "as `inputs`, its output_schema, which says what each record will hold, and its `samples`, runs "
    "the vendor recorded -- an input and what it returned -- to copy a first run from. Returns in "
    "about a second and bills nothing. Read it before running a source you have not run in this "
    "conversation. Names, inputs and fields are the vendor's own: the same site differs between vendors."
)

MCP_DESC_LIVE_DATA = (
    "Run a live data source and get structured records back -- the site's fields, scraped for this "
    "call by a scraper built for that site, rather than prose to read through. One record for a "
    "product page, many for a search or a listing. Takes seconds, up to a few minutes on some "
    "vendors, and bills one run on the vendor's account at the vendor's own rate. `provider` is "
    "required, since a source's name belongs to one vendor. Use it when `live_data_sources` lists a "
    "source for the site and item you have; use `extract` for a page no source covers, and `search` "
    "to find pages. `inputs` are the source's own, from `live_data_source`. A run the backend could "
    "not complete is an error, never an empty answer."
)

# -- search -----------------------------------------------------------------

LIVE_DATA_SEARCH_PAGE: Final = 100
"""Sources asked for per page while reading a catalogue to search it here; Nimble's own cap on a page."""

LIVE_DATA_SEARCH_MAX_SOURCES: Final = 5000
"""Sources read at most to search one catalogue here, so a vendor that pages forever cannot hold a call."""

LIVE_DATA_SEARCH_NATIVE: Final = "native"
LIVE_DATA_SEARCH_LOCAL: Final = "local"

# -- HTTP backends ----------------------------------------------------------

LIVE_DATA_HTTP_TIMEOUT: Final = 330.0
"""Seconds per live data request over HTTP: Apify's synchronous run waits up to 300 s, plus room to answer."""

# -- backends ---------------------------------------------------------------

ENV_APIFY_TOKEN: Final = "APIFY_TOKEN"
"""Apify's API token, under the name Apify's own SDKs and CLI read."""

BUILTIN_LIVE_DATA_PROVIDERS: dict[str, tuple[str, str, str]] = {
    Provider.NIMBLE.value: ("web_router.providers.nimble_live_data", "NimbleLiveDataProvider", "extract"),
    Provider.APIFY.value: ("web_router.providers.apify", "ApifyLiveDataProvider", "extract"),
    Provider.BRIGHTDATA.value: ("web_router.providers.brightdata_live_data", "BrightDataLiveDataProvider", "extract"),
    Provider.OXYLABS.value: ("web_router.providers.oxylabs_live_data", "OxylabsLiveDataProvider", "extract"),
}
"""Each built-in live data backend's module, class, and the extra that installs it."""

LIVE_DATA_ENV_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    Provider.NIMBLE.value: ("NIMBLE_API_KEY",),
    Provider.APIFY.value: (ENV_APIFY_TOKEN,),
    Provider.BRIGHTDATA.value: ("BRIGHTDATA_API_TOKEN",),
    Provider.OXYLABS.value: ("OXYLABS_USERNAME", "OXYLABS_PASSWORD"),
}
"""Which variables each live data provider needs, all of them.

Spelled out rather than imported, because :mod:`web_router.constants` imports
this module; ``tests/test_live_data_contract.py`` checks each matches the
variable the provider's other methods read. A live data run fetches and
parses on the backend's account, so there is no keyless tier, and no ``auto``:
a source's name belongs to one vendor. Bright Data's live data needs its token
alone -- the zone is its Web Unlocker's, which a scraper does not use.
"""
