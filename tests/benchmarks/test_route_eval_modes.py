"""route-eval with ``--modes``: a one-method set's vendor mode rows as candidates of their own, and ``fitted``."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from tests.benchmarks.conftest import route_row as _row
from tests.benchmarks.conftest import write_frozen_set as _write_set
from web_router import RouteDecision, prices


def _moded_set(folder: Path) -> None:
    """40 questions: Perplexity right on 10, Exa's default on 5, its ``exa-deep`` mode on 30."""
    rows = []
    for i in range(40):
        qid = f"q{i:02d}"
        rows.append(_row("perplexity", "correct" if i < 10 else "incorrect", cost=0.005, qid=qid))
        rows.append(_row("exa", "correct" if i < 5 else "incorrect", cost=0.012, qid=qid))
        rows.append(_row("exa", "correct" if i < 30 else "incorrect", cost=0.017, qid=qid, mode="exa-deep"))
    _write_set(folder, "tiny", rows)


def test_a_mode_is_a_candidate_only_with_modes(route_eval: ModuleType, tmp_path: Path) -> None:
    _moded_set(tmp_path)
    sets = sys.modules["route_eval_sets"]
    _, plain = sets.load_set("tiny", tmp_path)
    _, moded = sets.load_set("tiny", tmp_path, modes=True)
    assert plain[0].candidates == ["exa", "perplexity"]
    assert moded[0].candidates == ["exa", "exa:exa-deep", "perplexity"]
    assert moded[0].rows["exa:exa-deep"]["mode"] == "exa-deep"


def test_a_routers_request_names_the_mode_at_its_own_price(route_eval: ModuleType, tmp_path: Path) -> None:
    _moded_set(tmp_path)
    merge = sys.modules["route_eval_merge"]
    _, moded = route_eval.load_set("tiny", tmp_path, modes=True)
    found = {c.id: c for c in merge.request_for(moded[0]).candidates}
    deep = found["search:exa:exa-deep"]
    assert deep.mode == "exa-deep" and not deep.is_backend
    assert deep.est_cost_usd == prices.estimate("exa", "search", "exa-deep") != found["search:exa"].est_cost_usd
    assert merge.method_of(moded[0]) == "search", "a mode's entry is no mixed set's path"


def test_static_never_runs_a_mode_and_a_ranked_one_serves(route_eval: ModuleType, tmp_path: Path) -> None:
    _moded_set(tmp_path)
    _, moded = route_eval.load_set("tiny", tmp_path, modes=True)
    q, static = moded[7], route_eval.static_router("search")
    assert "exa:exa-deep" not in static(q)
    assert route_eval.replay(static, q, static).served_by == "perplexity"
    asked = RouteDecision(attempts=("search:exa:exa-deep", "search:perplexity"))
    outcome = route_eval.replay(lambda _: asked, q, static)
    assert outcome.served_by == "exa:exa-deep" and outcome.correct and outcome.cost_usd == 0.017


def test_fitted_routes_on_modes_only_when_shown_them(route_eval: ModuleType, tmp_path: Path) -> None:
    _moded_set(tmp_path)
    shown = route_eval.evaluate_set("tiny", ["fitted"], floor=0.05, folder=tmp_path, modes=True)
    plain = route_eval.evaluate_set("tiny", ["fitted"], floor=0.05, folder=tmp_path)

    assert shown["modes"] and not plain["modes"]
    assert shown["static_order"] == plain["static_order"] == ["perplexity", "exa"]
    assert {c["path"] for c in shown["routers"]["fitted"]["choices"]} == {"exa:exa-deep"}
    assert {c["path"] for c in plain["routers"]["fitted"]["choices"]} == {"perplexity"}
    held = shown["routers"]["fitted"]["held_out"]
    assert held["accuracy"] > shown["routers"]["static"]["held_out"]["accuracy"]
    assert shown["recommended_order"][0] == "exa:exa-deep"
    assert ", vendor modes as candidates" in route_eval.markdown([shown], 0.05, {})


def test_a_ranking_that_names_a_mode_runs_it(route_eval: ModuleType, tmp_path: Path) -> None:
    _moded_set(tmp_path)
    _, moded = route_eval.load_set("tiny", tmp_path, modes=True)
    q, static = moded[20], route_eval.static_router("search")
    assert route_eval.replay(route_eval.always_router("exa:exa-deep"), q, static).served_by == "exa:exa-deep"
    assert route_eval.replay(route_eval.oracle_router("search"), q, static).served_by == "exa:exa-deep"
    assert route_eval.replay(route_eval.always_router("exa"), q, static).served_by == "exa"
