"""Names and messages for a router's preference and its accuracy scores (:mod:`web_router.routing.accuracy`)."""

from __future__ import annotations

PREFERRED_ROUTER_NAME = "preferred"
"""The built-in router that orders every call's backends by its ``prefer``: cheap, fast or accurate."""

PREFERRED_REASON = "{prefer} first"
"""The ``preferred`` router's reason, by its preference: ``"cheap first"``."""

MSG_PREFER_NEEDED = "the preferred router takes prefer='cheap', 'fast' or 'accurate'; got {got!r}."

MSG_ACCURATE_NEEDS_SCORES = (
    "prefer='accurate' ranks by accuracy scores, and this router holds none: pass accuracy= a mapping of method to "
    "{{provider: score}}, or a path to that JSON."
)

MSG_ACCURACY_FILE = "accuracy= could not read {path!r} as JSON: {why}."

MSG_ACCURACY_SHAPE = "accuracy= takes {{method: {{provider or provider:mode: score}}}}; got {got!r} at {where}."

MSG_ACCURACY_METHOD = "accuracy= names {got!r}, not a routed method; known: {known}."

MSG_ACCURACY_PROVIDER = "accuracy= names {got!r} for {method}, not one of its backends."

MSG_ACCURACY_MODE = "accuracy= names mode {got!r} for {provider}, which {why}."

MSG_ACCURACY_MODE_SPELLING = "is spelled {canonical!r}"

MSG_ACCURACY_MODE_UNKNOWN = "it does not offer"

MSG_ACCURACY_SCORE = "accuracy= scores {key!r} for {method} at {got!r}: a score is a finite number from 0 to 1."
