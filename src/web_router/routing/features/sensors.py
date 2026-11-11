"""What the ``features`` router reads in a prompt: its opening move, a URL, a call for many items, a research word.

Experimental. Each sensor is a regular expression over the prompt alone: no
model, no key, nothing sent. They are the features the method table was
learned on, so changing what one matches changes the method a prompt runs.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from web_router.routing.url_policy import public_only


class Verb(StrEnum):
    """The prompt's opening move: a question, or the class of its leading verb."""

    QUESTION = "question"
    EXTRACT = "extract"
    ACT = "act"
    NAVIGATE = "navigate"
    COMPILE = "compile"
    ANALYZE = "analyze"
    OTHER = "other"


# Polite or framing openers to skip before the leading verb. Each "to" has one place to match, so the repeated
# group cannot backtrack exponentially on "i want to i want to …".
_OPENER = (
    r"^\W*(?:please\s+|kindly\s+|can\s+you\s+|could\s+you\s+|"
    r"i\s*(?:want|need|would\s+like|'d\s+like|wish)\s+(?:you\s+)?(?:to\s+)?(?:help\s+me\s+(?:to\s+)?)?|"
    r"help\s+me\s+(?:to\s+)?|let'?s\s+)*"
)
_VERB_CLASSES = (
    (Verb.EXTRACT, r"extract|scrape|pull|parse|fetch|crawl|grab|copy"),
    (Verb.ACT, r"add|book|buy|purchase|order|subscribe|sign|log|register|click|fill|submit|reserve|apply|open"),
    (Verb.NAVIGATE, r"find|locate|search|look\s+up|look\s+for|browse|navigate|go|check|view|show|visit|identify"),
    (Verb.COMPILE, r"list|compile|collect|gather|count|track|enumerate|tabulate|give\s+me\s+(a\s+)?list"),
    (
        Verb.ANALYZE,
        r"analy[sz]e|research|conduct|write|summari[sz]e|compare|explain|discuss|evaluate|assess|investigate|review|describe",
    ),
)
_VERB_RE = tuple((verb, re.compile(rf"{_OPENER}({pattern})\b", re.IGNORECASE)) for verb, pattern in _VERB_CLASSES)
_WH_RE = re.compile(
    r"^\W*((in|on|at|for|from|by|during|under|of|to|with)\s+(what|which|whom|whose)\b|"
    r"who|whom|whose|what|when|where|which|why|how|is|are|was|were|do|does|did|can|could|"
    r"has|have|had|will|would|should|according\s+to)\b",
    re.IGNORECASE,
)

# Asks for many items (all, every, list, top 10) rather than one.
_PLURAL_RE = re.compile(
    r"\b(all|every|each|list|top\s+\d+|top\s+(five|ten|twenty)|any|which\s+(?!is\b|was\b|has\b|does\b)\w+s\b|entries|"
    r"compile|collect|gather|enumerate|count\s+the)\b",
    re.IGNORECASE,
)

# Words a user tends to write when asking for research: a report, an analysis, a comparison, a why or how.
_KW_RESEARCH_RE = re.compile(
    r"\b(research|report|analy[sz]e|analysis|compare|comparison|comprehensive|in[-\s]depth|overview|"
    r"literature|trends?|pros\s+and\s+cons|implications?|compile|landscape|survey|"
    r"why|how\s+(do|does|did|can|could)|impact|effects?\s+of|factors|each\s+of|across)\b",
    re.IGNORECASE,
)

# What a URL runs over: not whitespace, quotes or a table cell's `|`, not the `](` joining a markdown link's text to
# its target, and not a comma that starts the next URL. Other brackets and commas stay (`?ids[]=3`, `?ids=1,2`).
_URL_CHAR = r"(?:(?!\]\(|,(?:https?://|www\.))[^\s<>\"'`|])"
# An http(s) URL, or a `www.` host with no scheme. The lookbehinds are ASCII-only, so a URL right after CJK text counts.
_SCHEME_OR_WWW = rf"(?<![a-z0-9])https?://{_URL_CHAR}+|(?<![a-z0-9_.@/-])www\.{_URL_CHAR}+"
# A bare host's TLD is lower-case: `Node.JS` and `README.MD` are names, not hosts.
_BARE_DOMAIN = rf"(?<![a-z0-9_.@/-])(?:[a-z0-9-]+\.)+(?-i:[a-z]{{2,}})(?![a-z0-9-])(?:/{_URL_CHAR}*)?"
_URL_RE = re.compile(f"(?i){_SCHEME_OR_WWW}|{_BARE_DOMAIN}")
_SCHEME_RE = re.compile(r"(?i)https?://")
# Ends a sentence or a markdown emphasis rather than a URL. Non-ASCII punctuation (”, …, 。) too.
_TRAILING_PUNCTUATION = ".,;:!?*"
_CLOSER_TO_OPENER = {")": "(", "]": "[", "}": "{"}


def verb(prompt: str) -> Verb:
    """The class of the prompt's leading verb, past any polite opener; else a question, else :attr:`Verb.OTHER`."""
    for found, pattern in _VERB_RE:
        if pattern.match(prompt):
            return found
    return Verb.QUESTION if _WH_RE.match(prompt) else Verb.OTHER


def plural(prompt: str) -> bool:
    """Whether the prompt asks for many items rather than one."""
    return _PLURAL_RE.search(prompt) is not None


def kw_research(prompt: str) -> bool:
    """Whether the prompt holds a word people write when they ask for research."""
    return _KW_RESEARCH_RE.search(prompt) is not None


def find_url(prompt: str) -> str | None:
    """The first URL in the prompt on a public host, ``https://`` added when it has no scheme.

    Wider than the URLs :meth:`~web_router.WebRouter.plan` reads, which need a scheme: a ``www.`` host and a bare
    one (``nfl.com``) count, as they did in the data the table was learned on -- so does a dotted name such as
    ``node.js``.
    """
    for match in _URL_RE.finditer(prompt):
        # Between angle brackets (RFC 3986, appendix C) a URL is delimited already, so its last character is its own.
        delimited = prompt[match.start() - 1 : match.start()] == "<" and prompt[match.end() : match.end() + 1] == ">"
        url = match.group() if delimited else _trimmed(match.group())
        if not _URL_RE.fullmatch(url):
            continue
        url = url if _SCHEME_RE.match(url) else f"https://{url}"
        if public_only(url) is None:
            return url
    return None


def _trimmed(candidate: str) -> str:
    """``candidate`` without what ends the sentence around it: its punctuation, and a bracket it did not open."""
    unclosed = {close: candidate.count(close) - candidate.count(open_) for close, open_ in _CLOSER_TO_OPENER.items()}
    end = len(candidate)
    while end:
        last = candidate[end - 1]
        if unclosed.get(last, 0) > 0:
            unclosed[last] -= 1
        elif not (last in _TRAILING_PUNCTUATION or (not last.isascii() and unicodedata.category(last).startswith("P"))):
            break
        end -= 1
    return candidate[:end]
