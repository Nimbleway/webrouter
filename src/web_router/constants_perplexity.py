"""Perplexity search and answer: the search options' fields, the answer's effort level, tool fields and recency table.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Perplexity's host, auth header,
response path and output item types stay there, beside its search and research
settings.
"""

from __future__ import annotations

from web_router.types_answer import Recency
from web_router.types_options import ResearchEffort, SearchDepth

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

MSG_PERPLEXITY_ANSWER_DOMAINS_EXCLUSIVE = (
    "Perplexity filters on one domain list, not both: send include_domains or exclude_domains."
)
"""Both domain lists are refused together: the docs say a list is an allowlist or a denylist, not both.

Live, a list holding both answered 200 and kept to the allowed domain, which
says nothing about whether the excluded one was honoured -- so the pair is
refused rather than risk a dropped exclusion.
"""

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

PERPLEXITY_ANSWER_STOPPED = frozenset({"failed", "cancelled"})
"""Statuses that end an answer with nothing to return, raised as an error rather than read as an empty answer."""

MSG_PERPLEXITY_ANSWER_STOPPED = "the answer {status}: {detail}"


# The Search API's options, per docs.perplexity.ai/api-reference/search-post
# (checked 2026-11-09; not yet run live with these fields).
# `search_domain_filter` holds up to 20 domains, an excluded one prefixed `-`
# as the answer's filter does; `country` is ISO 3166-1 alpha-2;
# `search_language_filter` a list of up to 20 two-letter ISO 639-1 codes;
# `search_recency_filter` hour | day | week | month | year.

PERPLEXITY_SEARCH_NAMES = {"max_results": "max_results", "country": "country"}
"""Normalized parameter -> Perplexity search field, sent as the caller set it."""

PERPLEXITY_MAX_DOMAINS = 20
MSG_PERPLEXITY_TOO_MANY_DOMAINS = "Perplexity filters on at most {limit} domains; this request names {count}."

PERPLEXITY_LANGUAGE_FIELD = "search_language_filter"
PERPLEXITY_LANGUAGE_PATTERN = r"^[A-Za-z]{2}$"
"""A ``locale`` Perplexity can take: a bare two-letter language code. ``en-US`` is refused, not cut to ``en``."""

MSG_PERPLEXITY_LOCALE = "Perplexity takes a two-letter ISO 639-1 language code, such as 'en'; got {locale!r}."

PERPLEXITY_TOKENS_FIELD = "max_tokens_per_page"
PERPLEXITY_TOKENS_BY_DEPTH: dict[SearchDepth, int] = {"lite": 128, "fast": 1024}
"""``max_tokens_per_page`` for each ``search_depth`` it serves, at about four characters a token.

Perplexity documents no default and no whole-page setting, so ``deep`` is
refused until a live call shows a large budget returns whole pages.
"""

PERPLEXITY_SEARCH_TYPE_FIELD = "search_type"
PERPLEXITY_SEARCH_TYPE_BY_EFFORT: dict[ResearchEffort, str | None] = {
    "low": "fast",
    "medium": None,
    "high": None,
    "max": None,
}
"""``search_type`` for each ``effort``: ``web``, the default, is also its most thorough, so only low sends one.

``people`` is a vertical, not a tier, and stays in ``provider_options``.
"""
