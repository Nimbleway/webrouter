"""Perplexity answer: its effort level, the web_search tool's fields and the recency table.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Perplexity's host, auth header,
response path and output item types stay there, beside its search and research
settings.
"""

from __future__ import annotations

from web_router.types_answer import Recency

# Verified live on 2026-11-01. An answer is POST /v1/responses with no
# `background`, so the call returns the finished response: `status`
# `completed`, a `search_results` output item whose results carry an integer
# `id`, then a `message` whose text cites them inline as `[2]` or `[3][10]`.
# `annotations` was always empty. `effort` and `preset` were both accepted and
# both echoed as `fast@2026-10-16.0`; `effort` is the name the docs use.

PERPLEXITY_ANSWER_EFFORT_FIELD = "effort"
PERPLEXITY_ANSWER_EFFORT = "fast"
"""The lowest level: one search step, no reasoning, an answer in about two seconds."""

PERPLEXITY_ANSWER_TOOL = "web_search"
"""The one tool an answer configures, and only when a filter or location is set."""

PERPLEXITY_ANSWER_DOMAIN_FIELD = "search_domain_filter"
PERPLEXITY_ANSWER_EXCLUDE_PREFIX = "-"
"""An excluded domain is the same list's entry with this prefix."""

PERPLEXITY_ANSWER_DOMAINS_EXCLUSIVE = frozenset({"include_domains", "exclude_domains"})
"""Refused together: the docs say a list is an allowlist or a denylist, not both.

Live, a list holding both answered 200 and kept to the allowed domain, which
says nothing about whether the excluded one was honoured -- so the pair is
refused rather than risk a dropped exclusion.
"""

MSG_PERPLEXITY_ANSWER_DOMAINS_EXCLUSIVE = (
    "Perplexity filters on one domain list, not both: send include_domains or exclude_domains."
)

PERPLEXITY_ANSWER_RECENCY_FIELD = "search_recency_filter"
PERPLEXITY_ANSWER_LOCATION_FIELD = "user_location"
PERPLEXITY_ANSWER_COUNTRY_FIELD = "country"
"""Under ``user_location``, beside ``filters``.

Live, an emergency-number question answered 999 for GB and 911 for US.
"""

PERPLEXITY_ANSWER_RECENCY: dict[Recency, str] = {
    "day": "day",
    "week": "week",
    "month": "month",
    "year": "year",
}
"""Perplexity's own values, one for one. It also has ``hour``, which no other backend offers."""
