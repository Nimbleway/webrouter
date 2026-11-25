"""The data-driven router's regular expressions, copied from the code it was trained with.

``OPENER``'s possessive loop (``)*+``) prevents repeated openers from backtracking exponentially.
The training code includes the same fix; every golden request keeps the same feature values.

Never edit one: the tool table holds counts of requests keyed by what these match, so a changed pattern
moves a request to another row with no error. ``tests/data/data_driven/golden.jsonl.gz`` holds what the training code
returned for two thousand requests, and ``tests/test_data_driven_golden.py`` replays it against these.
``make data-driven-router`` imports a new trained version and fails when these no longer match its code: only
then is this file updated, from that code.
"""

from __future__ import annotations

import re

_I = re.IGNORECASE

UI_ACTION_RE = re.compile(
    r"\b(click|log\s?in|sign\s?(in|up)|add\s+(it\s+|them\s+|\d+\s+\w+\s+)?to\s+(the\s+|my\s+)?(cart|bag|basket|wishlist|list)|"
    r"check\s?out|fill\s+(in|out)|submit|subscribe|register|navigate|browse\s+to|go\s+to|scroll|"
    r"filter|sort\s+by|select|choose|book\s+a|reserve|apply\s+(the\s+)?filter|open\s+(the|a|one))\b",
    _I,
)
PLURAL_RE = re.compile(
    r"\b(all|every|each|list|top\s+\d+|top\s+(five|ten|twenty)|any|which\s+\w+s\b|entries|"
    r"compile|collect|gather|enumerate|count\s+the)\b",
    _I,
)
VERB_CLASSES = [
    ("extract", r"extract|scrape|pull|parse|fetch|crawl|grab|copy"),
    (
        "act",
        r"add|book|buy|purchase|order|subscribe|sign|log|register|click|fill|submit|reserve|apply|open",
    ),
    (
        "navigate",
        r"find|locate|search|look\s+up|look\s+for|browse|navigate|go|check|view|show|visit|identify",
    ),
    (
        "compile",
        r"list|compile|collect|gather|count|track|enumerate|tabulate|give\s+me\s+(a\s+)?list",
    ),
    (
        "analyze",
        r"analy[sz]e|research|conduct|write|summari[sz]e|compare|explain|discuss|evaluate|assess|investigate|review|describe",
    ),
]
OPENER = (
    r"^\W*(please\s+|kindly\s+|can\s+you\s+|could\s+you\s+|"
    r"i\s*(want|need|would\s+like|'d\s+like|wish)\s+(you\s+)?(to\s+)?(help\s+me\s+)?(to\s+)?|"
    r"help\s+me\s+(to\s+)?|let'?s\s+)*+"
)
VERB_RE = [(name, re.compile(rf"{OPENER}({pat})\b", _I)) for name, pat in VERB_CLASSES]
WH_RE = re.compile(
    r"^\W*((in|on|at|for|from|by|during|under|of|to|with)\s+(what|which|whom|whose)\b|"
    r"who|whom|whose|what|when|where|which|why|how|is|are|was|were|do|does|did|can|could|"
    r"has|have|had|will|would|should|according\s+to)\b",
    _I,
)
KW_RESEARCH = (
    r"research|report|analy[sz]e|analysis|compare|comparison|comprehensive|in[-\s]depth|overview|"
    r"literature|trends?|pros\s+and\s+cons|implications?|compile|landscape|survey|"
    r"why|how\s+(do|does|did|can|could)|impact|effects?\s+of|factors|each\s+of|across"
)
KW_RESEARCH_RE = re.compile(rf"\b({KW_RESEARCH})\b", _I)
URL_PARTS_RE = re.compile(r"https?://[^/\s?#]+(?P<path>/[^\s?#]*)?", _I)
ROOT_PATH_RE = re.compile(
    r"^/?((index|home|default)(\.\w+)?|[a-z]{2}(-[a-z]{2})?|[a-z]{2}_[a-z]{2}|index_[a-z]{2})?/?$",
    _I,
)
