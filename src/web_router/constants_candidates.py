"""The candidate builder's fixed values: the getter for each method's ``auto``, and why a candidate is dropped.

Experimental: :mod:`web_router.routing` is new and may change before 1.0.
Kept apart from :mod:`web_router.constants` and :mod:`web_router.constants_routing`,
so neither grows.
"""

from __future__ import annotations

AUTO_GETTERS = {
    "search": "get_provider",
    "extract": "get_extract_provider",
    "answer": "get_answer_provider",
    "research": "get_research_provider",
    "browse": "get_browse_provider",
}
"""Per method, the router method that hands back its ``auto`` -- built once per router and cached there."""

KEYLESS_OPTION = "allow_keyless"
"""The chain option that puts a member on its keyless tier, which route-eval never ranks."""

RANKED_METHODS = ("search", "answer")
"""The methods whose default ``auto`` order route-eval ranked. Extract, research and browse are hand-ordered."""

MSG_DROPPED_UNKNOWN_COST = "no known price, under a cost cap of ${cap:g}"
MSG_DROPPED_OVER_COST = "estimated ${cost:g}, over the ${cap:g} left"
MSG_DROPPED_UNKNOWN_LATENCY = "no measured p90 latency, and the budget is strict"
MSG_DROPPED_OVER_TIME = "p90 latency {p90:g}s, over the {limit:g}s left"
MSG_DROPPED_NOT_CONFIGURED = "not configured: no credential, and no keyless tier to fall back on"
MSG_DROPPED_NOT_INSTALLED = "its optional dependency is not installed"
