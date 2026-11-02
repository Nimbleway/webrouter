"""Exa answer: its path and the fields of its response.

Kept apart from :mod:`web_router.constants`, which every other provider's
settings share, so that file stops growing. Exa's host and auth header stay
there, beside its search and contents settings.
"""

from __future__ import annotations

# Verified live on 2026-11-01. POST /answer with the query alone answered in
# about 1.4 seconds with `answer`, citing inline as `[1][2][3]` -- numbering
# `citations` from 1 -- `citations` of `{id, url, title, author,
# publishedDate, image}`, `requestId` and `costDollars` ($0.005 a call). `text`
# comes back only when `text: true` is sent, which is not sent here. `answer`
# is a string unless `outputSchema` is sent, which is not mapped.
# `userLocation` was accepted but showed no clear effect: for GB the answer
# never named 999, so `country` is not mapped to it.

EXA_ANSWER_PATH = "/answer"

EXA_ANSWER_CITATION_TEXT_FIELD = "text"
"""The cited page's text, present only when asked for; read as the snippet when it is there."""
