"""A :class:`~web_router.RouteRequest` as a decider's question, and its checked ranking as a decision.

Experimental. See :mod:`.decider_router`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from web_router.constants_decider import (
    DECIDER_COST,
    DECIDER_COST_UNKNOWN,
    DECIDER_INSTRUCTIONS,
    DECIDER_LATENCY,
    DECIDER_LATENCY_UNKNOWN,
    DECIDER_MODE_OPTION,
    DECIDER_OPTION,
    DECIDER_PINNED,
    DECIDER_REASON,
    DECIDER_REASON_SCORE,
)
from web_router.routing.contract import Candidate, RouteDecision, RouteRequest, ToolName
from web_router.routing.decider import DeciderQuestion
from web_router.routing.decider_modes import by_score, mode_does


def describe(candidate: Candidate) -> str:
    """One option as a decider reads it: method, backend, its vendor mode and what it does, est. cost and p90."""
    cost, latency = candidate.est_cost_usd, candidate.est_latency_s
    shown = {
        "tool": candidate.tool,
        "provider": candidate.provider,
        "cost": DECIDER_COST_UNKNOWN if cost is None else DECIDER_COST.format(cost=cost),
        "latency": DECIDER_LATENCY_UNKNOWN if latency is None else DECIDER_LATENCY.format(latency=latency),
    }
    if candidate.is_backend or not candidate.mode:  # the second narrows the type: a mode is never ""
        return DECIDER_OPTION.format(**shown)
    return DECIDER_MODE_OPTION.format(**shown, mode=candidate.mode, does=mode_does(candidate.provider, candidate.mode))


def question_for(req: RouteRequest) -> DeciderQuestion:
    """The question for ``req``: every candidate but extract when the prompt names no URL, since a decider
    sets no URL for it to read."""
    options = {c.id: describe(c) for c in req.candidates if c.tool != "extract" or req.urls}
    pinned = DECIDER_PINNED.format(tool=req.pinned_tool) if req.pinned_tool else ""
    return DeciderQuestion(
        prompt=req.prompt,
        instructions=DECIDER_INSTRUCTIONS + pinned,
        options=options,
        budget=req.budget,
        pinned_tool=req.pinned_tool,
        deadline_s=req.deadline_s,
    )


def decision_for(
    req: RouteRequest,
    ranked: Sequence[str],
    scores: Mapping[str, float] | None,
    confidence: float | None,
    name: str,
) -> RouteDecision:
    """``tools`` in the order the ranking first names them, each tool's backends in ranked order; ``attempts``
    instead when a ranked id is a vendor mode (:func:`~.decider_modes.by_score`).

    ``ranked`` is already checked against the offered ids. The reason names the decider and the first id, with
    its score when it has one: never the decider's own words.
    """
    if not ranked:
        return RouteDecision(confidence=confidence)
    by_id = {c.id: c for c in req.candidates}
    if not all(by_id[cid].is_backend for cid in ranked):
        attempts = by_score(by_id, ranked, scores)
        return RouteDecision(attempts=attempts, confidence=confidence, reason=_reason(name, attempts[0], scores))
    providers: dict[ToolName, list[str]] = {}
    for cid in ranked:
        providers.setdefault(by_id[cid].tool, []).append(by_id[cid].provider)
    return RouteDecision(
        tools=list(providers), providers=providers, confidence=confidence, reason=_reason(name, ranked[0], scores)
    )


def _reason(name: str, top: str, scores: Mapping[str, float] | None) -> str:
    reason = DECIDER_REASON.format(name=name, top=top)
    if scores is not None and top in scores:
        reason += DECIDER_REASON_SCORE.format(score=scores[top])
    return reason
