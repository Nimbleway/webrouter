"""You.com search, research and answer: their endpoints, mappings, status table and answer fields.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. The env var and the provider's
place in ``auto`` stay there, beside every other backend's.
"""

from __future__ import annotations

import re

from web_router.types import ResearchEffort, ResearchState
from web_router.types_answer import Recency

# Verified live on 2026-10-16. Research lives on api.you.com, not on the
# ydc-index.io host search uses: there the same path answers 403. POST with
# `background` returns a task `queued` under `task_id`; GET reports it under
# `id` through `running` to `completed`, with the answer in `result.output`.
# No cancel is offered: POST .../cancel is a 404 and DELETE a 405. The
# `failed` and `cancelled` statuses, and a failed task's `error`, are from
# documentation: no live run reached either.

YOU_RESEARCH_BASE_URL = "https://api.you.com"
YOU_RESEARCH_PATH = "/v1/research"
YOU_RESEARCH_STATUS_PATH = "/v1/research/{job_id}"

YOU_RESEARCH_BACKGROUND = True
"""Always sent: without it the request blocks for the whole run, which a research job must not."""

YOU_RESEARCH_EFFORTS: dict[ResearchEffort, str] = {
    # `research_effort` runs lite, standard, deep and exhaustive against the
    # four normalized levels one for one, and defaults to standard, so an unset
    # effort sends nothing. `frontier` sits above exhaustive at well over twice
    # its price and runs for up to hours, so `max` stops at exhaustive; a caller
    # who wants frontier asks for it through `provider_options`.
    "low": "lite",
    "medium": "standard",
    "high": "deep",
    "max": "exhaustive",
}

YOU_RESEARCH_STATES: dict[str, ResearchState] = {
    "queued": "queued",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}

# Verified live on 2026-11-01. An answer is POST /v1/answer on api.you.com,
# the research host, answering in about two seconds with `answer` (markdown,
# citing as `[[1, 2]]`), `citations` -- only the sources the answer cites, in
# the order the markers count them from 1, each `{source, excerpts}` -- and
# `results.web`, every page considered, which carries the cited pages' titles.
# No request id. A query over 400 characters is a 422 naming the limit.

YOU_ANSWER_PATH = "/v1/answer"

YOU_ANSWER_PARAM_NAMES: dict[str, str] = {
    "include_domains": "include_domains",
    "exclude_domains": "exclude_domains",
    "country": "country",
}
"""Normalized parameter -> You.com field. Each was seen to apply live: an emergency-number question answered 999 for
GB and 911 for US.

``locale`` is left unmapped: ``language`` was accepted, but ``FR`` changed neither
the answer nor its sources, so nothing showed it doing what ``locale`` means.
"""

MSG_YOU_ANSWER_DOMAINS_EXCLUSIVE = (
    "You.com answers with one domain list, not both: send include_domains or exclude_domains."
)
"""Both domain lists answered 422 ``mutually_exclusive`` when sent together, so the pair is refused before the call."""

YOU_FRESHNESS_FIELD = "freshness"

YOU_RECENCY: dict[Recency, str] = {
    "day": "day",
    "week": "week",
    "month": "month",
    "year": "year",
}
"""You.com's own ``freshness`` values, used as is on search and the MCP tool; answer refuses ``day``
(``YOU_ANSWER_REFUSED_RECENCY``). Live, on answer, ``week`` kept every result to the last two weeks' pages."""

YOU_ANSWER_REFUSED_RECENCY: frozenset[Recency] = frozenset({"day"})
"""Recency the answer refuses: live (2026-11-09 to 11-10), ``freshness=day`` cited pages two to five days old."""

MSG_YOU_ANSWER_RECENCY = (
    "You.com answers with recency 'day' citing pages several days old, so it is refused: ask for 'week' or wider, "
    "or name another provider -- see the answer table at https://docs.webrouter.net/providers#answer."
)

YOU_ANSWER_MARKER = re.compile(r"\[\[(\d+(?:,\s*\d+)*)\]\]")
"""A citation marker in the answer: ``[[1, 2]]``, numbering ``citations`` from 1."""

# Search (you.com/docs/api-reference/search/v1-search). `country` and
# `language` take fixed lists and answer 422 for anything else; the MCP tool's
# inline `loc:` and `lang:` accept any value and drop an unknown one silently,
# so both are held to the lists before the call.

YOU_SEARCH_PARAM_NAMES: dict[str, str] = {
    "max_results": "count",
    "include_domains": "include_domains",
    "exclude_domains": "exclude_domains",
}
"""Normalized parameter -> You.com field. ``search_depth`` maps to ``extraction``, ``country`` and ``locale`` onto
the fixed lists below, ``recency`` onto ``freshness``. The two domain lists cannot be sent together."""

YOU_SEARCH_PARAMETERS = frozenset({*YOU_SEARCH_PARAM_NAMES, "search_depth", "country", "locale", "recency"})

YOU_COUNTRIES = frozenset(
    {
        "AR", "AU", "AT", "BE", "BR", "CA", "CL", "DK", "FI", "FR", "DE", "HK", "IN", "ID", "IT", "JP", "KR", "MY",
        "MX", "NL", "NZ", "NO", "CN", "PL", "PT", "PH", "RU", "SA", "ZA", "ES", "SE", "CH", "TW", "TR", "GB", "US",
    }
)  # fmt: skip
"""The 36 ISO codes You.com's ``country`` takes; another is refused before the call."""

MSG_YOU_SEARCH_VALUES = (
    "You.com searches only the countries and languages it lists: country at "
    + ", ".join(sorted(YOU_COUNTRIES))
    + ", and locale at a language "
    "it lists (see its language list; pt and zh alone are refused, as each could be either of two). Ask for one of "
    "those, leave it unset, or name another provider -- see the search table at https://docs.webrouter.net/providers#search."
)

MSG_YOU_SEARCH_DOMAINS_EXCLUSIVE = (
    "You.com searches with one domain list, not both: send include_domains or exclude_domains."
)

# The MCP tool `you-search` (tools/list on api.you.com/mcp?profile=free, and
# keyless calls on 2026-11-09): it has no country, language or include_domains
# argument; it reads them from `loc:`, `lang:` and `site:` in the query, strips
# them (metadata.query came back without them), and several `site:` keep each
# site. An inline site: cannot be combined with its exclude_domains argument.

YOU_MCP_PARAM_NAMES: dict[str, str] = {"max_results": "count", "exclude_domains": "exclude_domains"}
"""Normalized parameter -> argument of ``you-search``; the other filters go into the query, below."""

YOU_MCP_LANGUAGE = "lang:{language}"
YOU_MCP_COUNTRY = "loc:{country}"
"""The inline operators ``you-search`` maps itself, appended to the query; values are You.com's list values."""
