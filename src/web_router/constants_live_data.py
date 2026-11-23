"""Constants for live data: its messages, its MCP tools, and the names it had before the rename.

Kept out of :mod:`web_router.constants`, which is at its size limit; that
module imports the MCP entries back for its tool tables. Nothing here imports
:mod:`web_router.constants`, so the two cannot import each other in a loop.
"""

from __future__ import annotations

from typing import Any, Final, get_args

from web_router.constants_merchant import ENV_MERCHANT_AGENT_PROFILE_URL, PROVIDER_MERCHANT
from web_router.types import ExtractFormat, Provider

LIVE_DATA_JOB_DEADLINE_MARGIN: Final = 0.5
"""Seconds a wait on a vendor's job stops before the call's own deadline, so the job's error, naming it, is raised."""

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
        "description": "A site, brand or kind of page, e.g. 'amazon product'. Unset, the catalogue in order.",
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
        "description": "Named by the source's input_schema, from `live_data_source`.",
    },
    "format": {
        "type": "string",
        "enum": list(get_args(ExtractFormat)),
        "description": "Also return the source page in this form. Unset, the records alone.",
    },
}

MCP_DESC_LIVE_DATA_SOURCES = (
    "List live data sources: ready-made scrapers that turn one site's pages (a product, a job "
    "listing) into records with fixed fields. About a second. Pass `query` with the site or page kind; "
    "page with `limit` and `offset`. Leave `provider` out to search every configured vendor: each row "
    "names its `provider`, and `failures` names a vendor that could not answer. Each lists its "
    "`required_inputs` and `sample_inputs`; `live_data_source` has the full schemas."
)

MCP_DESC_LIVE_DATA_SOURCE = (
    "Describe one live data source: its input_schema (what `live_data` passes as `inputs`), output_schema "
    "and recorded sample runs. About a second; free. Read it before a source's first run here."
)

MCP_DESC_LIVE_DATA = (
    "Run a live data source and get structured records, scraped for this call. Seconds, up to minutes; "
    "bills one run at the vendor's rate. Pass the `provider` `live_data_sources` named (optional with one "
    "vendor configured); `inputs` come from `live_data_source`. For a page no source covers use "
    "`extract`. A failed run is an error, never an empty answer."
)

# -- search -----------------------------------------------------------------

LIVE_DATA_SEARCH_PAGE: Final = 100
"""Sources asked for per page while reading a catalogue to search it here; Nimble's own cap on a page."""

LIVE_DATA_SEARCH_MAX_SOURCES: Final = 5000
"""Sources read at most to search one catalogue here, so a vendor that pages forever cannot hold a call."""

LIVE_DATA_SEARCH_CONCURRENCY: Final = 8
"""Catalogue pages asked for at once by an async local search, once the first page has given the count."""

LIVE_DATA_SEARCH_CACHE_SECONDS: Final = 600.0
"""How long a catalogue read for a local search is reused by the next search of the same provider.

Only the listing: a run still fetches its site on every call.
"""

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
    Provider.OXYLABS.value: ("web_router.providers.oxylabs_live_data_any", "OxylabsLiveDataProvider", "extract"),
    PROVIDER_MERCHANT: ("web_router.providers.merchant_live_data", "MerchantLiveDataProvider", "extract"),
}
"""Each built-in live data backend's module, class, and the extra that installs it."""

LIVE_DATA_ENV_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    Provider.NIMBLE.value: ("NIMBLE_API_KEY",),
    Provider.APIFY.value: (ENV_APIFY_TOKEN,),
    Provider.BRIGHTDATA.value: ("BRIGHTDATA_API_TOKEN",),
    Provider.OXYLABS.value: ("OXYLABS_WEB_API_KEY",),
    PROVIDER_MERCHANT: (ENV_MERCHANT_AGENT_PROFILE_URL,),
}
"""Which variables each live data provider needs, all of them.

Spelled out rather than imported, because :mod:`web_router.constants` imports
this module; ``tests/test_live_data_contract.py`` checks each matches the
variable the provider's other methods read. A live data run fetches and
parses on the backend's account, so there is no keyless tier, and no ``auto`` fallback:
a source's name belongs to one vendor. Bright Data's live data needs its token
alone -- the zone is its Web Unlocker's, which a scraper does not use.
Oxylabs' entry is its Web API's key; its Web Scraper API's username and
password, the ones its extract uses, serve live data as well, and are in
:data:`LIVE_DATA_ALT_ENV_BY_PROVIDER`.
"""

LIVE_DATA_ALT_ENV_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    Provider.OXYLABS.value: ("OXYLABS_USERNAME", "OXYLABS_PASSWORD"),
}
"""A second set of variables that configures a live data provider on its own: Oxylabs' Web Scraper API's."""

LIVE_DATA_CREDENTIAL_SETS: dict[str, tuple[tuple[str, ...], ...]] = {
    name: (env, *((LIVE_DATA_ALT_ENV_BY_PROVIDER[name],) if name in LIVE_DATA_ALT_ENV_BY_PROVIDER else ()))
    for name, env in LIVE_DATA_ENV_BY_PROVIDER.items()
}
"""Each set of variables that alone configures a live data provider: one, or Oxylabs' two."""
