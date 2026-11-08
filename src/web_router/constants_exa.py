"""Exa search and answer: the search options' fields, the answer path and the fields of its response.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Exa's host and auth header stay
there, beside its contents settings.
"""

from __future__ import annotations

from typing import Any

from web_router.types_options import ResearchEffort, SearchDepth

# The Search API's options, per exa.ai/docs/reference/search (checked
# 2026-11-09; not yet run live with these fields). `includeDomains` and
# `excludeDomains` take up to 1,200 entries each, `userLocation` a two-letter ISO
# country code, `startPublishedDate` an ISO 8601 date-time. `type=lite` takes
# every one of them, and `contents` too.

EXA_PARAM_NAMES = {
    "max_results": "numResults",
    "include_domains": "includeDomains",
    "exclude_domains": "excludeDomains",
    "country": "userLocation",
}
"""Normalized parameter -> Exa field, sent as the caller set it."""

EXA_RECENCY_FIELD = "startPublishedDate"
EXA_DATE_FORMAT = "%Y-%m-%dT00:00:00.000Z"
"""``startPublishedDate`` is a date-time; a window starts at midnight UTC of its first day."""

EXA_CONTENTS = {"text": True}
"""What ``contents`` asks for when ``search_depth`` is unset: the page text, as before depth was mapped.

Exa returns links only unless contents are requested. Not an enrichment:
without it ``SearchResult.content`` would always be empty, which every other
provider fills.
"""

EXA_CONTENTS_BY_DEPTH: dict[SearchDepth, dict[str, Any]] = {
    "lite": {"highlights": {"maxCharacters": 500}},
    "fast": {"text": {"maxCharacters": 3000}},
    "deep": {"text": True},
}
"""``contents`` for each ``search_depth``, by the sizes the levels promise.

Highlights are the passages Exa picks for the query, capped at a snippet's
size; ``fast`` caps the page text; ``text: true`` is, in Exa's words, "full page
text with default settings".
"""

EXA_TYPE_FIELD = "type"
EXA_TYPE_BY_EFFORT: dict[ResearchEffort, str | None] = {
    "low": "lite",
    "medium": None,
    "high": "deep-lite",
    "max": "deep",
}
"""``type`` for each ``effort``; medium sends nothing, since ``auto`` is Exa's default.

The deep types also write a synthesized output, which stays in ``raw``, and
cost more per search than ``auto``.
"""

# Verified live on 2026-11-01. POST /answer with the query alone answered in
# about 1.4 seconds with `answer`, citing inline as `[1][2][3]` -- numbering
# `citations` from 1 -- `citations` of `{id, url, title, author,
# publishedDate, image}`, `requestId` and `costDollars` ($0.005 a call). `text`
# comes back only when `text: true` is sent, which is not sent here. `answer`
# is a string unless `outputSchema` is sent, which is not mapped.
# `userLocation` was accepted but showed no clear effect: for GB the answer
# never named 999, so `country` is not mapped to it.

EXA_ANSWER_PATH = "/answer"

EXA_ANSWER_CITATION_TEXT_FIELD = "text"
"""The cited page's text, present only when asked for; read as the snippet when it is there."""
