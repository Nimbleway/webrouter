"""Routing's fixed values: the candidate id's separator and the messages its types raise.

Experimental: :mod:`web_router.routing` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

CANDIDATE_ID_SEPARATOR = ":"
"""Between a candidate's tool and its provider: ``search:exa``."""

MSG_CANDIDATE_ID = "Candidate id {id!r} does not match its tool and provider; it would be {expected!r}."

RULES_ROUTER_NAME = "rules"
"""The built-in rules router's name, as a decision record and the MCP capabilities resource show it."""

RULES_REASON_URL = "the prompt names a URL"
RULES_REASON_RESEARCH = "the prompt asks for research: a report, a comparison or an in-depth analysis"
RULES_REASON_NO_URL = "the prompt names no URL"

RULES_RESEARCH_PATTERN = (
    # Opening with the verb, after a polite opener: "research ...", "can you analyze ...".
    r"^\W*(?:(?:please|kindly|can\s+you|could\s+you|help\s+me(?:\s+to)?|i\s+(?:want|need)\s+you\s+to)\s+)*"
    r"(?:research|analy[sz]e)\b"
    # Or asking, anywhere, for what research writes.
    r"|\b(?:in[-\s]depth|deep[-\s]dive|literature\s+review|pros\s+and\s+cons"
    r"|market\s+(?:research|analysis|landscape)|compet(?:itive|itor)\s+(?:analysis|landscape)"
    r"|comprehensive\s+(?:report|analysis|overview|review|comparison|guide))\b"
    r"|\b(?:write|prepare|produce|draft|create)\s+(?:me\s+)?an?\s+(?:[\w-]+\s+){0,3}?(?:report|analysis|briefing)\b"
    r"|\bcompare\b.{1,200}?\b(?:and|vs\.?|versus|with|to|against)\b"
)
"""What the rules router reads as a request for research, case-insensitive: phrases, not single words.

A wrong research pick costs a run of up to a few dollars and a minute or more, where a wrong search costs one
cheap call the caller can follow with research, so a question that only says why, how, all or list is searched.
English only: a prompt in another language is searched."""

PROMPT_PARAMS = frozenset({"query", "task"})
"""The options that carry a plan's prompt, left out of its ``repr`` with the prompt itself."""

MSG_BUDGET_VALUE = "Budget({name}=...) takes a finite number of 0 or more, or None for no cap; got {value!r}."
