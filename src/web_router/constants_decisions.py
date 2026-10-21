"""The decision gate: what classify() asks a decider, how labels become a route, and the built-in deciders.

Kept apart from :mod:`web_router.constants`, as :mod:`web_router.constants_kernel`
is, so that file stops growing.
"""

from __future__ import annotations

from typing import Any

# --- Decision gate (spike) --------------------------------------------------
# WebRouter.classify(): one decision-model call labels the query, code turns the
# labels into a method and an auto chain order. Opt-in through decider=.

ENV_TYPESAFE_API_KEY = "TYPESAFE_API_KEY"
"""TypeSafe's key, for the Jev decider. A library-level key, like the judge's: no provider table names it."""

TYPESAFE_BASE_URL = "https://api.typesafe.ai"
TYPESAFE_DECISION_PATH = "/v1/systemone"
JEV_MODEL = "jev-1.14.0"
"""Pinned rather than ``jev-latest``: an alias moves, and thresholds are tuned against one version."""

URL_TRAILING_PUNCTUATION = ".,;:!?"
"""Stripped from a URL found in prose: the end of a sentence, not part of the address."""

ROUTE_DECIDER_JEV = "jev"
"""Jev by name: ``WebRouter(decider="jev")``."""

ROUTE_DECIDER_TIMEOUT = 0.8
"""Seconds. Past this the route falls back to the default one rather than hold up the call it routes."""
ROUTE_DECIDER_CONNECT_TIMEOUT = 0.3

ROUTE_MIN_CONFIDENCE = 0.5
"""Below this a label counts as unsure and its default is used. A starting point, not measured."""

ROUTE_METHODS = ("search", "extract", "research", "browse")
"""What a route can run. One whose auto chain is empty here downgrades to search."""

ROUTE_QUESTIONS: dict[str, dict[str, Any]] = {
    "tool": {
        "type": "choice",
        "instructions": "Which kind of web call best answers this request?",
        "criteria": {
            "search": "A lookup: a fact, a name, a number or a short answer found in search results.",
            "extract": "Read one specific known page, document or URL the request points to.",
            "research": "A multi-source investigation or report that needs many searches and synthesis.",
            "browse": "Act on a website: click, fill a form, log in, or move through several pages.",
        },
    },
    "recent": {
        "type": "choice",
        "instructions": (
            "How old can the sources be and still answer this correctly, counting back from asked_on? "
            "If two windows fit, pick the longer one."
        ),
        "criteria": {
            "hour": "Live status that changes by the minute: a service outage now, a live score, an event in progress.",
            "day": (
                "Today's state: a current price, stock availability, a rate or quote, "
                "today's or tomorrow's weather, availability for specific dates."
            ),
            "week": "Something from the past few days: this week's announcements, posts, meetings or decisions.",
            "month": (
                "Recent but not daily: the latest release, funding round or quarterly result, "
                "current pricing plans, recent discussion."
            ),
            "any": (
                "Answerable from sources of any age: history, definitions, how-to, "
                "long-standing roles, past events with a known outcome."
            ),
        },
    },
    "coverage": {
        "type": "choice",
        "instructions": "How much evidence does a correct answer need? If unsure, pick the lower value.",
        "criteria": {
            "lookup": "One good source settles it. e.g. 'Who is the CEO of Example Corp?'",
            "synthesis": (
                "A few sources, combined or checked against each other. e.g. 'Which has the longest battery life: "
                "MacBook Air M4, Dell XPS 13 or ThinkPad X1 Carbon?'"
            ),
            "research": (
                "Many sources, open-ended, or a report. e.g. 'Map the vendors selling web data APIs to AI agents, "
                "with pricing'."
            ),
        },
    },
    "well_scoped": {
        "type": "choice",
        "instructions": "Is this request clear enough to act on without guessing what it means?",
        "criteria": {
            "clear": "It names what it is about, so a search could answer it as written.",
            "missing_context": (
                "It refers to something it never names ('it', 'he', 'the one above'), lacks a place or time it "
                "needs ('opening hours tomorrow'), or a key word has several meanings ('Mercury price today')."
            ),
        },
    },
}
"""The labels' criteria. Ties resolve to lookup (coverage) and clear (well_scoped)."""

