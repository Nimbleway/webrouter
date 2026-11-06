"""The merge rule's fixed values: the options a router may set, and the notes it records.

Experimental: :mod:`web_router.routing.merge` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants`, so that file stops growing.
"""

from __future__ import annotations

from typing import Final

ROUTER_PARAMS: dict[str, frozenset[str]] = {
    "search": frozenset({"max_results", "search_depth"}),
    "research": frozenset({"effort"}),
    "extract": frozenset({"urls"}),
    "browse": frozenset({"start_url"}),
    "answer": frozenset(),
}
"""Per method, the only options a router may set. Anything else -- ``provider``, ``provider_options``,
``timeout``, ``output_schema``, ``allowed_domains``, ``max_steps`` -- is dropped: ``provider_options`` goes
into the vendor's request as it is, where a budget cannot see what it spends."""

EXTRACT_TOOL: Final = "extract"
"""The method rule 7 moves to ``search`` when it has no URL to read."""

LAST_RESORT_TOOL: Final = "search"
"""The method a call runs when nothing pins one and the router names none that has a candidate."""

ROUTER_TIMEOUT: Final = "router_timeout"
ROUTER_ERROR: Final = "router_error"
ROUTER_INVALID: Final = "router_invalid"
"""Why a decision was set aside for the static order: the router ran out of time, raised, or answered with
something that is not a well-formed ``RouteDecision``."""
ROUTER_EMPTY: Final = "router_empty"
"""The reason recorded for a well-formed decision that decides nothing: the static order, and not a failure."""

NOTE_EXCLUDE = "exclude"
NOTE_EXTRACT = "extract"
"""The keys of the notes that are about the decision as a whole rather than one id or option."""

MSG_UNKNOWN_ID = "{id} is not a candidate for this call"
MSG_UNKNOWN_TOOL = "{tool} is not a method a router can choose"
MSG_EXCLUSIONS_EMPTY = "excluding {ids} would leave {tool} no backend to try, so the exclusions were ignored"
MSG_EXTRACT_NO_URL = "extract was chosen with no URL to read, so the call searches instead"
MSG_PARAM_NOT_ALLOWED = "a router may not set {tool}'s {key}"
MSG_PARAM_CALLER = "the caller set {key}, and a router never changes it"
MSG_PARAM_INVALID = "the router's {key} is not a valid {key} for {tool}"
MSG_PARAM_NO_URL_POLICY = "no URL policy is in force, so a router may not set {key}"
MSG_PARAM_URL_REFUSED = "a URL on {host} is refused by the URL policy: {why}"
MSG_PARAM_UNDECLARED = "{tool}:{provider} does not take {key}"
MSG_PARAM_UNCHECKED = "{tool}:{provider} could not be checked for {key}: {error}"
URL_HIDDEN = "<url>"
"""How a URL with no scheme or host shows in a record: the URL itself never does."""
URL_IN_TEXT = r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"`]+"
"""A URL inside free text, such as a router's reason, up to whitespace, a quote, an angle bracket or a backtick;
:func:`~web_router.routing.merge_params.hide_urls` also ends it at a closing bracket it did not open."""
URL_BRACKETS = {")": "(", "]": "[", "}": "{"}
"""Each closing bracket and its opener: an unopened closer ends a URL in free text, as in ``(see https://a.b)``."""
MSG_PARAM_DEPTH_UNKNOWN_PRICE = "search:{provider} at depth {depth} has no known price, so it never fits a cost cap"
MSG_PARAM_DEPTH_COST = "search:{provider} at depth {depth} is estimated at ${cost:g}, over the ${cap:g} cost cap"
