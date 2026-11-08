"""Exa search, over REST and over its keyless MCP server, and answer: the options' fields and the answer path.

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

# --- Exa MCP, a keyless route -----------------------------------------------
# Exa's hosted MCP server, documented as free and rate-limited without a key
# (exa.ai/docs/reference/exa-mcp). A lone tools/call needs no initialize
# handshake (verified live 2026-10-01). The server lists only web_search_exa,
# which takes a query, numResults and an objective, unless `?tools=` on its URL
# names others; web_search_advanced_exa, named there, takes the filters, the
# content sizes and the search type. Checked keyless on 2026-11-09 with
# tools/list and five searches: it answers in Exa's REST JSON (`results` of
# `{url, title, text | highlights, publishedDate, author}` and `requestId`),
# not the text blocks web_search_exa writes, and refuses any argument its
# schema does not declare -- `objective` among them.

EXA_MCP_BASE_URL = "https://mcp.exa.ai"
EXA_MCP_SEARCH_PATH = "/mcp"
EXA_MCP_SEARCH_TOOL = "web_search_advanced_exa"
EXA_MCP_TOOLS_PARAM = "tools"
"""The query parameter on the server URL that makes it serve :data:`EXA_MCP_SEARCH_TOOL`."""

EXA_MCP_PARAM_NAMES = {
    "max_results": "numResults",
    "include_domains": "includeDomains",
    "exclude_domains": "excludeDomains",
    "country": "userLocation",
}
"""Normalized parameter -> the tool's argument, sent as the caller set it."""

EXA_MCP_DATE_FORMAT = "%Y-%m-%d"
"""The tool's ``startPublishedDate`` is a calendar date, YYYY-MM-DD, unlike REST's date-time."""

EXA_MCP_CONTENTS: dict[str, Any] = {"enableHighlights": True}
"""Sent when ``search_depth`` is unset: the query's passages, as web_search_exa returned.

Unset, the advanced tool returns each page's whole text, which is ``deep``'s
size and was slower live (1.9 s against 0.2-0.3 s for highlights or a capped
text), so a call naming no depth keeps the output it had before the switch.
"""

EXA_MCP_CONTENTS_BY_DEPTH: dict[SearchDepth, dict[str, Any]] = {
    "lite": {"enableHighlights": True, "highlightsMaxCharacters": 500},
    "fast": {"textMaxCharacters": 3000},
    "deep": {},
}
"""The tool's content arguments for each ``search_depth``.

``deep`` sends none: "page text is the full page by default", and live it came
back at 10,000-14,000 characters a result. Highlights without a cap ran to about
4,000 characters, so ``lite`` caps them at a snippet's size.
"""

EXA_MCP_TYPE_BY_EFFORT: dict[ResearchEffort, str | None] = {
    "low": "lite",
    "medium": None,
    "high": None,
    "max": None,
}
"""``type`` for each ``effort``. The tool's top type is ``auto``, its default, so medium and up send nothing.

It has no deep types; ``fast`` and ``instant`` are legacy names for ``lite``.
"""
