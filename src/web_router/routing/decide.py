"""From candidates and a router's outcome to what a call runs: the method, its backends, its options, its record.

Experimental. The one path a routed call and :meth:`~web_router.WebRouter.plan`
share: :func:`static_order` reads the candidate builder's order,
:func:`~web_router.routing.merge.merge` merges the router's decision into it,
and :func:`decided` adds the budget's research effort and writes the decision
record, stamped with a fresh ``decision_id``. Nothing here sends or waits.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from web_router.constants_params import PARAMS_SOURCE_BUDGET
from web_router.routing.candidates import CandidateSet
from web_router.routing.contract import Budget, RouteDecision, RouteRequest, ToolName
from web_router.routing.invoke import RouterOutcome
from web_router.routing.merge import FallbackReason, UrlCheck, merge
from web_router.routing.merge_params import shown
from web_router.routing.params import EffortChoice, map_effort
from web_router.routing.record import stamp
from web_router.types_decision import DecisionRecord


@dataclass(frozen=True, kw_only=True)
class Decided:
    """What a call runs: the method, the backends in order, the options a router or the budget set, and the record."""

    tool: ToolName
    providers: tuple[str, ...]
    params: Mapping[str, Any] = field(default_factory=dict)
    record: DecisionRecord = field(default_factory=DecisionRecord)


def static_order(found: CandidateSet) -> dict[ToolName, list[str]]:
    """Each method's candidates by backend name, in its ``auto`` order: the order with no router."""
    order: dict[ToolName, list[str]] = {}
    for candidate in found.candidates:
        order.setdefault(candidate.tool, []).append(candidate.provider)
    return order


def _budget_effort(
    tool: ToolName,
    providers: tuple[str, ...],
    params: dict[str, Any],
    caller_set: Collection[str],
    budget: Budget | None,
) -> EffortChoice | None:
    """The budget's research effort for the first backend, set in ``params``; ``None`` when one is set already."""
    if tool != "research" or not providers or "effort" in params or "effort" in caller_set:
        return None
    choice = map_effort(providers[0], budget)
    if choice.effort is not None:
        params["effort"] = choice.effort
    return choice


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
    merged = merge(req, decision, static_order(found), failure=failure, caller_set=caller_set, url_check=url_check)
    params = dict(merged.params)
    fields = merged.record()
    choice = _budget_effort(merged.tool, merged.providers, params, caller_set, req.budget)
    if choice is not None:
        fields["params_reason"] = choice.reason
        if choice.effort is not None:
            fields.update(params=shown(params), params_source=PARAMS_SOURCE_BUDGET)
    if outcome is None:
        fields["reason"] = None  # no router: the static order is no router's empty answer
    out = DecisionRecord(
        router=router,
        offered=[candidate.id for candidate in req.candidates],
        dropped={**(refused or {}), **found.dropped} or None,
        unmeasured=list(found.unmeasured) or None,
        router_ms=None if outcome is None else outcome.elapsed_ms,
        **stamp(req.prompt),
        **fields,
    )
    return Decided(tool=merged.tool, providers=merged.providers, params=params, record=out)
