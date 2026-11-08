"""DeepSeek search and extract: endpoints, limits and the query operators filters become.

Kept apart from :mod:`web_router.constants`, as :mod:`web_router.constants_you`
is, so that file stops growing. The env var and the provider's place in ``auto``
stay there, beside every other backend's.
"""

from __future__ import annotations

from web_router.types_options import SearchDepth

# Search and Fetch (api-docs.deepseek.com/guides/search), on the same host and
# bearer key as DeepSeek's model API. Checked live on 2026-10-19; see
# tests/providers/test_deepseek.py for what each call showed.

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_SEARCH_PATH = "/v1/tools/search"
DEEPSEEK_FETCH_PATH = "/v1/tools/fetch"
DEEPSEEK_API_KEY_PREFIX = "Bearer "

DEEPSEEK_SEARCH_PARAMETERS = frozenset(
    {"max_results", "include_domains", "exclude_domains", "locale", "recency", "search_depth"}
)
"""What DeepSeek Search maps.

``max_results`` goes to ``count``; the domain filters, ``locale`` and
``recency`` go into the query, as operators.

``search_depth`` serves ``lite`` and ``deep`` (see ``DEEPSEEK_DEPTH_LEVELS``).

``country`` stays unmapped and raises: DeepSeek's ``localization`` takes an IP
address to localize from, not a country, and inventing an IP for a country is
not a translation. ``effort`` too: Search has no tier.
"""

DEEPSEEK_DEPTH_LEVELS: frozenset[SearchDepth] = frozenset({"lite", "deep"})
"""DeepSeek has two content tiers: a snippet and a summary written for a model, or those plus the page body.

``lite`` is the first, DeepSeek's default, and sends nothing. DeepSeek documents
the summary at 600-800 characters; live, 40 came back at 108-4,651, median about
1,800, so lite runs long of the ~500-character definition. ``deep`` asks for the
page body (``need_content``). There is no truncated body between them, so ``fast``
is refused rather than served as either.
"""

DEEPSEEK_DEEP = {"need_content": True}

DEEPSEEK_MAX_COUNT = 50
"""The most results one search returns; a larger ``count`` is out of range.

``max_results`` is an upper bound, so a larger one is sent as this and still holds.
"""

DEEPSEEK_QUERY_LIMIT = 128
"""The longest query Search accepts, in Unicode characters, operators included.

A longer one is refused before it is sent, never cut: a query missing its tail,
or the filters web-router appended to it, would answer a different question.
"""

DEEPSEEK_INCLUDE_DOMAIN = "site:{domain}"
DEEPSEEK_INCLUDE_JOINER = " OR "
DEEPSEEK_EXCLUDE_DOMAIN = "-site:{domain}"
DEEPSEEK_LANGUAGE = "lang:{language}"
DEEPSEEK_AFTER = "after:{day}"
"""Search filters by domain and language through operators in the query, not through fields.

Several sites are joined with ``OR``, as DeepSeek's own example does
(``DeepSeek site:github.com OR site:huggingface.co``). ``lang:`` takes bare
language codes (``lang:zh,en``), so a regional locale sends its language alone. ``after:`` takes
a date ("pages published after"), so ``recency`` sends the day its window starts.
"""

DEEPSEEK_FETCH_INTERVAL = 0.2
"""Seconds between fetch starts in one extract: DeepSeek allows an account 5 fetches a second.

Past it, a fetch answers 429. The limit is per account, and Fetch adds one per
domain, so other callers on the same key can still draw a 429.
"""

MSG_DEEPSEEK_QUERY_TOO_LONG = (
    "DeepSeek Search takes a query of at most {limit} characters, and this one would be {length}; "
    "include_domains, exclude_domains, locale and recency are sent inside the query and count toward it. "
    "Shorten the query, drop a filter, or use another provider."
)
