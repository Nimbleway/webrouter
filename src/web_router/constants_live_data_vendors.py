"""Bright Data's and Oxylabs' live data APIs: the paths, statuses and catalogue web-router uses.

From https://docs.brightdata.com (Web Scraper API), read on 2026-11-09, and
https://developers.oxylabs.io (Web API: endpoint discovery, dedicated scrapers,
delivery modes, troubleshooting), read on 2026-11-10.
"""

from __future__ import annotations

from typing import Final

# -- Bright Data ------------------------------------------------------------

BRIGHTDATA_SCRAPE_PATH: Final = "/datasets/v3/scrape"
"""A synchronous scrape: ``dataset_id`` in the query, ``{"input": [...]}`` in the body, records back.

Waits about a minute; a scrape that runs longer answers 202 with a
``snapshot_id``, whose progress is read from ``/datasets/v3/progress/{id}`` and
whose records, once it is ready, from ``/datasets/v3/snapshot/{id}``.
"""

BRIGHTDATA_PROGRESS_PATH: Final = "/datasets/v3/progress"
BRIGHTDATA_SNAPSHOT_PATH: Final = "/datasets/v3/snapshot"
BRIGHTDATA_SNAPSHOT_READY: Final = "ready"
BRIGHTDATA_SNAPSHOT_FAILED: Final = "failed"
BRIGHTDATA_SNAPSHOT_CANCELED: Final = "canceled"

BRIGHTDATA_SNAPSHOT_POLL_SECONDS: Final = 5.0
BRIGHTDATA_SNAPSHOT_WAIT_SECONDS: Final = 300.0
"""How often, and how long, a scrape that outlasted the synchronous window is waited for, within the call's deadline."""

BRIGHTDATA_SCRAPERS_PATH: Final = "/datasets/v3/scrapers"
"""Every ready-made scraper, as one unpaged array; ``?dataset_id=`` narrows it to one."""

BRIGHTDATA_PENDING_STATUS: Final = 202

BRIGHTDATA_DEFAULT_METHOD: Final = "collect_by_url"
BRIGHTDATA_DISCOVER_PREFIX: Final = "discover_by_"
BRIGHTDATA_DISCOVER_TYPE: Final = "discover_new"
BRIGHTDATA_ID_PREFIX: Final = "gd_"
"""The collection method a scraper is described by when it has one: inputs keyed by the page's URL."""

MSG_BRIGHTDATA_PENDING: Final = (
    "Bright Data is still scraping {source!r} after {seconds:.0f} s; nothing is lost. Fetch the records "
    "later from snapshot {job_id!r}: GET /datasets/v3/snapshot/{job_id}?format=json"
)

MSG_BRIGHTDATA_NOT_READY: Final = "Bright Data has no records for {source!r} yet: the scrape is still running"

MSG_BRIGHTDATA_NO_SNAPSHOT: Final = (
    "Bright Data kept scraping {source!r} past its synchronous window but returned no snapshot id to read it by"
)

MSG_BRIGHTDATA_SNAPSHOT_FAILED: Final = "Bright Data's snapshot {job_id} for {source!r} failed: {reason}"
MSG_BRIGHTDATA_NO_REASON: Final = "Bright Data gave no reason"

MSG_BRIGHTDATA_SNAPSHOT_CANCELED: Final = (
    "Bright Data's snapshot {job_id} for {source!r} was canceled; it has no records"
)

MSG_BRIGHTDATA_POLL_FAILED: Final = (
    "Bright Data's snapshot {job_id} for {source!r} was started, but reading it failed: {error}. "
    "It may still finish: GET /datasets/v3/progress/{job_id}"
)

MSG_BRIGHTDATA_ALL_FAILED: Final = "every input failed on {source!r}: {errors}"

# -- Oxylabs ----------------------------------------------------------------

