"""Bright Data's and Oxylabs' live data APIs: the paths, statuses and catalogue web-router uses.

From https://docs.brightdata.com (Web Scraper API) and
https://developers.oxylabs.io (Web Scraper API, Realtime), read on 2026-11-09.
"""

from __future__ import annotations

from typing import Final

# -- Bright Data ------------------------------------------------------------

BRIGHTDATA_SCRAPE_PATH: Final = "/datasets/v3/scrape"
"""A synchronous scrape: ``dataset_id`` in the query, ``{"input": [...]}`` in the body, records back.

Waits about a minute; a scrape that runs longer answers 202 with a
``snapshot_id`` to fetch later from ``/datasets/v3/snapshot/{id}``.
"""

BRIGHTDATA_SCRAPERS_PATH: Final = "/datasets/v3/scrapers"
"""Every ready-made scraper, as one unpaged array; ``?dataset_id=`` narrows it to one."""

BRIGHTDATA_PENDING_STATUS: Final = 202

BRIGHTDATA_DEFAULT_METHOD: Final = "collect_by_url"
BRIGHTDATA_DISCOVER_PREFIX: Final = "discover_by_"
BRIGHTDATA_DISCOVER_TYPE: Final = "discover_new"
BRIGHTDATA_ID_PREFIX: Final = "gd_"
"""The collection method a scraper is described by when it has one: inputs keyed by the page's URL."""

MSG_BRIGHTDATA_PENDING: Final = (
    "Bright Data is still scraping {source!r} past its synchronous window; nothing is lost. Fetch the records "
    "later from snapshot {snapshot_id!r}: GET /datasets/v3/snapshot/{snapshot_id}?format=json"
)

MSG_BRIGHTDATA_ALL_FAILED: Final = "every input failed on {source!r}: {errors}"

# -- Oxylabs ----------------------------------------------------------------

OXYLABS_DOCS_URL: Final = (
    "https://developers.oxylabs.io/scraping-solutions/web-scraper-api/features/result-processing-and-storage/"
    "dedicated-parsers"
)

OXYLABS_SOURCES: Final = (
    "amazon",
    "amazon_search",
    "amazon_product",
    "amazon_pricing",
    "amazon_bestsellers",
    "amazon_sellers",
    "google",
    "google_search",
    "google_ai_mode",
    "google_ads",
    "google_lens",
    "google_scholar",
    "google_shopping_search",
    "google_shopping_product",
    "google_travel_hotels",
    "youtube_metadata",
    "youtube_channel",
    "chatgpt",
    "perplexity",
    "gemini",
    "bing",
    "bing_search",
    "walmart",
    "walmart_search",
    "walmart_product",
    "bestbuy_product",
    "etsy",
    "etsy_product",
    "target",
    "target_search",
    "target_product",
)
"""Every source Oxylabs documents a dedicated parser for, so ``parse: true`` answers with fields.

Oxylabs' Realtime API has no endpoint that lists them; this is the list on
:data:`OXYLABS_DOCS_URL`, read on 2026-11-09. A source not here can still be
run by name -- Oxylabs refuses one it does not have.
"""

OXYLABS_PARSE_OK: Final = frozenset({12000, 12004, 12005})
"""``parse_status_code`` values that are a record: a full parse, or a partial one (fields missing or defaulted)."""

OXYLABS_FAILED_FETCH_FLOOR: Final = 400
"""The first per-result ``status_code`` that means the page was not fetched."""

MSG_OXYLABS_NOT_PARSED: Final = "Oxylabs could not parse {source!r}: parse_status_code {code} ({url})"

MSG_BRIGHTDATA_RENAMED_HINT: Final = (
    "; Bright Data may have added a scraper with the same name, which gives each its site: {names}."
    " A gd_ dataset id never changes"
)
"""Appended to a Bright Data not-found: a name gains its site when another scraper takes the same name."""

MSG_OXYLABS_NO_RESULTS: Final = "Oxylabs returned no result for {source!r}: no page was fetched or parsed"

MSG_OXYLABS_NOT_FETCHED: Final = "Oxylabs could not fetch {source!r}: status {status} ({url})"
