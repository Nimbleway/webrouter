"""From candidates and a router's outcome to what a call runs: the method, its backends, its options, its record.

Experimental. The one path a routed call and :meth:`~web_router.WebRouter.plan`
share: :func:`static_order` reads the candidate builder's order -- under a cost
cap, ``auto``'s cost order for each method the router ranks no backend of --
:func:`~web_router.routing.merge.merge` merges the router's decision into it,
and :func:`decided` keeps the research effort the candidates were priced at,
unless the router set one, and writes the decision record, stamped with a fresh
``decision_id``. Nothing here sends or waits.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from web_router._urls import shown
from web_router.constants_merge import ROUTER_TIMEOUT
from web_router.constants_params import PARAMS_SOURCE_ROUTER
from web_router.routing.candidates import CandidateSet
from web_router.routing.composite import members_of, none_answered
from web_router.routing.contract import RouteDecision, RouteRequest, ToolName
from web_router.routing.invoke import RouterOutcome
from web_router.routing.merge import FallbackReason, UrlCheck, merge
from web_router.routing.merge_attempts import ranks, reasoned
from web_router.routing.merge_shape import well_formed
from web_router.routing.record import stamp
from web_router.types_decision import DecisionRecord


@dataclass(frozen=True, kw_only=True)
class Decided:
    """What a call runs: the method, the backends in order, the options a router or the budget set, and the record."""

    tool: ToolName
    providers: tuple[str, ...]
    params: Mapping[str, Any] = field(default_factory=dict)
    """Kept whole, to be called with; the ``repr`` shows each URL as its scheme and host only."""
    record: DecisionRecord = field(default_factory=DecisionRecord)

    def __repr__(self) -> str:
        return f"Decided(tool={self.tool!r}, providers={self.providers!r}, params={shown(self.params)!r})"


def static_order(found: CandidateSet) -> dict[ToolName, list[str]]:
    """Each method's backends by name, in its ``auto`` order: the order with no router. Vendor modes are left out."""
    order: dict[ToolName, list[str]] = {}
    for candidate in found.candidates:
        if candidate.is_backend:
            order.setdefault(candidate.tool, []).append(candidate.provider)
    return order


def _base_order(found: CandidateSet, decision: object) -> dict[ToolName, list[str]]:
    """The order the merge starts from: the static order, and the cost order of each method ``decision`` -- a
    well-formed one, else ``None`` -- ranks no backend of. The default ``rules`` router ranks none, so a plan
    under a cost cap runs cheapest first as an unrouted call does; a router's own ranking is kept as it gave it.
    """
    order = static_order(found)
    providers: Mapping[Any, Any] = getattr(decision, "providers", None) or {}
    attempts = getattr(decision, "attempts", None)
    # Set, a decision's attempts replace its providers: a method is ranked where the merge keeps one of them.
    if attempts:
        excluded = reasoned(found.candidates, getattr(decision, "exclude", None) or {})
        ranked = {t for t in {c.tool for c in found.candidates} if ranks(found.candidates, t, attempts, excluded)}
    else:
        ranked = {t for t, n in providers.items() if n}
    order.update({tool: list(names) for tool, names in found.cost_order.items() if tool not in ranked})
    return order


def decided(
    req: RouteRequest,
    found: CandidateSet,
    outcome: RouterOutcome | None,
    *,
    router: str | None,
    caller_set: Collection[str] = (),
    url_check: UrlCheck | None = None,
    refused: Mapping[str, str] | None = None,
) -> Decided:
    """``outcome`` merged over ``found``'s static order, with the budget's effort, and the record of how.

    ``outcome`` is ``None`` with no router attached: the static order, the
    pinned method or ``search``. ``refused`` is what the URL policy left out
    before any candidate was built, recorded beside the budget's drops.
    """
    decision: object = RouteDecision() if outcome is None else outcome.decision
    failure = None if outcome is None else cast("FallbackReason | None", outcome.failure)
    if failure is None and none_answered(decision) == ROUTER_TIMEOUT:
        failure = ROUTER_TIMEOUT  # a composite out of time: a timeout, its members still recorded
    merged = merge(
        req,
        decision,
        _base_order(found, None if failure is not None or not well_formed(decision) else decision),
        failure=failure,
        caller_set=caller_set,
        url_check=url_check,
        requests=found.requests,
    )
    params = dict(merged.params)
    fields = merged.record()
    effort = found.effort if merged.tool == "research" and "effort" not in params else None
    if effort is not None:
        # The effort the candidates were priced at: unless a router changed it, it is the one that runs.
        if effort.effort is not None:
            params["effort"] = effort.effort
        fields.update(effort.fields(), params=shown(params) or None)
    if outcome is None:
        fields["reason"] = None  # no router: the static order is no router's empty answer
    out = DecisionRecord(
        router=router,
        offered=[candidate.id for candidate in req.candidates],
        dropped={**(refused or {}), **found.dropped} or None,
        unmeasured=list(found.unmeasured) or None,
        router_ms=None if outcome is None else outcome.elapsed_ms,
        members=members_of(decision),
        **stamp(req.prompt),
        **fields,
    )
    return Decided(tool=merged.tool, providers=merged.providers, params=params, record=out)


def router_set(record: DecisionRecord) -> frozenset[str]:
    """The options a router set, by the record a plan carries: an attempt's vendor mode replaces some of them."""
    return frozenset(record.params or ()) if record.params_source == PARAMS_SOURCE_ROUTER else frozenset()
