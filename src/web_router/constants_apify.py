"""Apify's API: the paths web-router's live data provider calls, from https://docs.apify.com/api/v2."""

from __future__ import annotations

from typing import Final

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
