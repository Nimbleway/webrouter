"""The answer method's messages and MCP tool text.

Kept apart so ``constants.py`` stops growing; import them from here by name. Imports
``types_answer`` (which imports only ``types``), as AGENTS.md allows a ``constants_*``
module to. The answer backends' variable table stays in ``constants.py``,
beside every method's.
"""

from __future__ import annotations

from typing import Any, get_args

from web_router.types_answer import Recency

__all__ = [
    "ANSWER_MARKER",
    "MCP_ANSWER_PROPERTIES",
    "MCP_DESC_ANSWER",
    "MSG_ANSWER_FAILED",
    "MSG_ANSWER_NONE_CONFIGURED",
    "MSG_ANSWER_NO_DEFAULT",
]

MSG_ANSWER_FAILED = "answer failed: {error}"

ANSWER_MARKER = "[{number}]"
"""An inline citation as most answer backends write it: the source's number in brackets, ``[2]``."""

MSG_ANSWER_NO_DEFAULT = (
    "answer() has no 'auto' yet: the router's default ({default!r}) does not serve it, and {count} answer "
    "backends are configured, so there is no single one to use. Name one with provider=: {available}."
)
"""Raised as :class:`~web_router.exceptions.ProviderNotFoundError` when no answer backend could be chosen.

``answer()`` falls back to the one configured backend when the router's default
does not serve it; with several configured, which to call is the caller's choice.
"""

MSG_ANSWER_NONE_CONFIGURED = (
    "answer() has no 'auto' yet: the router's default ({default!r}) does not serve it, and no answer backend "
    "has a key. Set one of {env_vars}, or pass one through provider_config."
)
"""The same refusal with nothing configured: what to set, rather than a list of names that cannot run."""

MCP_ANSWER_PROPERTIES: dict[str, dict[str, Any]] = {
    "query": {"type": "string", "description": "The question to answer."},
    "include_domains": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Restrict sources to these domains.",
    },
    "exclude_domains": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Keep these domains out of the sources.",
    },
    "country": {"type": "string", "description": "Country code for geo-targeting, e.g. 'US'."},
    "locale": {"type": "string", "description": "Language code, e.g. 'en'."},
    "recency": {
        "type": "string",
        "enum": list(get_args(Recency)),
        "description": "Only sources from the last day, week, month or year.",
    },
}

MCP_DESC_ANSWER = (
    "Answer one question: the backend searches and writes a short answer itself, and it comes back "
    "with the sources it rests on. Returns in a few seconds and bills one call. Use it for a closed "
    "question whose answer is a sentence or two; use `search` when you need the pages themselves, "
    "`extract` when you have the URL, and `research` for a question that needs an investigation. "
    "The answer is the backend's own, unedited -- check it against the sources when it matters. A "
    "source the answer cites inline carries that marker (e.g. '[2]') in its `field`. There is no "
    "automatic choice of backend yet: with more than one configured, name one with `provider`."
)
