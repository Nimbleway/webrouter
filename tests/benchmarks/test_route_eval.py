"""route-eval: the ranking rule, the replay of a router over frozen rows, and the scores it reports -- all offline."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "route_eval.py"
SIBLINGS = ("_common", "_report_tables", "frozen", "_frozen_checks")


@pytest.fixture(scope="module")
def route_eval() -> Iterator[ModuleType]:
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("route_eval_under_test", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered first: its dataclasses look their module up by name.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(SCRIPT.parent))
        for name in (*SIBLINGS, "route_eval_under_test"):
            sys.modules.pop(name, None)


def _row(provider: str, grade: str | None, latency: float = 1.0, cost: float | None = None, **extra: Any) -> dict:
    return {
        "set": "tiny",
        "question_id": extra.get("qid", "q"),
        "provider": provider,
        "method": extra.get("method", "search"),
        "mode": "default",
        "grade": grade,
        "latency_s": latency,
        "cost_usd": cost,
        "error": extra.get("error"),
    }


def _question(route_eval: ModuleType, *rows: dict[str, Any], prompt: str | None = None) -> Any:
    return route_eval.Question(rows[0]["question_id"], prompt, {r["provider"]: r for r in rows})


def _stats(accuracy: float, cost: float | None, latency: float = 1.0) -> dict[str, Any]:
    return {"accuracy": accuracy, "cost_usd": cost, "latency_p50": latency}


# The ranking rule


def test_a_gap_wider_than_the_noise_floor_ranks_by_accuracy_whatever_the_cost(route_eval: ModuleType) -> None:
    stats = {"cheap": _stats(0.80, 0.001), "dear": _stats(0.90, 0.050)}
    assert route_eval.rank_backends(stats, noise_floor=0.02) == ["dear", "cheap"]


def test_within_the_noise_floor_known_cost_then_latency_then_name_decide(route_eval: ModuleType) -> None:
    stats = {
        "best-dear": _stats(0.90, 0.010),
        "cheap": _stats(0.89, 0.005, latency=2.0),
        "cheap-fast": _stats(0.895, 0.005, latency=1.0),
        "b-twin": _stats(0.89, 0.005, latency=2.0),
    }
    assert route_eval.rank_backends(stats, noise_floor=0.02) == ["cheap-fast", "b-twin", "cheap", "best-dear"]


def test_an_unknown_or_free_cost_never_moves_a_backend_up(route_eval: ModuleType) -> None:
    stats = {"priced": _stats(0.88, 0.020), "unknown": _stats(0.90, None), "free": _stats(0.90, 0.0)}
    order = route_eval.rank_backends(stats, noise_floor=0.03)
    assert order[0] == "priced", "inside the floor, a known price beats a missing or $0 one"
    assert set(order[1:]) == {"unknown", "free"}


def test_the_keyless_tier_stays_last_however_well_it_scores(route_eval: ModuleType) -> None:
    stats = {"you-mcp": _stats(0.99, 0.0), "nimble": _stats(0.50, 0.005)}
    assert route_eval.rank_backends(stats, noise_floor=0.01) == ["nimble", "you-mcp"]
    assert "you-mcp" in route_eval.KEYLESS and "tavily" not in route_eval.KEYLESS


# The replay


def test_a_failed_row_falls_through_to_the_next_charging_its_time_but_not_its_cost(route_eval: ModuleType) -> None:
    q = _question(
        route_eval,
        _row("brave", None, latency=0.5, error="timeout"),
        _row("tavily", "correct", latency=1.0),
    )
    outcome = route_eval.replay(lambda _: ["brave", "tavily"], q, route_eval.static_router("search"))

    assert outcome.correct and outcome.served_by == "tavily" and outcome.fallback and not outcome.router_failed
    assert outcome.latency_s == pytest.approx(1.5, abs=0.01)
    assert outcome.cost_usd == pytest.approx(0.008), "tavily's list price, brave's failed call unbilled"


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
    assert route_eval.verdict(0.02, 0.012) == "gain"
    assert route_eval.verdict(0.01, 0.012) == "no gain (within noise floor)"
    assert route_eval.verdict(-0.02, 0.012) == "loss"


# A set end to end


def _write_set(folder: Path, name: str, rows: list[dict[str, Any]]) -> None:
    import gzip

    folder.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps({**r, "set": name}) + "\n" for r in rows).encode()
    (folder / f"{name}.jsonl.gz").write_bytes(gzip.compress(body))


def _tiny_set(folder: Path) -> None:
    rows = []
    for i in range(20):
        qid = f"q{i:02d}"
        rows.append(_row("perplexity", "correct" if i < 10 else "incorrect", qid=qid))
        rows.append(_row("nimble", "correct" if i >= 5 else "incorrect", qid=qid))
    _write_set(folder, "tiny", rows)


def test_a_set_reports_static_oracle_and_a_router_in_sample_and_held_out(
    route_eval: ModuleType, tmp_path: Path
) -> None:
    _tiny_set(tmp_path)
    summary = route_eval.evaluate_set("tiny", ["always:nimble"], floor=0.05, folder=tmp_path)

    assert summary["train"] + summary["held_out"] == 20 and summary["held_out"] == 10
    assert summary["static_order"] == ["perplexity", "nimble"]
    assert not summary["prompts"]
    routers = summary["routers"]
    assert list(routers) == ["static", "oracle", "always:nimble"]
    for part in ("in_sample", "held_out"):
        assert routers["oracle"][part]["accuracy"] == 1.0, "one of the two is right on every question"
        r = routers["always:nimble"][part]
        assert r["regret_oracle"] == pytest.approx(1.0 - r["accuracy"])
        assert r["regret_static"] == pytest.approx(routers["static"][part]["accuracy"] - r["accuracy"])
    assert routers["static"]["verdict"] == "baseline"
    nimble_gain = -routers["always:nimble"]["held_out"]["regret_static"]
    assert routers["always:nimble"]["verdict"] == route_eval.verdict(nimble_gain, 0.05)

    report = route_eval.markdown([summary], 0.05, {})
    assert "Prompt: unavailable" in report and "Recommended order" in report and "Noise floor: 5.0 points" in report


def test_a_custom_router_gets_the_prompt_from_a_joined_folder_and_is_fit_on_the_training_split(
    route_eval: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tiny_set(tmp_path / "frozen")
    joined = tmp_path / "joined"
    joined.mkdir()
    (joined / "tiny.jsonl").write_text(
        "".join(json.dumps({"question_id": f"q{i:02d}", "question": f"question {i}"}) + "\n" for i in range(20))
    )
    module = tmp_path / "my_router.py"
    module.write_text(
        "seen = []\nfitted = []\n"
        "def pick(prompt, candidates):\n    seen.append((prompt, list(candidates)))\n    return ['nimble']\n"
        "def _fit(train):\n    fitted.append(len(train))\n"
        "pick.fit = _fit\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    summary = route_eval.evaluate_set("tiny", ["my_router:pick"], floor=0.05, folder=tmp_path / "frozen", joined=joined)
    import my_router  # type: ignore[import-not-found]

    assert summary["prompts"]
    assert my_router.fitted == [10]
    assert all(p.startswith("question ") and c == ["nimble", "perplexity"] for p, c in my_router.seen)
    assert summary["routers"]["my_router:pick"]["held_out"]["accuracy"] is not None
    sys.modules.pop("my_router", None)


def test_the_split_is_seeded_and_disjoint(route_eval: ModuleType, tmp_path: Path) -> None:
    _tiny_set(tmp_path)
    _, questions = route_eval.load_set("tiny", tmp_path)
    train, test = route_eval.split(questions, 0, 0.5)
    again, _ = route_eval.split(questions, 0, 0.5)
    other, _ = route_eval.split(questions, 1, 0.5)
    assert [q.question_id for q in train] == [q.question_id for q in again]
    assert {q.question_id for q in train}.isdisjoint(q.question_id for q in test)
    assert [q.question_id for q in other] != [q.question_id for q in train]


def test_an_always_router_must_name_a_backend_in_the_set(route_eval: ModuleType, tmp_path: Path) -> None:
    _tiny_set(tmp_path)
    with pytest.raises(SystemExit, match="no rows in tiny"):
        route_eval.evaluate_set("tiny", ["always:brave"], floor=0.05, folder=tmp_path)


# The committed results


def test_the_noise_floor_is_the_twin_runs_widest_gap(route_eval: ModuleType) -> None:
    floor, pairs = route_eval.noise_floor()
    assert set(pairs) == {"nimble", "webiq"}
    assert pairs["webiq"] == (51 / 254, 54 / 254)
    assert floor == pytest.approx(3 / 254)


def test_the_committed_sets_replay_offline(route_eval: ModuleType) -> None:
    floor, _ = route_eval.noise_floor()
    summary = route_eval.evaluate_set("sealqa-hard-254", ["always:webiq"], floor=floor)
    assert summary["static_order"][0] == "perplexity" and summary["static_order"][-1] == "you-mcp"
    assert "you-mcp" not in summary["recommended_order"], "it served none of SealQA"
    oracle = summary["routers"]["oracle"]["held_out"]["accuracy"]
    assert all(r["held_out"]["accuracy"] <= oracle for r in summary["routers"].values())


@pytest.mark.parametrize("fraction", [0.0, 1.0])
def test_a_split_that_leaves_either_side_empty_is_refused(
    route_eval: ModuleType, tmp_path: Path, fraction: float
) -> None:
    _tiny_set(tmp_path)
    with pytest.raises(SystemExit, match="both must be non-empty"):
        route_eval.evaluate_set("tiny", [], test_fraction=fraction, floor=0.05, folder=tmp_path)


def test_the_report_shows_n_a_for_numbers_an_empty_score_cannot_have(route_eval: ModuleType) -> None:
    empty = {**route_eval.score([]), "regret_oracle": None, "regret_static": None}
    summary = {
        "set": "tiny",
        "method": "search",
        "questions": 0,
        "train": 0,
        "held_out": 0,
        "seed": 0,
        "prompts": True,
        "static_order": [],
        "routers": {"static": {"in_sample": empty, "held_out": empty, "verdict": "baseline"}},
        "backends": {},
        "recommended_order": [],
    }
    assert "| n/a | baseline |" in route_eval.markdown([summary], 0.01, {})
