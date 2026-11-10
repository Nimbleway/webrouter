"""Linkup search, answer and research: the search effort table, the answer's output type, field names and
recency windows, and research's endpoints, depths, output types and states.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Linkup's host, auth header and
search path stay there, beside its search and extract settings.
"""

from __future__ import annotations

from web_router.types import ResearchState
from web_router.types_options import ResearchEffort

# Verified live on 2026-11-01. An answer is a search with `outputType:
# sourcedAnswer`: the same POST /v1/search, answering `{answer, sources}` at the
# top level -- not nested under `output`, as a research run's is -- and no
# request id. `sources` is every page read. With `includeInlineCitations`
# (checked live 2026-11-02) the answer ends its sentences with `[2]` or
# `[1][2]`, numbering `sources` from 1, at no other change in shape or time.

LINKUP_ANSWER_OUTPUT_TYPE = "sourcedAnswer"

LINKUP_ANSWER_INLINE_CITATIONS_FIELD = "includeInlineCitations"
LINKUP_ANSWER_INLINE_CITATIONS = True
"""Always sent, so the answer marks which source backs which sentence, as Perplexity, You.com and Exa do."""


LINKUP_ANSWER_PARAM_NAMES: dict[str, str] = {
    "include_domains": "includeDomains",
    "exclude_domains": "excludeDomains",
}
"""Normalized parameter -> Linkup field, as on search. ``country`` and ``locale`` have no Linkup field."""

LINKUP_ANSWER_FROM_DATE_FIELD = "fromDate"

# ``recency`` sets ``fromDate`` to the window's first day (``_clock.recency_start``).
# Verified live on 2026-11-01: a week's window turned an answer about a topic's
# latest news from early-October sources to late-October ones, and a ``toDate``
# in 2019 to a 2019-only answer. Linkup filters on the date it holds for a page,
# which is not always its publication date -- one of twenty sources in the week's
# window was a January page.

LINKUP_ANSWER_DATE_FORMAT = "%Y-%m-%d"
"""``fromDate`` takes a calendar date; Linkup's docs give YYYY-MM-DD."""

LINKUP_DEPTH_BY_EFFORT: dict[ResearchEffort, str] = {
    "low": "flash",
    "medium": "fast",
    "high": "standard",
    "max": "deep",
}
"""A search's ``depth`` for each ``effort``, one for one; unset sends ``LINKUP_DEFAULT_DEPTH``, which is medium's.

Linkup's four depths, per its search reference: ``flash`` the lowest latency,
``fast`` its recommended default, ``standard`` one agentic pass, ``deep``
several. Measured live on 2026-09-30 at roughly 0.4 s, 1 s, 1.3 s and 6-16 s,
with snippets of the same size; ``deep`` is billed at ten times the others.
A search's ``recency`` sets ``fromDate`` as an answer's does.
"""

LINKUP_RESEARCH_PATH = "/v1/research"
LINKUP_RESEARCH_STATUS_PATH = "/v1/research/{research_id}"

LINKUP_RESEARCH_DEPTHS: dict[ResearchEffort, str] = {
    # `reasoningDepth` runs S, M, L, XL against the four normalized levels one
    # for one. It sets the price too, $0.25 to $2.50 a run, and defaults to L, so
    # an unset effort sends nothing.
    "low": "S",
    "medium": "M",
    "high": "L",
    "max": "XL",
}

LINKUP_RESEARCH_OUTPUT_TYPE = "sourcedAnswer"
"""The ``outputType`` a run asks for when it wants prose. The API requires one and has no default."""

LINKUP_RESEARCH_STRUCTURED_OUTPUT_TYPE = "structured"
"""The ``outputType`` a run with an ``output_schema`` asks for, beside ``structuredOutputSchema``.

Verified live on 2026-09-30: the finished run's ``output`` is the object the
schema describes and nothing else -- ``{"city": "Paris", "year": 1889}`` for a
two-field schema -- with no answer and no sources beside it.
"""

LINKUP_RESEARCH_SCHEMA_FIELD = "structuredOutputSchema"
"""Where the schema goes, as a JSON string, which is how Linkup takes it."""

LINKUP_RESEARCH_STATES: dict[str, ResearchState] = {
    "pending": "queued",
    "processing": "running",
    "completed": "completed",
    "failed": "failed",
}
