"""Routing's fixed values: the candidate id's separator and the messages its types raise.

Experimental: :mod:`web_router.routing` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

CANDIDATE_ID_SEPARATOR = ":"
"""Between a candidate's tool and its provider: ``search:exa``."""

MSG_CANDIDATE_ID = "Candidate id {id!r} does not match its tool and provider; it would be {expected!r}."

PROMPT_PARAMS = frozenset({"query", "task"})
"""The options that carry a plan's prompt, left out of its ``repr`` with the prompt itself."""

MSG_BUDGET_VALUE = "Budget({name}=...) takes a finite number of 0 or more, or None for no cap; got {value!r}."
