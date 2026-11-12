"""Brave search's option mappings, and Brave answer: its path, model, and how its streamed answer is read.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Brave's host and auth header stay
there, beside its search settings.
"""

from __future__ import annotations

from web_router.types_options import Recency

# Verified live on 2026-11-03. POST /res/v1/chat/completions with model `brave`
# answers OpenAI-style. Citations come only on a streamed call with
# `enable_citations: true`: the answer arrives as `chat.completion.chunk`
# events (`data: {...}` lines, ending `data: [DONE]`), and the text carries a
# `<citation>{...}</citation>` tag after each sentence a source backs -- JSON
# with `number`, `url`, `snippet`, `favicon`, and `start_index` / `end_index`
# into the answer with the tags taken out -- and a closing `<usage>{...}</usage>`
# with the call's stated cost ($0.054 for one SimpleQA-style question, almost
# all of it input tokens). A blocking call answers with no sources at all.
# `language: "es"` answered in English and an unknown value was accepted, so
# `locale` is not mapped; nothing else in the API is a normalized option.

BRAVE_ANSWER_PATH = "/res/v1/chat/completions"

BRAVE_ANSWER_MODEL = "brave"

BRAVE_ANSWER_TAGS = ("citation", "usage")
"""The tags Brave writes into the streamed text: a citation after the sentence it backs, and the call's cost."""

BRAVE_SSE_DONE = "[DONE]"

BRAVE_ANSWER_CITATION_MAPPED = frozenset({"url", "snippet"})
"""The citation tag's fields a :class:`Citation` has; ``number``, the span and ``favicon`` go in ``extra``."""

BRAVE_FINISH_LENGTH = "length"
"""The ``finish_reason`` of an answer cut off at its token limit: ``partial``."""

MSG_BRAVE_ANSWER_STREAM_ERROR = "the answer stream reported an error: {error}"

MSG_BRAVE_ANSWER_STREAM_CUT = "the answer stream ended before [DONE], after {count} events"

# Search (api-dashboard.search.brave.com/api-reference/web/search/get and
# .../documentation/resources/search-operators). Domain filters have no field:
# they are search operators in q, which Brave marks experimental. Several sites
# are joined with OR, as its own example does; one is left out with NOT site:.

BRAVE_SEARCH_PARAM_NAMES = {"max_results": "count"}
"""Normalized parameter -> Brave field; the rest are translated below."""

BRAVE_SEARCH_PARAMETERS = frozenset(
    {*BRAVE_SEARCH_PARAM_NAMES, "search_depth", "include_domains", "exclude_domains", "country", "locale", "recency"}
)

BRAVE_COUNTRIES = frozenset(
    {
        "AR", "AU", "AT", "BE", "BR", "CA", "CL", "DK", "FI", "FR", "DE", "GR", "HK", "IN", "ID", "IT", "JP", "KR",
        "MY", "MX", "NL", "NZ", "NO", "CN", "PL", "PT", "PH", "RU", "SA", "ZA", "ES", "SE", "CH", "TW", "TR", "GB",
        "US",
    }
)  # fmt: skip
"""The 37 ISO codes Brave's ``country`` takes (its ``ALL`` is no country); another is refused before the call."""

BRAVE_FRESHNESS: dict[Recency, str] = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
"""``freshness`` by page age, "the most relevant date reported by the content, such as its published or last
modified date": 24 hours, 7 days, 31 days and 365 days."""

BRAVE_EXCLUDE_DOMAIN = "NOT site:{domain}"

BRAVE_QUERY_MAX_CHARS = 600
BRAVE_QUERY_MAX_WORDS = 75
"""q's documented limits, operators included; a longer one is refused before it is sent, never cut."""

MSG_BRAVE_QUERY_TOO_LONG = (
    "Brave takes a query of at most {chars} characters and {words} words, and this one would be {length} "
    "characters and {count} words; include_domains and exclude_domains are sent inside the query and count "
    "toward it. Shorten the query, drop a filter, or use another provider."
)

MSG_BRAVE_SEARCH_VALUES = (
    "Brave searches only the countries and languages it lists: country at "
    + ", ".join(sorted(BRAVE_COUNTRIES))
    + ", and locale at a language "
    "it lists (pt and zh alone are refused, as each could be either of two). Ask for one of those, leave it "
    "unset, or name another provider -- see the search table at https://docs.webrouter.net/providers#search."
)
