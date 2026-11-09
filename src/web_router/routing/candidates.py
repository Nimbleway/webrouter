"""The candidates a call offers a router: every (tool, provider) web-router can serve it with.

Experimental. :func:`build_candidates` reads each method's ``auto`` -- the
chain it would try, a configured ``chain`` included -- and keeps the members
its ``_eligible`` keeps, read by :mod:`web_router.routing.eligible`: only those
that map every option the request sets, so a candidate is never one the call
would be silently degraded on -- and that
``auto`` can build, so a member with no credential is never offered. Each carries
its estimated cost and its measured p90 latency by :mod:`.pricing`'s rule, the
one the budget's ledger charges by; neither is ever guessed, so an unknown
stays ``None``.

A budget, the part of it still left, then prefilters them. Under a cost cap a
candidate whose price is unknown, or whose estimate is over what is left, is
dropped. Research is priced at the ``effort`` the caller set, else at the
one effort :func:`~web_router.routing.params.shared_effort` picks for the
whole call, over every eligible backend -- each backend's default when there
is no cap -- so it is offered at the effort it would run with. A request carrying ``provider_options`` may be
billed at another mode than its row, and a floor row bills more on top, so
their price is unknown, and under a cost cap dropped; so is a search asking
for more results than its row's price assumes (``results_assumed``), charged
per result past it. ``provider_options`` make the p90 unknown too. Under a
time cap a known p90 over the time left drops a candidate, and an unknown p90 drops it only on a strict budget. Every
drop is recorded with its reason, for the decision record.

It is on the hot path of every routed call: it reads classes and the cached
price list, builds a member only as ``auto`` does, once per router, and sends nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from web_router.constants import PROVIDER_AUTO
from web_router.constants_candidates import MSG_DROPPED_REFUSES_EFFORT
from web_router.routing.candidates_priced import Found, price_members
from web_router.routing.contract import Budget, Candidate, ToolName, candidate_id
from web_router.routing.effort_call import choose_effort
from web_router.routing.eligible import eligible_members
from web_router.routing.params import EffortChoice
from web_router.types import (
    BrowseRequest,
    ExtractRequest,
    NormalizedRequest,
    Provider,
    ResearchRequest,
    SearchRequest,
    provider_name,
)
from web_router.types_answer import AnswerRequest


@dataclass(frozen=True, kw_only=True)
class CandidateSet:
    """What :func:`build_candidates` found: the candidates to offer, and what the budget left out."""

    candidates: tuple[Candidate, ...] = ()
    """Every (tool, provider) that can serve the call within the budget, each method's in its ``auto`` order."""

    dropped: Mapping[str, str] = field(default_factory=dict)
    """Candidates the budget left out, by id, each with why."""

    unmeasured: tuple[str, ...] = ()
    """The ids of the candidates whose price or p90 is unknown or uncertain, or whose place route-eval did not rank."""

    effort: EffortChoice | None = None
    """The research effort the budget chose for the call, which every research candidate is priced at."""

    requests: Mapping[ToolName, NormalizedRequest] = field(default_factory=dict, repr=False)
    """Per method, the request its candidates were priced at: the router's options are priced on it."""


REQUEST_OF: dict[ToolName, type[NormalizedRequest]] = {
    "search": SearchRequest,
    "extract": ExtractRequest,
    "answer": AnswerRequest,
    "research": ResearchRequest,
    "browse": BrowseRequest,
}
"""Each routable method's request: the one table the routing modules read, both ways."""


def base_params(tool: ToolName, prompt: str, urls: Sequence[str]) -> dict[str, Any]:
    """What ``tool`` is called with for ``prompt``: extract its URLs, browse the task and its first URL, else the
    prompt as the query. Before any option a router or the budget sets."""
    if tool == "extract":
        return {"urls": list(urls)}
    if tool == "browse":
        return {"task": prompt, "start_url": urls[0] if urls else None}
    return {"query": prompt}


def default_requests(prompt: str, urls: Sequence[str] = ()) -> dict[ToolName, NormalizedRequest]:
    """One request per routable method for ``prompt``, as the call that sets no option would send it.

    Extract is left out when there are no ``urls``: it has nothing to fetch.
    """
    return {
        tool: cls(**base_params(tool, prompt, urls)) for tool, cls in REQUEST_OF.items() if tool != "extract" or urls
    }


def build_candidates(
    router: Any,
    requests: Mapping[ToolName, NormalizedRequest],
    *,
    budget: Budget | None = None,
    provider: str | Provider | None = None,
) -> CandidateSet:
    """Every (tool, provider) ``router`` can serve ``requests`` with, within ``budget``.

    Args:
        router: The :class:`~web_router.WebRouter` or
            :class:`~web_router.AsyncWebRouter` making the call; its ``auto``
            for each method, configured ``chain`` and keys included, says which
            backends there are.
        requests: The request each method would be sent, by method. A call to
            one method passes only its own: the router then orders that
            method's backends and no other.
        budget: What is left of the call's budget. ``None`` caps nothing.
        provider: The backend the caller named. One other than ``auto``
            leaves nothing for a router to choose, so there are no candidates.
    """
    if provider is not None and provider_name(provider) != PROVIDER_AUTO:
        return CandidateSet()
    found = Found()
    effort: EffortChoice | None = None
    for tool, request in requests.items():
        eligible, unbuilt = eligible_members(router, tool, request)
        request, chosen = choose_effort([name for name, _ in eligible], request, budget)
        if chosen is not None:  # a backend may refuse the effort for this request: offered only as it will run
            effort, taken = chosen, eligible_members(router, tool, request)[0]
            unbuilt |= {name: MSG_DROPPED_REFUSES_EFFORT.format(effort=chosen.effort) for name, _ in eligible}
            eligible = taken
        found.priced[tool] = request
        price_members(tool, request, eligible, budget, found)
        for name, why in unbuilt.items():
            if (unbuilt_id := candidate_id(tool, name)) not in found.seen:  # built at another place
                found.dropped[unbuilt_id] = why
    return CandidateSet(
        candidates=tuple(found.kept),
        dropped=found.dropped,
        unmeasured=tuple(found.unmeasured),
        effort=effort,
        requests=found.priced,
    )
