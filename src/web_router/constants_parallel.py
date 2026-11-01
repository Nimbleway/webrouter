"""Parallel answer: the Chat API's path, model and response fields.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Parallel's host and its search and
research settings stay there.
"""

from __future__ import annotations

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

PARALLEL_ANSWER_TRUNCATED = "length"
"""The ``finish_reason`` saying the generation was cut short, the one early stop the API reports."""