ENV_OXYLABS_WEB_API_KEY: Final = "OXYLABS_WEB_API_KEY"
"""A Web API key, made on a Web API instance in the dashboard: not the Realtime username and password extract uses."""

OXYLABS_WEB_API_BASE_URL: Final = "https://webapi.oxylabs.io"
OXYLABS_API_KEY_PREFIX: Final = "Bearer "

OXYLABS_SCRAPERS_PATH: Final = "/v1/scrapers"
"""Every scrape endpoint the key can call, realtime and async, as paths; ``OPTIONS`` on one gives its parameters."""

OXYLABS_SCRAPE_PREFIX: Final = "/v1/scrape"
OXYLABS_ASYNC_PREFIX: Final = "/v1/async/scrape"
OXYLABS_BATCH_PREFIX: Final = "/v1/async/scrape/batch"
"""Realtime, async-job and batch paths. A source is the part after the prefix: ``amazon/product``."""

OXYLABS_SYNC: Final = "sync"
OXYLABS_ASYNC: Final = "async"
OXYLABS_BATCH: Final = "batch"

OXYLABS_OUTPUT: Final = ["json"]
"""What a run asks for: a dedicated scraper's fields, under ``results[].json``."""

OXYLABS_TRANSPORT_PARAMS: Final = frozenset({"output", "callback_url", "storage", "client_notes"})
"""Parameters that choose how a result is delivered, not what is scraped: set here, so left out of a schema."""

OXYLABS_JOB_POLL_SECONDS: Final = 5.0
OXYLABS_JOB_WAIT_SECONDS: Final = 300.0
"""How often, and how long, a run of an async-only scraper (``chatgpt``, ``gemini``...) waits for its job."""

OXYLABS_JOB_DEADLINE_MARGIN: Final = 0.5
"""Seconds a job's wait stops before the call's own deadline, so the job's error, naming it, is what is raised."""

OXYLABS_JOB_DONE: Final = "done"
OXYLABS_JOB_FAULTED: Final = "faulted"

OXYLABS_FAILED_FETCH_FLOOR: Final = 400
"""The first per-result ``metadata.status_code`` that means the page was not fetched."""

OXYLABS_DOCS_URL: Final = "https://developers.oxylabs.io/products/web-api/scrape/dedicated-scrapers"

MSG_OXYLABS_NO_FIELDS: Final = "Oxylabs returned no parsed fields for {source!r} ({url})"

MSG_BRIGHTDATA_RENAMED_HINT: Final = (
    "; Bright Data may have added a scraper with the same name, which gives each its site: {names}."
    " A gd_ dataset id never changes"
)
"""Appended to a Bright Data not-found: a name gains its site when another scraper takes the same name."""

MSG_OXYLABS_NOT_FETCHED: Final = "Oxylabs could not fetch {source!r}: status {status} ({url})"

MSG_OXYLABS_FAULTED: Final = "Oxylabs' job {job_id} for {source!r} faulted: status {status}"

MSG_OXYLABS_NO_JOB_ID: Final = "Oxylabs accepted the job for {source!r} but returned no job id to read it by"

MSG_OXYLABS_POLL_FAILED: Final = (
    "Oxylabs' job {job_id} for {source!r} was submitted, but reading it failed: {error}. "
    "It may still finish: GET /v1/async/scrape/{job_id}"
)
"""A poll that failed after the submit: the job is billed, so its id is named, never "no such source"."""

MSG_OXYLABS_JOB_RESULT_FAILED: Final = (
    "Oxylabs' job {job_id} for {source!r} finished, but its result could not be used: {error}. "
    "Read it: GET /v1/async/scrape/{job_id}"
)
"""A finished job whose answer raised: the job is billed, so its id is named here too."""

MSG_OXYLABS_STILL_RUNNING: Final = (
    "Oxylabs is still running {source!r} after {seconds:.0f} s; nothing is lost. Fetch it later: "
    "GET /v1/async/scrape/{job_id}"
)
