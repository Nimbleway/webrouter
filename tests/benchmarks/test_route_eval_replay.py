"""route-eval's replay of a router over frozen rows, its routers, and the scores it reports -- all offline."""

from __future__ import annotations

import math
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import route_question as _question
from tests.benchmarks.conftest import route_row as _row


def test_a_failed_row_falls_through_to_the_next_charging_its_time_and_its_estimate(route_eval: ModuleType) -> None:
    q = _question(
        route_eval,
        _row("brave", None, latency=0.5, error="timeout"),
        _row("tavily", "correct", latency=1.0),
    )
    outcome = route_eval.replay(lambda _: ["brave", "tavily"], q, route_eval.static_router("search"))

    assert outcome.correct and outcome.served_by == "tavily" and outcome.fallback and not outcome.router_failed
    assert outcome.latency_s == pytest.approx(1.5, abs=0.01)
    assert outcome.cost_usd == pytest.approx(0.013), "tavily's list price, plus brave's for the failed attempt"


def test_a_failed_row_with_no_known_price_makes_the_total_unknown(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("webiq", None, error="timeout"), _row("tavily", "correct"))
    outcome = route_eval.replay(lambda _: ["webiq", "tavily"], q, route_eval.static_router("search"))
    assert outcome.served_by == "tavily" and outcome.cost_usd is None, "webiq has no list price"


def test_a_router_that_raises_or_names_no_candidate_falls_back_to_the_static_order(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))

    def broken(_: Any) -> list[str]:
        raise RuntimeError("router down")

    for router in (broken, lambda _: [], lambda _: ["not-a-backend"]):
        outcome = route_eval.replay(router, q, route_eval.static_router("search"))
        assert outcome.router_failed and outcome.fallback
        assert outcome.served_by == "perplexity", "perplexity comes before tavily in today's order"


def test_a_question_no_backend_served_is_a_miss(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("brave", None, error="http_5xx"))
    outcome = route_eval.replay(lambda _: ["brave"], q, route_eval.static_router("search"))
    assert outcome.served_by is None and not outcome.correct and outcome.fallback


def test_the_routers_own_cost_and_time_are_charged(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("exa", "correct", latency=1.0, cost=0.005, method="answer"))
    outcome = route_eval.replay(lambda _: {"ranked": ["exa"], "cost_usd": 0.001}, q, route_eval.static_router("answer"))
    assert outcome.cost_usd == pytest.approx(0.006), "the stated cost on the row, plus the router's"
    assert outcome.latency_s >= 1.0 and outcome.overhead_s >= 0


def test_the_oracle_takes_the_cheapest_right_backend(route_eval: ModuleType) -> None:
    q = _question(
        route_eval,
        _row("exa", "correct"),
        _row("perplexity", "correct"),
        _row("webiq", "correct"),
        _row("brave", "incorrect"),
    )
    ranked = route_eval.oracle_router("search")(q)
    assert ranked[:3] == ["perplexity", "exa", "webiq"], "perplexity at $0.005, exa at $0.017, webiq unpriced"


def test_score_counts_unknown_cost_apart_rather_than_as_zero(route_eval: ModuleType) -> None:
    outcomes = [
        route_eval.Outcome(True, "a", 1.0, 0.01, False, False, 0.0),
        route_eval.Outcome(False, "b", 3.0, None, True, False, 0.0),
    ]
    s = route_eval.score(outcomes)
    assert s["accuracy"] == 0.5 and s["mean_cost_usd"] == 0.01 and s["cost_unknown"] == 1
    assert s["mean_latency_s"] == 2.0 and s["fallbacks"] == 1


def test_the_verdict_needs_a_margin_wider_than_the_noise_floor(route_eval: ModuleType) -> None:
    assert route_eval.verdict(0.02, 0.012, 0.5, (0.51, 0.53)) == "gain"
    assert route_eval.verdict(0.01, 0.012, 0.5, (0.505, 0.515)) == "no gain (within noise floor)"
    assert route_eval.verdict(-0.02, 0.012, 0.5, (0.47, 0.49)) == "loss"
    assert route_eval.verdict(-0.01, 0.012, 0.5, (0.485, 0.495)) == "no gain (within noise floor)", "nor a loss"
    assert route_eval.verdict(None, 0.012, 0.5, None) == "n/a"


def test_the_verdict_needs_the_static_order_outside_the_routers_interval(route_eval: ModuleType) -> None:
    assert route_eval.verdict(0.1, 0.012, 0.5, (0.42, 0.75)) == "no gain (static inside the 95% interval)"
    assert route_eval.verdict(-0.1, 0.012, 0.5, (0.3, 0.55)) == "no gain (static inside the 95% interval)"


def test_the_static_order_is_autos_own_chain_keyless_tier_before_the_appended_backends(
    route_eval: ModuleType,
) -> None:
    from web_router.constants import AUTO_APPENDED, AUTO_PREFERRED
    from web_router.providers.auto import AutoProvider

    every_key = frozenset((*AUTO_PREFERRED, *AUTO_APPENDED))
    assert route_eval.STATIC_ORDER["search"] == tuple(n for n, _ in AutoProvider.default_chain(every_key))
    order = route_eval.STATIC_ORDER["search"]
    assert order.index("you-mcp") < order.index("keenable") < order.index("deepseek")


def test_the_oracle_sorts_an_unknown_latency_last_rather_than_failing(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("brave", "correct", latency=None), _row("perplexity", "correct", latency=2.0))
    assert route_eval.oracle_router("search")(q)[:2] == ["perplexity", "brave"], "same price; the known time first"


