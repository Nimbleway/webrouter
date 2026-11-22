"""What the agent tools say to a model, and the defaults they run with.

Every message a model reads says in words that the call did not run or
failed, because an OpenAI tool output has no error flag: the text is all the
model has to tell a refusal from an answer.
"""

from __future__ import annotations

from typing import Final

from web_router.constants import LANGCHAIN_EXTRACT_TOOL_NAME, LANGCHAIN_SEARCH_TOOL_NAME
from web_router.constants_answer import LANGCHAIN_ANSWER_TOOL_NAME
from web_router.constants_extract import MSG_EXTRACT_NEAR_EMPTY

INTEGRATION_TIMEOUT: Final = 60.0
"""Seconds one tool call may take before it comes back as a ``timeout`` error result; ``timeout=None`` lifts it."""

INTEGRATION_URL_POLICY: Final = "public_only"
"""The URL policy the tools hold a model's URLs to unless the caller passes ``url_policy=None``."""

LANGCHAIN_NAMES: Final = {
    "search": LANGCHAIN_SEARCH_TOOL_NAME,
    "extract": LANGCHAIN_EXTRACT_TOOL_NAME,
    "answer": LANGCHAIN_ANSWER_TOOL_NAME,
}
"""The LangChain tools' names, by method: a model that guesses one is told the name it meant."""

MODEL_WARNINGS: Final = frozenset({MSG_EXTRACT_NEAR_EMPTY})
"""The page warnings a model reads -- web-router's own, which it can act on; a vendor's stay on the response."""

MSG_DID_NOT_RUN: Final = "{tool} did not run: {why}"
MSG_FAILED: Final = "{tool} failed: {why}"
MSG_UNKNOWN_TOOL: Final = "there is no such tool here; call one of {names}."
MSG_LANGCHAIN_NAME: Final = "there is no such tool here; {name} is the LangChain tool's name, this one is {ours}."
MSG_NOT_JSON: Final = "its arguments are not valid JSON ({error})."
MSG_NOT_OBJECT: Final = "its arguments must be a JSON object, got {got}."
MSG_UNKNOWN_KEYS: Final = "it takes only `{field}`; leave out {extra}."
MSG_MISSING: Final = "it needs `{field}`, which was missing."
MSG_QUERY: Final = "`query` must be a non-empty string, got {got}."
MSG_URLS: Final = "`urls` must be a list of 1 to {max_urls} URL strings, got {got}."
MSG_NO_URL_ALLOWED: Final = "every URL was refused; see failures."

JSON_TYPES: Final[dict[str, str]] = {
    "null": "null",
    "boolean": "a boolean",
    "number": "a number",
    "string": "a string",
    "empty": "an empty string",
    "array": "an array",
    "object": "an object",
}
"""A value's JSON type as a model would name it, for the ``got`` of a message above."""
GOT_BLANK: Final = "a blank string"
GOT_COUNT: Final = "{count} of them"
GOT_AMONG: Final = "{got} among them"

FACTS: Final = "kind={kind}, retryable={retryable}"
FACT_STATUS: Final = ", status={status}"
FACT_RETRY_AFTER: Final = ", retry_after={seconds:g}s"
TRIED: Final = " Tried: {members}."
SHORT_ERROR: Final = "{who}{reason} ({facts})."

KIND_REASONS: Final[dict[str, str]] = {
    "auth": "the backend refused its key",
    "rate_limit": "the backend is throttling this key or it is out of quota",
    "bad_request": "the backend refused the request as sent",
    "not_found": "the backend's endpoint or the page is not there",
    "timeout": "no answer in time",
    "target": "the page's address is the problem, so another backend would fail the same way",
    "local": "a failure on this side of the call, not the backend's",
    "upstream": "the backend failed",
}
"""What each failure kind means, in the words a model reads; the vendor's own text stays on the exception."""

REFUSED_KIND: Final = "refused"
"""The kind a URL the URL policy refused carries in ``failures``: it was never sent."""

GENERIC_REASON: Final = "it failed"
