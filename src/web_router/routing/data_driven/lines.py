"""The order the data-driven router tries search's measured candidates in, by what the call prefers.

A *line* holds the points no other point beats on both quality and one cost: price for the cost line, median
latency for the speed line. A point off its line is never tried before one on it: something else is at least as
good and cheaper, or faster.

- ``cheap`` walks the cost line from its cheapest point up.
- ``fast`` walks the speed line from its fastest point up.
- ``accurate``, or no preference, walks the cost line from its best point down, after the *knee*: when cheaper
  points overlap the best one within their confidence intervals, the cheapest of them goes first.

A failed attempt moves to the next point of the same line, never to the other line. After the line, every other
backend runs at its default mode, in ``auto``'s order; one tried in a measured mode is not tried again at its default.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from web_router.routing.contract import Prefer, RouteRequest
from web_router.routing.data_driven.providers import Point


def line(points: Sequence[Point], cost: Callable[[Point], float]) -> list[Point]:
    """The points no other beats on quality and ``cost`` together, cheapest first, so quality rises along it."""
    kept: list[Point] = []
    for point in sorted(points, key=lambda p: (cost(p), -p.quality)):
        if not kept or point.quality > kept[-1].quality:
            kept.append(point)
    return kept


def _overlap(one: Point, other: Point) -> bool:
    return abs(one.quality - other.quality) <= one.ci95 + other.ci95


def ranked(points: Sequence[Point], prefer: Prefer | None) -> list[Point]:
    """``points`` on the line ``prefer`` names, in the order to try them."""
    if prefer == "fast":
        return line(points, lambda p: p.p50_s)
    rising = line(points, lambda p: p.usd_per_1k)
    if prefer == "cheap" or not rising:
        return rising
    best = rising[-1]
    knee = next(point for point in rising if _overlap(point, best))  # the cheapest one as good as the best
    return [knee, *(point for point in reversed(rising) if point is not knee)]


def search_attempts(req: RouteRequest, points: Sequence[Point], prefer: Prefer | None) -> list[str] | None:
    """The measured search candidates the call was shown, in ``prefer``'s order; ``None`` with fewer than two.

    One measured candidate alone is no ranking: putting it first would only move it ahead of ``auto``'s order, so
    that order stays. A candidate with ``measured`` false, a keyless tier or one with its own options, is not the
    backend a row measured, so it is passed over. So is a point whose median latency is over the budget's time
    limit: the price list holds no latency for a tier at a depth, so the budget's own filter does not see it.
    """
    shown = {c.id for c in req.candidates if c.measured}
    limit = req.budget.time_limit_s if req.budget is not None else None
    fits = [point for point in points if point.id in shown and (limit is None or point.p50_s <= limit)]
    return [point.id for point in ranked(fits, prefer)] if len(fits) > 1 else None