ROUTE_DEFAULT_LABELS = {"tool": "search", "recent": "any", "coverage": "lookup", "well_scoped": "clear"}
"""What an unsure, missing or failed label falls back to."""

ROUTE_LOW_CONFIDENCE_EXEMPT_LABELS = ("well_scoped",)
"""Labels whose unsure answer takes its default without recording low_confidence: they never change the method or
chain."""

MSG_ROUTE_NO_DECIDER = (
    "classify() needs a decider: WebRouter(decider='keywords') needs no key; "
    "'jev' needs TYPESAFE_API_KEY and 'llm' OPENAI_API_KEY."
)
MSG_ROUTE_UNKNOWN_DECIDER = "Unknown decider {name!r}; known: {available}."
MSG_ROUTE_DECIDER_CONFIG_UNUSED = (
    "decider_config is for a decider given by name; configure a decider instance yourself before passing it."
)
MSG_ROUTE_BAD_ANSWER = "The decider's answer could not be read: {detail}"
MSG_ROUTE_BAD_PROBABILITIES = "probabilities that cannot be scaled to sum to 1: {probabilities}"

ROUTE_FALLBACK_NO_URL = "no_url"
"""Extract was the pick, but the request names no page: search to find it first."""
ROUTE_FALLBACK_NO_BACKEND = "no_backend_for_method"
"""The pick has no backend configured here, so the route downgrades to search."""
ROUTE_FALLBACK_LOW_CONFIDENCE = "low_confidence"
"""At least one label that can move the route was below ROUTE_MIN_CONFIDENCE and took its default."""
ROUTE_FALLBACK_DECIDER_ERROR = "decider_error"
"""The decider failed or timed out; every label took its default."""

ROUTE_ADVICE_NO_URL = "search to find the page, then extract it"
ROUTE_ADVICE_NO_BACKEND = "{method} ({wanted} would fit, but no {wanted} backend is configured here)"
ROUTE_ADVICE_LOW_CONFIDENCE = "{method}, low confidence"
ROUTE_ADVICE_LEANED = "{method}, low confidence (the classifier leaned {wanted})"
ROUTE_ADVICE_DECIDER_ERROR = "{method} (the classifier was unavailable, so this is the default)"
ROUTE_TOOL_LINE = "Recommended tool: {advice}{confidence}"
ROUTE_LABEL_LINE = "{heading}: {text}{confidence}"
ROUTE_CONFIDENCE = " (confidence {value:.2f})"
ROUTE_UNSURE = " (unsure at {value:.2f}, so the default)"
"""The lines classify's text is built from: what an agent reads, each label as a plain instruction."""

ROUTE_FRESHNESS_TEXT = {
    "hour": "use sources from the past hour; ignore older results",
    "day": "use sources from today or yesterday; ignore older results",
    "week": "use sources from the past week; ignore older results",
    "month": "use sources from the past month; ignore older results",
    "any": "any date is fine",
}
ROUTE_SCOPE_TEXT = {
    "clear": "clear",
    "missing_context": "missing context: ask the user for it before searching",
}
ROUTE_COVERAGE_TEXT = {
    "lookup": "one good source settles it",
    "synthesis": "a few sources, combined or checked against each other",
    "research": "many sources, open-ended, or a report",
}
ROUTE_LABEL_LINES = (
    ("recent", "Freshness", ROUTE_FRESHNESS_TEXT),
    ("well_scoped", "Request", ROUTE_SCOPE_TEXT),
    ("coverage", "Evidence needed", ROUTE_COVERAGE_TEXT),
)
"""The labels after the tool line, in order: name, heading, and the instruction for each value."""