def test_an_unknown_id_in_a_ranking_is_dropped_and_the_rest_kept(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("tavily", "correct"), _row("perplexity", "incorrect"))
    outcome = route_eval.replay(lambda _: ["made-up", "tavily"], q, route_eval.static_router("search"))
    assert not outcome.router_failed and not outcome.fallback
    assert outcome.served_by == "tavily", "the router's ranking stands without the unknown id"


def test_the_candidates_a_router_left_out_follow_in_the_static_order(route_eval: ModuleType) -> None:
    q = _question(
        route_eval,
        _row("brave", None, error="timeout"),
        _row("tavily", "incorrect"),
        _row("perplexity", "correct"),
    )
    outcome = route_eval.replay(lambda _: ["brave"], q, route_eval.static_router("search"))
    assert outcome.served_by == "perplexity" and outcome.fallback and not outcome.router_failed, "then today's order"


def test_a_decision_is_merged_as_a_routed_call_would_merge_it(route_eval: ModuleType) -> None:
    from web_router import RouteDecision

    q = _question(route_eval, _row("tavily", "correct"), _row("perplexity", "incorrect"), _row("brave", "correct"))
    decision = RouteDecision(providers={"search": ["tavily"]}, exclude={"search:perplexity": "slow"})
    outcome = route_eval.replay(lambda _: decision, q, route_eval.static_router("search"))
    assert outcome.served_by == "tavily" and not outcome.router_failed
    invalid = route_eval.replay(lambda _: {"decision": 42}, q, route_eval.static_router("search"))
    assert invalid.router_failed and invalid.served_by == "perplexity", "router_invalid: the static order"


def test_an_unrecorded_latency_is_unknown_not_zero(route_eval: ModuleType) -> None:
    q = _question(route_eval, _row("brave", "correct", latency=None))
    outcome = route_eval.replay(lambda _: ["brave"], q, route_eval.static_router("search"))
    assert outcome.latency_s is None
    known = route_eval.Outcome(True, "a", 2.0, 0.01, False, False, 0.0)
    s = route_eval.score([outcome, known])
    assert s["mean_latency_s"] == 2.0 and s["latency_unknown"] == 1


@pytest.mark.parametrize("error", ["judge", "leaked"])
def test_a_row_the_judge_did_not_grade_still_served_the_question(route_eval: ModuleType, error: str) -> None:
    q = _question(route_eval, _row("tavily", None, latency=1.0, error=error), _row("perplexity", "correct"))
    outcome = route_eval.replay(lambda _: ["tavily", "perplexity"], q, route_eval.static_router("search"))
    assert outcome.served_by == "tavily" and not outcome.fallback, "the backend answered; only grading failed"
    assert outcome.ungraded and not outcome.correct
    assert outcome.cost_usd == pytest.approx(0.008), "an answered call is billed"
    assert route_eval.score([outcome])["ungraded"] == 1


def test_a_router_given_as_a_py_file_path_loads(route_eval: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "my.py").write_text(
        "from web_router import RouteDecision\n"
        "def pick(request):\n    return RouteDecision(providers={'search': ['perplexity']})\n"
    )
    router = route_eval.load_router(f"{tmp_path}/my.py:pick", "search")
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    outcome = route_eval.replay(router, q, route_eval.static_router("search"))
    assert outcome.served_by == "perplexity" and outcome.correct and not outcome.router_failed
    with pytest.raises(SystemExit, match="a router is static, oracle"):
        route_eval.load_router("x", "search")


def test_a_router_file_with_a_dataclass_loads(route_eval: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "picker.py").write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n\n"
        "from web_router import RouteDecision\n\n\n"
        "@dataclass\n"
        "class Picker:\n"
        "    first: str\n\n"
        "    def __call__(self, request):\n"
        "        return {'decision': RouteDecision(providers={'search': [self.first]}), 'cost_usd': 0.0}\n\n\n"
        "pick = Picker('perplexity')\n"
    )
    router = route_eval.load_router(f"{tmp_path}/picker.py:pick", "search")
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    assert route_eval.replay(router, q, route_eval.static_router("search")).served_by == "perplexity"


@pytest.mark.parametrize("cost", [-0.01, float("nan"), float("inf")])
def test_a_router_stating_an_impossible_cost_is_a_router_failure(route_eval: ModuleType, cost: float) -> None:
    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    outcome = route_eval.replay(
        lambda _q: {"ranked": ["perplexity"], "cost_usd": cost}, q, route_eval.static_router("search")
    )
    assert outcome.router_failed and outcome.router_error and "a cost is a finite number" in outcome.router_error
    assert outcome.cost_usd is not None and math.isfinite(outcome.cost_usd)


def test_the_replay_merges_with_the_librarys_own_function(route_eval: ModuleType) -> None:
    import sys

    from web_router.routing.merge import merge

    assert sys.modules["route_eval_merge"].merge is merge


def test_a_decision_ranking_no_candidate_fails_as_the_same_list_does(route_eval: ModuleType) -> None:
    from web_router import RouteDecision

    q = _question(route_eval, _row("tavily", "incorrect"), _row("perplexity", "correct"))
    static = route_eval.static_router("search")
    as_list = route_eval.replay(lambda _: ["bogus"], q, static)
    as_decision = route_eval.replay(lambda _: RouteDecision(providers={"search": ["bogus"]}), q, static)
    assert as_list.router_failed and as_decision.router_failed
    assert as_decision.served_by == as_list.served_by == "perplexity"
    other = route_eval.replay(lambda _: RouteDecision(providers={"answer": ["bogus"]}), q, static)
    assert not other.router_failed, "a ranking for another method names nothing for this one"
