"""The candidates a call offers a router: every (tool, provider) web-router can serve it with.

Experimental. :func:`build_candidates` reads each method's ``auto`` -- the
chain it would try, a configured ``chain`` included -- and keeps the members
its ``_eligible`` keeps: only those that map every option the request sets, so
a candidate is never one the call would be silently degraded on. Each carries
its estimated cost and its measured p90 latency by :mod:`.pricing`'s rule, the
one the budget's ledger charges by; neither is ever guessed, so an unknown
stays ``None``.

A budget, the part of it still left, then prefilters them. Under a cost cap a
candidate whose price is unknown, or whose estimate is over what is left, is
dropped. Research is priced at the ``effort`` the caller set, else at the
one :func:`~web_router.routing.params.map_effort` picks for that backend under
the budget -- the backend's default when there is no cap -- so it is offered at
the effort it would run with. A request carrying ``provider_options`` may be billed at another
mode than its row, and a floor row bills more on top, so their price is
unknown, and under a cost cap dropped. Under a time cap a known p90 over the time left
drops a candidate, and an unknown p90 drops it only on a strict budget. Every
drop is recorded with its reason, for the decision record.

It is on the hot path of every routed call: it reads classes and the cached
price list, builds nothing a call would not, and sends nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from web_router._chains import chains
from web_router.constants import PROVIDER_AUTO
from web_router.constants_candidates import (
    AUTO_GETTERS,
    KEYLESS_OPTION,
    MSG_DROPPED_OVER_COST,
    MSG_DROPPED_OVER_TIME,
    MSG_DROPPED_UNKNOWN_COST,
    MSG_DROPPED_UNKNOWN_LATENCY,
    RANKED_METHODS,
)
from web_router.exceptions import ProviderNotInstalledError, WebRouterError
from web_router.routing.attempts import Attempt
from web_router.routing.contract import Budget, Candidate, ToolName
from web_router.routing.params import map_effort
from web_router.routing.pricing import attempt_cost, attempt_p90
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


def default_requests(prompt: str, urls: Sequence[str] = ()) -> dict[ToolName, NormalizedRequest]:
    """One request per routable method for ``prompt``, as the call that sets no option would send it.

    Extract is left out when there are no ``urls``: it has nothing to fetch.
    Browse starts on the first URL when there is one.
    """
    requests: dict[ToolName, NormalizedRequest] = {"search": SearchRequest(query=prompt)}
    if urls:
        requests["extract"] = ExtractRequest(urls=list(urls))
    requests["answer"] = AnswerRequest(query=prompt)
    requests["research"] = ResearchRequest(query=prompt)
    requests["browse"] = BrowseRequest(task=prompt, start_url=urls[0] if urls else None)
    return requests


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
    kept: list[Candidate] = []
    dropped: dict[str, str] = {}
    unmeasured: list[str] = []
    seen: set[str] = set()
    for tool, request in requests.items():
        for name, keyless in _eligible(router, tool, request):
            cost, p90 = _estimates(tool, name, request, budget)
            candidate = Candidate(tool=tool, provider=name, est_cost_usd=cost, est_latency_s=p90)
            if candidate.id in seen:
                continue  # a member listed twice is one candidate, at its first place
            seen.add(candidate.id)
            if (why := _over_budget(cost, p90, budget)) is not None:
                dropped[candidate.id] = why
                continue
            kept.append(candidate)
            if cost is None or p90 is None or keyless or request.provider_options or name not in _ranked(tool):
                unmeasured.append(candidate.id)
    return CandidateSet(candidates=tuple(kept), dropped=dropped, unmeasured=tuple(unmeasured))


def _eligible(router: Any, tool: ToolName, request: NormalizedRequest) -> list[tuple[str, bool]]:
    """The members of ``tool``'s ``auto`` that can serve ``request``, in chain order, each with whether it is keyless.

    ``auto`` passes over a member that does not map an option the request sets,
    and raises when none is left; here that is a method with no candidates. A
    member whose extra is not installed is one ``auto`` keeps, to fail on; it
    cannot serve, so it is no candidate.
    """
    auto = getattr(router, AUTO_GETTERS[tool])(PROVIDER_AUTO)
    try:
        if isinstance(request, ExtractRequest):
            members = auto._eligible(auto.format_of(request))
        else:
            members = auto._eligible(request)
    except WebRouterError:
        return []
    chain = auto._chain  # the keyless tier is hand-ordered, whatever route-eval ranked the keyed member
    return [
        (name, bool(chain[position][1].get(KEYLESS_OPTION)))
        for position, name in members
        if _serves(tool, name, request)
    ]


def _serves(tool: ToolName, name: str, request: NormalizedRequest) -> bool:
    """Whether ``name`` is installed and maps the options ``auto``'s own check leaves unread.

    Search's and extract's ``_eligible`` keep a member whose class cannot be
    read, and extract's reads only the format.
    """
    from web_router.registry import get_extract_provider_class, get_provider_class

    try:
        if tool == "search":
            get_provider_class(name)
        elif tool == "extract":
            return request.specified_parameters() <= get_extract_provider_class(name).supported_parameters
    except ProviderNotInstalledError:
        return False
    return True


def _estimates(
    tool: ToolName, name: str, request: NormalizedRequest, budget: Budget | None
) -> tuple[float | None, float | None]:
    """The candidate's estimated cost in US dollars and its p90 seconds, by the one pricing rule.

    Research runs at the effort the caller set, else the one the budget picks for this backend.
    """
    if isinstance(request, ResearchRequest) and request.effort is None:
        request = request.model_copy(update={"effort": map_effort(name, budget).effort})
    attempt = Attempt(tool=tool, provider=name, request=request)
    return attempt_cost(attempt), attempt_p90(attempt)


def _over_budget(cost: float | None, p90: float | None, budget: Budget | None) -> str | None:
    """Why ``budget`` leaves the candidate out, or ``None`` when it fits."""
    if budget is None:
        return None
    cap = budget.max_cost_usd
    if cap is not None and cost is None:
        return MSG_DROPPED_UNKNOWN_COST.format(cap=cap)
    if cap is not None and not _fits(cost, cap):
        return MSG_DROPPED_OVER_COST.format(cost=cost, cap=cap)
    limit = budget.time_limit_s
    if limit is not None and p90 is None and budget.strict:
        return MSG_DROPPED_UNKNOWN_LATENCY
    if limit is not None and p90 is not None and p90 > limit:
        return MSG_DROPPED_OVER_TIME.format(p90=p90, limit=limit)
    return None


def _fits(cost: float | None, cap: float) -> bool:
    return cost is not None and cost <= cap


@cache
def _ranked(tool: ToolName) -> frozenset[str]:
    """The members route-eval ranked in ``tool``'s default order; none for a hand-ordered method."""
    if tool not in RANKED_METHODS:
        return frozenset()
    order = chains()[tool]
    return frozenset(order["preferred"]) - frozenset(order.get("unmeasured", ()))