# --- Decision providers (spike) ---------------------------------------------
# Who answers ROUTE_QUESTIONS for classify() is swappable: Jev, a chat model over
# an OpenAI-compatible API, keyless keyword rules, or any object with decide() and close().

ROUTE_DECIDER_LLM = "llm"
"""A chat model answers the questions with structured outputs: ``WebRouter(decider="llm")``. It calls the chat
completions API, so any OpenAI-compatible endpoint works through ``base_url``."""
ROUTE_DECIDER_KEYWORDS = "keywords"
"""Simple keyword rules, keyless and deterministic: ``WebRouter(decider="keywords")``."""

MSG_DECISION_DECIDER_FAILED = "The decider failed: {detail}"

LLM_DECIDER_MODEL = "gpt-4.1-mini"
"""Small and fast: the decider runs before every routed call, and the answer is four short labels."""

LLM_DECIDER_TIMEOUT = 5.0
"""Seconds. Longer than ROUTE_DECIDER_TIMEOUT, which is Jev's: a general model over the network rarely answers
in 0.8s. Past it the route falls back to the default, as for any failed decider."""

LLM_DECIDER_MAX_RETRIES = 0
"""A retry would double the wait a route is meant to save; a failure falls back to the default route instead."""

LLM_DECIDER_TEMPERATURE = 0.0
LLM_DECIDER_SCHEMA_NAME = "route_labels"
"""The ``json_schema.name`` sent with the structured-output request."""

LLM_DECIDER_PROMPT_HEAD = (
    "Label one web request. Request: {query}. asked_on: {asked_on}.\n"
    "For each question pick exactly one choice and give a probability for every choice; they sum to 1."
)
LLM_DECIDER_PROMPT_QUESTION = "\n[{name}] {instructions}"
LLM_DECIDER_PROMPT_CRITERION = "- {choice}: {text}"
"""The prompt is built from ROUTE_QUESTIONS' own instructions and criteria, so it asks what Jev is asked."""

KEYWORDS_URL = r"https?://"
KEYWORDS_RESEARCH = r"\b(compare|identify|list|which .* (and|both)|across|report|determine|find the names)\b"
KEYWORDS_RECENT_MONTH = r"\b(latest|current|now|today|this (week|month|year)|headcount|employees|funding|raised)\b"
KEYWORDS_SYNTHESIS = r"\b(compare|among|both|and then|which of|across|or)\b"
KEYWORDS_RESEARCH_LENGTH = 220
"""Characters. A request longer than this reads as open-ended research rather than a lookup."""
KEYWORDS_MISSING_CONTEXT_WORDS = 2
"""A request of this many words or fewer is taken to be missing context."""
KEYWORDS_CONFIDENCE = {"tool": 0.8, "recent": 0.7, "coverage": 0.7, "well_scoped": 0.6}
"""Fixed per label, not measured: the probability the keyword rules give their pick. The rest of each label's
probability is spread evenly over the other choices, so the distribution sums to 1. The reported confidence is
its normalized one (see :func:`~web_router.decisions.route.normalized_confidence`), the scale Jev reports."""
KEYWORDS_PAID_TOOLS = ("research", "browse")
KEYWORDS_PAID_TOOL_CONFIDENCE = 0.4
"""Fixed, not measured: the probability for a research or browse pick, low enough that its normalized confidence
is below ROUTE_MIN_CONFIDENCE, so a paid method is never picked on keywords alone. classify() records
``low_confidence`` and routes to search unless overridden."""
KEYWORDS_VERSION = "keywords-v1"

LLM_DECIDER_VERSION = "{model}+p{digest}"
LLM_DECIDER_DIGEST_LENGTH = 8
"""The OpenAI decider's version: its model, and a short sha256 of the prompt templates, ROUTE_QUESTIONS and the
answer schema. Two prompts on one model are two deciders, and the record must say which answered."""
