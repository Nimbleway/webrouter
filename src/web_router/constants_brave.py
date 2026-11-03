"""Brave answer: its path, model, and how its streamed answer is read.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Brave's host and auth header stay
there, beside its search settings.
"""

from __future__ import annotations

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

BRAVE_SSE_DATA_PREFIX = "data: "

BRAVE_SSE_DONE = "[DONE]"

BRAVE_ANSWER_CITATION_MAPPED = frozenset({"url", "snippet"})
"""The citation tag's fields a :class:`Citation` has; ``number``, the span and ``favicon`` go in ``extra``."""

BRAVE_FINISH_LENGTH = "length"
"""The ``finish_reason`` of an answer cut off at its token limit: ``partial``."""

MSG_BRAVE_ANSWER_STREAM_ERROR = "the answer stream reported an error: {error}"
