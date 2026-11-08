"""The options a budget sets on a call: research's effort, mapped from what the budget has left.

Experimental. A pure selection with no model and no side effects: it reads the
research effort table (:mod:`web_router.routing.effort_table`, its measured
p90s first) and the price list (:func:`web_router.prices.estimate`), and
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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from web_router import prices
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
from web_router.constants_routing import CANDIDATE_ID_SEPARATOR
from web_router.routing.contract import Budget
from web_router.routing.effort_table import (
    RESEARCH,
    effort_of,
    effort_p90,
    effort_table,
    measured_efforts,
    noise_floor,
)
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

    def record(self, provider: str | Provider) -> dict[str, Any]:
        """The :class:`~web_router.DecisionRecord` fields this choice fills: params, their source, unmeasured."""
        return {
            "params": None if self.effort is None else {"effort": self.effort},
            "params_source": self.source,
            "unmeasured": [f"{RESEARCH}{CANDIDATE_ID_SEPARATOR}{provider_name(provider)}"] if self.unmeasured else None,
        }


def effort_misfit(provider: str | Provider, effort: str | None, budget: Budget) -> str | None:
    """Why research on ``provider`` at ``effort`` does not fit ``budget``, or ``None`` when it fits.

    ``effort=None`` is the call that sets none. Under a cost cap the effort's
    list price must be known and within it; under a time cap its p90 must be
    within it, and an unknown p90 fits unless the budget is ``strict``.
    """
    name = provider_name(provider)
    shown = effort or DEFAULT_EFFORT_NAME
    if budget.max_cost_usd is not None:
        cost = prices.estimate(name, RESEARCH, effort)
        if cost is None:
            return MSG_MISFIT_UNKNOWN_PRICE.format(provider=name, effort=shown)
        if cost > budget.max_cost_usd:
            return MSG_MISFIT_COST.format(provider=name, effort=shown, cost=cost, cap=budget.max_cost_usd)
    if budget.time_limit_s is not None:
        p90 = effort_p90(name, effort)
        if p90 is None and budget.strict:
            return MSG_MISFIT_STRICT.format(provider=name, effort=shown)
        if p90 is not None and p90 > budget.time_limit_s:
            return MSG_MISFIT_TIME.format(provider=name, effort=shown, p90=p90, cap=budget.time_limit_s)
    return None


def _is_measured(provider: str, effort: str | None) -> bool:
    return effort_of(provider, effort) in measured_efforts(provider)


def _default(provider: str, budget: Budget, reason: str) -> EffortChoice:
    """The backend's default effort, unchanged, with whether it fits ``budget``."""
    misfit = effort_misfit(provider, None, budget)
    return EffortChoice(
        effort=None, source=None, reason=reason, misfit=misfit, unmeasured=not _is_measured(provider, None)
    )


def _cheapest_key(provider: str, effort: str) -> tuple[bool, float, float, int]:
    """Cost first, an unknown one last; then p90, an unknown one last; then the lower effort."""
    cost, p90 = prices.estimate(provider, RESEARCH, effort), effort_p90(provider, effort)
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
    if len(measured) < MIN_MEASURED_EFFORTS:
        unmeasured = MSG_EFFORT_UNMEASURED.format(provider=name, min=MIN_MEASURED_EFFORTS, set=effort_table()["set"])
        return _default(name, budget, unmeasured)
    fitting = {e: accuracy for e, accuracy in measured.items() if effort_misfit(name, e, budget) is None}
    if not fitting:
        return _default(name, budget, MSG_EFFORT_NONE_FITS.format(provider=name))
    return _chosen(name, fitting)
