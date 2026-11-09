"""Parallel search and answer: the Search API's path and fields, and the Chat API's path, model and response fields.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Parallel's host, key header and
research settings stay there.
"""

from __future__ import annotations

from web_router.types_options import ResearchEffort, SearchDepth

# The Search API on /v1, per Parallel's API reference and migration guide
# (checked 2026-11-09; not yet run live). /v1beta/search took every setting at the
# top level; /v1/search takes only the objective, the queries, `mode` and a few
# session fields there, and nests the rest under `advanced_settings`, rejecting
# any field it does not declare. The response is the beta's: `search_id`, and
# `results` of `{url, title, publish_date, excerpts}`.

PARALLEL_SEARCH_PATH = "/v1/search"

PARALLEL_SEARCH_MODE = "fast"
"""Sent on every search, so a search keeps the speed and price it had on the beta endpoint.

Unset, /v1 runs ``advanced``, the beta's ``agentic``: slower, and $5 per 1,000
rather than $1. The beta's default, ``one-shot``, has no /v1 name; Parallel's
migration guide sends both it and the beta's ``fast`` to ``fast``.
"""

PARALLEL_ADVANCED_SETTINGS = "advanced_settings"
"""The object /v1/search nests its result count, source policy, excerpt settings and location under."""
PARALLEL_SOURCE_POLICY = "source_policy"
PARALLEL_SOURCE_FIELDS = {"include_domains": "include_domains", "exclude_domains": "exclude_domains"}
PARALLEL_AFTER_DATE = "after_date"
PARALLEL_EXCERPT_SETTINGS = "excerpt_settings"
PARALLEL_EXCERPT_MAX = "max_chars_per_result"
PARALLEL_LOCATION = "location"
"""The fields under ``advanced_settings`` that the shared options fill: domains and ``recency`` in the source
policy, ``search_depth`` as the excerpt cap, ``country`` as the location."""

PARALLEL_ADVANCED_NAMES: dict[str, str] = {"max_results": "max_results"}
"""Normalized parameter -> its field under :data:`PARALLEL_ADVANCED_SETTINGS`, sent as the caller set it.

An option that needs translating -- a depth to an excerpt size, a window to an
``after_date``, a country to a lowercase ``location`` -- builds its own piece of
the same object instead.
"""

PARALLEL_EXCERPT_CHARS: dict[SearchDepth, int] = {"lite": 500, "fast": 3000}
"""``search_depth`` -> ``advanced_settings.excerpt_settings.max_chars_per_result``.

Parallel documents this as an upper bound per URL on excerpts chosen for the
query, never the page, so ``deep`` is left out and refused: no setting returns
a whole page.
"""

PARALLEL_MODE_BY_EFFORT: dict[ResearchEffort, str] = {
    "low": "turbo",
    "medium": PARALLEL_SEARCH_MODE,
    "high": "advanced",
    "max": "advanced",
}
"""``effort`` -> ``mode``, on Parallel's ladder of turbo, fast, basic and advanced.

``medium`` is the pinned ``fast``, what a search sends when ``effort`` is unset.
``low`` is ``turbo``, the fastest (about 200 ms against fast's 700, at fast's
$1 per 1,000; English and Japanese queries only). ``advanced``, the most
thorough (about 3 s, $5 per 1,000), serves both ``high`` and ``max``: ``basic``
returns longer excerpts rather than searching harder, so it sits on no rung.
``provider_options={"mode": ...}`` still overrides any of them.
"""

PARALLEL_DOMAIN_LIMIT = 200
"""The most entries ``include_domains`` and ``exclude_domains`` may hold between them."""

PARALLEL_SEARCH_PARAMETERS: frozenset[str] = frozenset(PARALLEL_ADVANCED_NAMES) | {
    "search_depth",
    "include_domains",
    "exclude_domains",
    "country",
    "recency",
    "effort",
}
"""The shared search options Parallel maps. ``locale`` is not among them: /v1/search takes no language."""

MSG_PARALLEL_DOMAINS = (
    f"Parallel takes at most {PARALLEL_DOMAIN_LIMIT} domains in a filter, and ignores exclude_domains whenever "
    "include_domains is set, so the two are never sent together: list only the domains to include, or only those "
    "to exclude."
)
PARALLEL_LOCATIONS = frozenset(
    {
        "ar",
        "au",
        "at",
        "be",
        "br",
        "ca",
        "cl",
        "cn",
        "dk",
        "fi",
        "fr",
        "de",
        "gr",
        "hk",
        "in",
        "id",
        "it",
        "jp",
        "my",
        "mx",
        "nl",
        "nz",
        "no",
        "ph",
        "pl",
        "pt",
        "ru",
        "sa",
        "za",
        "kr",
        "es",
        "se",
        "ch",
        "tw",
        "tr",
        "gb",
        "us",
    }
)
"""The ``location`` codes Parallel geo-targets, per its advanced-settings page (checked 2026-11-09).

Any other code is ignored with a warning rather than rejected, so ``country``
outside this set is refused before sending. The United Kingdom is ``gb``.
"""

MSG_PARALLEL_COUNTRY = (
    "Parallel geo-targets only these countries, and ignores any other: "
    + ", ".join(sorted(code.upper() for code in PARALLEL_LOCATIONS))
    + "."
)
MSG_PARALLEL_TURBO_PATHS = (
    "effort='low' runs Parallel's turbo mode, which takes no domain/path prefix in include_domains or "
    "exclude_domains; filter by bare domain, or ask for another effort."
)

# Verified live on 2026-11-01. The Chat API is OpenAI-compatible and in beta:
# POST /v1beta/chat/completions, authenticated with a bearer token (the search
# and task APIs take `x-api-key`). `lite` answered in about seven seconds with
# one sentence in `choices[0].message.content` and its sources under a
# top-level `basis`: per answer `field`, `citations` of `{title, url,
# excerpts}`, a `reasoning` and a `confidence`. `speed` answered faster but its
# `basis` was empty -- its sources only as `[domain, date]` text in the answer --
# so it cannot back an answer with its sources, and is not used.

PARALLEL_ANSWER_PATH = "/v1beta/chat/completions"
PARALLEL_ANSWER_API_KEY_HEADER = "Authorization"
PARALLEL_ANSWER_API_KEY_PREFIX = "Bearer "

PARALLEL_ANSWER_MODEL = "lite"
"""The fastest model that returns ``basis``: its sources, tied to the answer."""

PARALLEL_ANSWER_ROLE = "user"

PARALLEL_ANSWER_WHOLE_FIELD = "answer"
"""The ``basis`` field naming the answer as a whole, which a citation leaves as ``field=None``."""

PARALLEL_ANSWER_BASIS_EXTRA = frozenset({"confidence"})
"""The basis keys an answer's citation keeps in ``extra``; a research run's keeps every unmapped one."""

PARALLEL_ANSWER_TRUNCATED = "length"
"""The ``finish_reason`` saying the generation was cut short, the one early stop the API reports."""
