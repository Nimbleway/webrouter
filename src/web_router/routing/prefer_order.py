"""A preference's order: candidates by estimated cost (``cheap``), p90 latency (``fast``) or score (``accurate``).

Experimental. The one sort the ``preferred`` and ``cheapest_first`` routers
use. ``cheap`` puts a $0 backend first, then the cheapest; ``fast`` the lowest
p90 first; ``accurate`` the highest score in the router's own scores first
(:mod:`.accuracy`). A candidate with no estimate or score goes last, and ties
keep the order the candidates were offered in, which is the static order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from web_router.routing.accuracy import AccuracyScores, score_of
from web_router.routing.contract import Candidate, Prefer
from web_router.routing.pricing import cost_key


def _estimate(prefer: Prefer, scores: AccuracyScores | None) -> Callable[[Candidate], float | None]:
    """What ``prefer`` sorts a candidate by, lowest first: a score is negated, so the highest leads."""
    if prefer == "cheap":
        return lambda candidate: candidate.est_cost_usd
    if prefer == "fast":
        return lambda candidate: candidate.est_latency_s

    def accurate(candidate: Candidate) -> float | None:
        score = score_of(scores, candidate)
        return None if score is None else -score

    return accurate


def preferred_candidates(
    candidates: Iterable[Candidate], prefer: Prefer, scores: AccuracyScores | None = None
) -> list[Candidate]:
    """``candidates`` by ``prefer``'s estimate, unknowns last and ties as offered."""
    estimate = _estimate(prefer, scores)
    return sorted(candidates, key=lambda candidate: cost_key(estimate(candidate)))
