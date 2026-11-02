"""Linkup answer: its output type, field names and recency windows.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Linkup's host, auth header and
search path stay there, beside its search, extract and research settings.
"""

from __future__ import annotations

from web_router.types import Recency

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

LINKUP_ANSWER_RECENCY_DAYS: dict[Recency, int] = {
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
"""How far back ``fromDate`` is set for each ``recency``, counted from today.

Verified live on 2026-11-01: a week's window turned an answer about a topic's
latest news from early-October sources to late-October ones, and a ``toDate``
in 2019 to a 2019-only answer. Linkup filters on the date it holds for a page,
which is not always its publication date -- one of twenty sources in the week's
window was a January page.
"""

LINKUP_ANSWER_DATE_FORMAT = "%Y-%m-%d"
"""``fromDate`` takes a calendar date; Linkup's docs give YYYY-MM-DD."""
