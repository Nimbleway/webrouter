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
NOTE_EXTRACT_URLS = "params.extract.urls"
"""The note for the router's extract URLs when they were refused and the call searches instead."""

MSG_UNKNOWN_ID = "{id} is not a candidate for this call"
MSG_UNKNOWN_TOOL = "{tool} is not a method a router can choose"
MSG_TOOL_NO_CANDIDATE = (
    "{tool} was set aside: no candidate is left for it, since none was offered for this call (the budget or the "
    "configuration left it none) or the router excluded them all"
)
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
URL_IN_TEXT = r"(?:[A-Za-z][A-Za-z0-9+.-]*:)?//[^\s\"`]+"
"""A URL inside free text, such as a router's reason: from its scheme, or from any ``//`` as a scheme-relative
URL, wherever it stands, up to whitespace, a double quote or a backtick. All of it is replaced by its
scheme and host, so nothing after the host survives, brackets and punctuation included: a URL's end cannot be
told safely, and what follows it may be its query. The URL finder, ``web_router._urls``, has its own
pattern on purpose: it finds URLs to fetch, this one URLs to hide."""
HOST_CHARS = r"[A-Za-z0-9.-]+"
"""What a shown host keeps: a host parsed out of free text stops at the first character a hostname cannot hold."""
MSG_PARAM_DEPTH_UNKNOWN_PRICE = "search:{provider} at depth {depth} has no known price, so it never fits a cost cap"
MSG_PARAM_DEPTH_COST = "search:{provider} at depth {depth} is estimated at ${cost:g}, over the ${cap:g} cost cap"
MSG_PARAM_DEPTH_TIME = "search:{provider} at depth {depth} has a p90 of {p90:g}s, over the {cap:g}s time limit"
MSG_PARAM_DEPTH_STRICT = "search:{provider} at depth {depth} has no measured p90, and the budget is strict"
