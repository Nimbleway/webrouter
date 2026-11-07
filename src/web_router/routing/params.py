"""The options a budget sets on a call: research's effort, mapped from what the budget has left.

Experimental. A pure selection with no model and no side effects: it reads the
research effort table (``data/efforts.json``, written by ``make efforts`` from
the frozen ``tools-effort-60`` results), and prices and times each effort by
:mod:`.pricing`'s rule -- the price list, then the table's own p90 -- and
answers which effort to run with and why. Nothing calls it yet; enforcing the
budget is not its job.

Only research's ``effort`` is mapped. Search keeps its own ``search_depth``
and ``max_results``, and timeouts come from the time cap, not from here.

:func:`map_effort` applies :data:`~web_router.constants_params.EFFORT_RULE`:
under a cost or time cap, the cheapest measured effort whose accuracy is within
the noise floor of the most accurate one that fits. An effort the caller set
always stands: when it does not fit, the choice says so in ``misfit`` and
nothing downgrades it. :func:`effort_misfit` is the fit check on its own, for
an effort that came from elsewhere, such as a router's ``params``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from web_router.constants_budget import OVER_COST, OVER_TIME, UNKNOWN_P90, UNKNOWN_PRICE
from web_router.constants_params import (
    DEFAULT_EFFORT_NAME,
    FLOOR_TOLERANCE,
    MIN_MEASURED_EFFORTS,
    MSG_EFFORT_CALLER,
    MSG_EFFORT_CHOSEN,
    MSG_EFFORT_NO_BUDGET,
    MSG_EFFORT_NONE_FITS,
    MSG_EFFORT_UNMEASURED,
    MSG_MISFIT_COST,
    MSG_MISFIT_STRICT,
    MSG_MISFIT_TIME,
    MSG_MISFIT_UNKNOWN_PRICE,
    PARAMS_SOURCE_BUDGET,
    PARAMS_SOURCE_CALLER,
)
from web_router.routing import pricing
from web_router.routing.contract import Budget, candidate_id
from web_router.routing.pricing import RESEARCH, measured_efforts, misfit, mode_cost, mode_p90, research_mode
from web_router.types import Provider, ResearchEffort, provider_name

EFFORTS: tuple[str, ...] = get_args(ResearchEffort)
"""Research efforts, lowest first: the last tie-break prefers the lower."""


@dataclass(frozen=True, kw_only=True)
class EffortChoice:
    """The research effort a call runs with under its budget, who set it, and why."""

    effort: ResearchEffort | None
    """The effort to send; ``None`` sends none, so the backend's default applies, as with no budget."""

    source: Literal["budget", "caller"] | None
    """``budget`` when the mapping chose it, ``caller`` when the caller set it, ``None`` for the default."""

    reason: str
    """Why, in words, for the decision record."""

    misfit: str | None = None
    """Why this effort does not fit the budget; ``None`` when it fits, or with no budget to fit."""

    unmeasured: bool = False
    """The effort's accuracy is not measured: the table has no row for it."""

    def fields(self) -> dict[str, Any]:
        """The :class:`~web_router.DecisionRecord` fields this choice sets on a call: its params and their source."""
        return {"params": None if self.effort is None else {"effort": self.effort}, "params_source": self.source}

    def record(self, provider: str | Provider) -> dict[str, Any]:
        """:meth:`fields`, and ``unmeasured`` naming ``provider`` when the effort's accuracy is not measured."""
        unmeasured = [candidate_id(RESEARCH, provider_name(provider))] if self.unmeasured else None
        return {**self.fields(), "unmeasured": unmeasured}


def noise_floor() -> float:
    """The accuracy gap, as a fraction, two efforts must exceed to be told apart: route-eval's floor for the set."""
    return float(pricing.effort_table()["noise_floor"]["value"])


def effort_misfit(provider: str | Provider, effort: str | None, budget: Budget) -> str | None:
    """Why research on ``provider`` at ``effort`` does not fit ``budget``, or ``None`` when it fits.

    ``effort=None`` is the call that sets none. Under a cost cap the effort's
    list price must be known and within it; under a time cap its p90 must be
    within it, and an unknown p90 fits unless the budget is ``strict``.
    """
    name = provider_name(provider)
    cap, limit = budget.max_cost_usd, budget.time_limit_s
    cost = None if cap is None else mode_cost(name, RESEARCH, effort)
    p90 = None if limit is None else mode_p90(name, RESEARCH, effort)
    code = misfit(cost, p90, cap, limit, budget.strict)
    if code is None:
        return None
    shown_cap = limit if code == OVER_TIME else cap  # the time wording names the time limit as its cap
    return _MISFITS[code].format(provider=name, effort=effort or DEFAULT_EFFORT_NAME, cost=cost, cap=shown_cap, p90=p90)


