"""The data-driven router's lines: which measured search candidates are tried, and in what order, per preference."""

from __future__ import annotations

from typing import Any

import pytest

from web_router import Budget, RouteRequest
from web_router.routing.contract import Candidate
from web_router.routing.data_driven.lines import line, ranked, search_attempts
from web_router.routing.data_driven.providers import Point

# id, quality, ci95, usd per 1k, median seconds
CHEAP = Point("search:a", 60.0, 1.0, 1.0, 3.0)
MID = Point("search:b", 70.0, 1.0, 5.0, 0.5)
BEATEN = Point("search:c", 65.0, 1.0, 6.0, 4.0)  # b is better, cheaper and faster
BEST = Point("search:d", 80.0, 2.0, 20.0, 2.0)
NEAR = Point("search:e", 77.0, 2.0, 9.0, 1.0)  # overlaps the best within the intervals
POINTS = (BEATEN, BEST, CHEAP, NEAR, MID)


def _ids(points: list[Point]) -> str:
    return "".join(point.id[-1] for point in points)


def test_a_line_keeps_the_points_nothing_beats_on_both_and_rises_in_quality() -> None:
    assert _ids(line(POINTS, lambda p: p.usd_per_1k)) == "abed"
    assert _ids(line(POINTS, lambda p: p.p50_s)) == "bed"
    tied = (Point("search:x", 70.0, 1.0, 5.0, 1.0), MID)
    assert _ids(line(tied, lambda p: p.usd_per_1k)) == "x", "of two at one price and quality, one is kept"


@pytest.mark.parametrize(
    ("prefer", "order"),
    [("cheap", "abed"), ("fast", "bed"), (None, "edba"), ("accurate", "edba")],
)
def test_each_preference_walks_its_line_its_own_way(prefer: Any, order: str) -> None:
    assert _ids(ranked(POINTS, prefer)) == order


def test_the_knee_is_the_cheapest_point_whose_interval_overlaps_the_bests() -> None:
    apart = [point for point in POINTS if point is not NEAR]
    assert _ids(ranked(apart, None)) == "dba", "nothing overlaps the best, so it goes first"
    wide = Point("search:f", 61.0, 20.0, 0.5, 9.0)
    assert _ids(ranked([*POINTS, wide], None)) == "fdeb", "a wide interval at the cheap end reaches the best"
    assert ranked((), None) == ranked((), "cheap") == ranked((), "fast") == []


@pytest.mark.parametrize(("quality", "first"), [(76.0, "search:e"), (75.99, "search:d")])
def test_intervals_that_just_touch_overlap(quality: float, first: str) -> None:
    touching = Point("search:e", quality, 2.0, 9.0, 1.0)  # the best is 80 with 2: the gap of 4 is the two intervals
    assert ranked((BEST, touching), None)[0].id == first


def _req(*candidates: Candidate) -> RouteRequest:
    return RouteRequest(prompt="q", candidates=candidates, deadline_s=1.0)


def test_attempts_are_the_shown_measured_points_that_fit_the_time_limit() -> None:
    shown = (
        Candidate(tool="search", provider="a"),
        Candidate(tool="search", provider="b", measured=False),
        Candidate(tool="search", provider="d"),
        Candidate(tool="search", provider="zzz"),
        Candidate(tool="research", provider="r"),
    )
    assert search_attempts(_req(*shown), POINTS, "cheap") == ["search:a", "search:d"], "an unmeasured b is passed over"
    timed = RouteRequest(prompt="q", candidates=shown, deadline_s=1.0, budget=Budget(time_limit_s=2.0))
    within = RouteRequest(
        prompt="q",
        candidates=(*shown, Candidate(tool="search", provider="e")),
        deadline_s=1.0,
        budget=Budget(time_limit_s=2.0),
    )
    assert search_attempts(within, POINTS, "cheap") == ["search:e", "search:d"], "a's median of 3 s is over the limit"
    assert search_attempts(timed, POINTS, "cheap") is None, "d alone fits, and one is no ranking"


def test_no_attempts_when_the_call_was_shown_none_of_the_points() -> None:
    assert search_attempts(_req(Candidate(tool="search", provider="zzz")), POINTS, None) is None
    assert search_attempts(_req(Candidate(tool="research", provider="a")), POINTS, None) is None
    assert search_attempts(_req(Candidate(tool="search", provider="a")), POINTS, None) is None, "one is no ranking"
