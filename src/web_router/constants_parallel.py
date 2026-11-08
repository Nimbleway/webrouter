"""Parallel search and answer: the Search API's path and fields, and the Chat API's path, model and response fields.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Parallel's host, key header and
research settings stay there.
"""

from __future__ import annotations

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

PARALLEL_ADVANCED_NAMES: dict[str, str] = {"max_results": "max_results"}
"""Normalized parameter -> its field under :data:`PARALLEL_ADVANCED_SETTINGS`, sent as the caller set it.

An option that needs translating -- a depth to an excerpt size, a window to an
``after_date`` -- builds its own piece of the same object instead.
"""

PARALLEL_SEARCH_PARAMETERS: frozenset[str] = frozenset(PARALLEL_ADVANCED_NAMES)
"""The shared search options Parallel maps."""

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