_MISFITS = {
    UNKNOWN_PRICE: MSG_MISFIT_UNKNOWN_PRICE,
    OVER_COST: MSG_MISFIT_COST,
    UNKNOWN_P90: MSG_MISFIT_STRICT,
    OVER_TIME: MSG_MISFIT_TIME,
}


def _is_measured(provider: str, effort: str | None) -> bool:
    return research_mode(provider, effort) in measured_efforts(provider)


def _default(provider: str, budget: Budget, reason: str) -> EffortChoice:
    """The backend's default effort, unchanged, with whether it fits ``budget``."""
    misfit = effort_misfit(provider, None, budget)
    return EffortChoice(
        effort=None, source=None, reason=reason, misfit=misfit, unmeasured=not _is_measured(provider, None)
    )


def _cheapest_key(provider: str, effort: str) -> tuple[bool, float, float, int]:
    """Cost first, an unknown one last; then p90, an unknown one last; then the lower effort."""
    cost, p90 = mode_cost(provider, RESEARCH, effort), mode_p90(provider, RESEARCH, effort)
    return (cost is None, cost or 0.0, float("inf") if p90 is None else p90, EFFORTS.index(effort))


def _chosen(provider: str, fitting: Mapping[str, float]) -> EffortChoice:
    """The cheapest of ``fitting`` -- effort to accuracy -- within the noise floor of the most accurate."""
    best = max(fitting, key=lambda e: (fitting[e], -EFFORTS.index(e)))
    floor = noise_floor()
    close = [e for e, accuracy in fitting.items() if fitting[best] - accuracy <= floor + FLOOR_TOLERANCE]
    chosen = min(close, key=lambda e: _cheapest_key(provider, e))
    reason = MSG_EFFORT_CHOSEN.format(
        effort=chosen,
        accuracy=fitting[chosen],
        floor=floor * 100,
        best=best,
        best_accuracy=fitting[best],
        provider=provider,
    )
    return EffortChoice(effort=cast(ResearchEffort, chosen), source=PARAMS_SOURCE_BUDGET, reason=reason)


def map_effort(provider: str | Provider, budget: Budget | None, effort: ResearchEffort | None = None) -> EffortChoice:
    """The effort research on ``provider`` runs with under ``budget``, the cap on what the call has left.

    By :data:`~web_router.constants_params.EFFORT_RULE`. ``effort``, when the
    caller set one, is kept as it is; ``misfit`` says when it does not fit.
    With no budget, or one that caps neither cost nor time, nothing changes:
    the effort is ``None`` and the backend's default applies, as today.
    """
    name = provider_name(provider)
    if effort is not None:
        return EffortChoice(
            effort=effort,
            source=PARAMS_SOURCE_CALLER,
            reason=MSG_EFFORT_CALLER.format(effort=effort),
            misfit=None if budget is None else effort_misfit(name, effort, budget),
            unmeasured=not _is_measured(name, effort),
        )
    if budget is None or (budget.max_cost_usd is None and budget.time_limit_s is None):
        return EffortChoice(effort=None, source=None, reason=MSG_EFFORT_NO_BUDGET.format(provider=name))
    measured = {e: float(s["accuracy"]) for e, s in measured_efforts(name).items() if e in EFFORTS}
    fitting = {e: accuracy for e, accuracy in measured.items() if effort_misfit(name, e, budget) is None}
    if len(measured) < MIN_MEASURED_EFFORTS:
        # Too few to rank: the default stands while it fits; when it does not, a measured effort that fits runs.
        unmeasured = MSG_EFFORT_UNMEASURED.format(
            provider=name, min=MIN_MEASURED_EFFORTS, set=pricing.effort_table()["set"]
        )
        default = _default(name, budget, unmeasured)
        return default if default.misfit is None or not fitting else _chosen(name, fitting)
    if not fitting:
        return _default(name, budget, MSG_EFFORT_NONE_FITS.format(provider=name))
    return _chosen(name, fitting)


def shared_effort(providers: Sequence[str], budget: Budget | None) -> EffortChoice | None:
    """The one research effort a call sends, whichever of ``providers`` runs it; ``None`` sends none.

    The request is shared by every backend the call may try, so the effort is
    chosen once, before candidates are priced or a router is asked: the first
    backend's :func:`map_effort` pick, in ``providers``' order, that fits the
    budget on every backend that can run at all -- at its own pick or its
    default. With none that fits all of them, each runs at its own default.
    """
    if budget is None or (budget.max_cost_usd is None and budget.time_limit_s is None):
        return None
    picks = {provider: map_effort(provider, budget) for provider in providers}
    runnable = [provider for provider, pick in picks.items() if effort_misfit(provider, pick.effort, budget) is None]
    for pick in picks.values():
        if pick.effort is not None and all(effort_misfit(p, pick.effort, budget) is None for p in runnable):
            return pick
    return None
