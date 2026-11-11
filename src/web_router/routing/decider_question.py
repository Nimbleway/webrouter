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
    DECIDER_OPTION,
    DECIDER_PINNED,
    DECIDER_REASON,
    DECIDER_REASON_SCORE,
)
from web_router.routing.contract import Candidate, RouteDecision, RouteRequest, ToolName
from web_router.routing.decider import DeciderQuestion


def describe(candidate: Candidate) -> str:
    """One option as a decider reads it: method, backend, estimated cost and p90 latency."""
    cost, latency = candidate.est_cost_usd, candidate.est_latency_s
    return DECIDER_OPTION.format(
        tool=candidate.tool,
        provider=candidate.provider,
        cost=DECIDER_COST_UNKNOWN if cost is None else DECIDER_COST.format(cost=cost),
        latency=DECIDER_LATENCY_UNKNOWN if latency is None else DECIDER_LATENCY.format(latency=latency),
    )


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
    """``tools`` in the order the ranking first names them, each tool's backends in ranked order.

    ``ranked`` is already checked against the offered ids. The reason names the decider and the first id, with
    its score when it has one: never the decider's own words.
    """
    if not ranked:
        return RouteDecision(confidence=confidence)
    by_id = {c.id: c for c in req.candidates}
    providers: dict[ToolName, list[str]] = {}
    for cid in ranked:
        providers.setdefault(by_id[cid].tool, []).append(by_id[cid].provider)
    top = ranked[0]
    reason = DECIDER_REASON.format(name=name, top=top)
    if scores is not None and top in scores:
        reason += DECIDER_REASON_SCORE.format(score=scores[top])
    return RouteDecision(tools=list(providers), providers=providers, confidence=confidence, reason=reason)
