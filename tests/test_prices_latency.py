"""Measured latency in the price list: `make latency` writes it from the frozen benchmark results."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from scripts import latency
from tests.test_prices import _row
from web_router import prices
from web_router.prices import Price


def test_latency_fields_match_the_frozen_results() -> None:
    filled, _ = latency.frozen_latency()
    current = latency.PRICES_JSON.read_text(encoding="utf-8")
    assert latency.regenerate(current, filled) == current, "prices.json's latency is stale: run `make latency`."


def test_estimate_latency_reads_the_row() -> None:
    measured = prices.estimate_latency("webiq", "search")
    assert measured is not None
    p50, p90 = measured
    assert 0 < p50 <= p90
    row = prices.price("webiq", "search")
    assert row is not None and row.latency_n is not None and row.latency_n > 0
    # The SealQA rerun repeats the same questions on two backends only, so it is not pooled.
    assert row.latency_runs == ("sealqa-hard-254", "simpleqa-300-v2")
    # The call that sets no mode reads the default row's latency.
    assert prices.estimate_latency("nimble", "search") == prices.estimate_latency("nimble", "search", "fast")


def test_latency_is_unknown_where_nothing_measured_it() -> None:
    assert prices.estimate_latency("octen", "search", "deep") is None
    assert prices.estimate_latency("tavily", "extract") is None
    assert prices.estimate_latency("parallel", "research") is None
    assert prices.estimate_latency("nobody", "search") is None
    measured = {row.method for row in prices.prices() if row.latency_n is not None}
    assert measured <= {"search", "answer"}


@pytest.mark.parametrize(
    ("method", "mode", "expected"),
    [
        ("search", "default", None),
        *[("search", old, new) for old, new in (("ultra-fast", "tavily-ultra-fast"), ("@deep", "deep"))],
        ("search", "ultra-fast@deep", "tavily-ultra-fast+deep"),
        *[("search", canonical, canonical) for canonical in ("tavily-ultra-fast", "fast", "tavily-fast+deep")],
        ("research", "low", "low"),
        ("research", "default", None),
    ],
)
def test_a_frozen_mode_maps_to_the_price_lists_mode(method: str, mode: str, expected: str | None) -> None:
    assert latency._benchmarks()[0].price_mode("tavily", method, mode) == expected


def _frozen(
    provider: str, mode: str, latency_s: float, error: str | None = None, set_name: str = "a", method: str = "search"
) -> dict[str, object]:
    return {
        "set": set_name,
        "provider": provider,
        "method": method,
        "mode": mode,
        "latency_s": latency_s,
        "error": error,
    }


def test_a_pooled_set_keeps_only_its_pooled_setups() -> None:
    rows = {
        "a": [
            _frozen("tavily", "default", 1.0),
            _frozen("tavily", "ultra-fast@deep", 2.0),
            _frozen("exa", "default", 3.0),
        ],
        "b": [_frozen("tavily", "default", 4.0, set_name="b")],
    }
    kept = latency.pooled_rows(rows, {"a": frozenset({("tavily", "default"), ("tavily", "ultra-fast@deep")})})
    assert [(r["provider"], r["mode"]) for r in kept] == [("tavily", "default"), ("tavily", "ultra-fast@deep")]


def test_measure_pools_sets_skips_failures_and_reports_unmatched() -> None:
    frozen, common = latency._benchmarks()
    runs = {
        ("a", "tavily", "default"): ("run-a", date(2026, 10, 1)),
        ("b", "tavily", "@lite"): ("run-b", date(2026, 10, 9)),
        ("a", "tavily", "ultra-fast@deep"): ("run-a", date(2026, 10, 1)),
    }
    rows = [
        _frozen("tavily", "default", 1.0),
        _frozen("tavily", "default", 9.0, error="timeout"),
        _frozen("tavily", "@lite", 3.0, set_name="b"),
        _frozen("tavily", "@lite", 2.0, set_name="b"),
        _frozen("tavily", "ultra-fast@deep", 0.5),
    ]
    filled, unmatched = latency.measure(rows, runs, pct=common._pct, price_mode=frozen.price_mode)
    # `tavily` (no mode) and `tavily@lite` are the same row, so they pool; the timeout does not count.
    assert filled == {
        ("tavily", "search", "lite"): {
            "latency_p50_s": 2.0,
            "latency_p90_s": 3.0,
            "latency_n": 3,
            "latency_runs": ["run-a", "run-b"],
            "latency_measured": "2026-10-09",
        }
    }
    assert unmatched == {("tavily", "search", "ultra-fast@deep")}


@pytest.mark.parametrize(
    "fields",
    [
        {"latency_p50_s": 1.0},
        {
            "latency_p50_s": 2.0,
            "latency_p90_s": 1.0,
            "latency_n": 5,
            "latency_runs": ["r"],
            "latency_measured": "2026-10-01",
        },
        {
            "latency_p50_s": 1.0,
            "latency_p90_s": 2.0,
            "latency_n": 0,
            "latency_runs": ["r"],
            "latency_measured": "2026-10-01",
        },
        {
            "latency_p50_s": 1.0,
            "latency_p90_s": 2.0,
            "latency_n": 5,
            "latency_runs": [],
            "latency_measured": "2026-10-01",
        },
        {"latency_p50_s": 1.0, "latency_p90_s": 2.0, "latency_n": 5, "latency_runs": ["r"]},
    ],
)
def test_a_malformed_latency_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Price.model_validate(_row(**fields))


def _set(answers: str, *labels: str, method: str = "search") -> dict[str, object]:
    return {"answers_sha256": answers, "method": method, "runs": [{"providers": list(labels)}]}


def test_a_rerun_of_the_same_questions_and_a_mixed_set_are_not_pooled() -> None:
    sets = {
        "sealqa": _set("s", "nimble", "webiq", "exa"),
        "sealqa-twin": _set("s", "nimble", "webiq", "brave"),
        "sealqa-modes": _set("s", "exa", "exa:exa-fast", "tavily:fast"),
        "simpleqa": _set("q", "nimble"),
        "answer-simpleqa": _set("q", "exa", method="answer"),
        "tools-effort": _set("t", "nimble", method="mixed"),
    }
    split_setup = latency._benchmarks()[0].split_setup
    assert latency.pooled_sets(sets, mixed="mixed", split_setup=split_setup) == {
        "sealqa": frozenset({("nimble", "default"), ("webiq", "default"), ("exa", "default")}),
        "sealqa-twin": frozenset({("brave", "default")}),  # only the backend the first run did not have
        # A mode is a setup of its own: new data beside a backend whose default ran before.
        "sealqa-modes": frozenset({("exa", "exa-fast"), ("tavily", "fast")}),
        "simpleqa": frozenset({("nimble", "default")}),
        "answer-simpleqa": frozenset({("exa", "default")}),
    }


def test_measure_reports_an_unmatched_setup_whose_every_call_failed() -> None:
    frozen, common = latency._benchmarks()
    runs = {("a", "tavily", "ultra-fast@deep"): ("run-a", date(2026, 10, 1))}
    rows = [_frozen("tavily", "ultra-fast@deep", 9.0, error="timeout")]
    filled, unmatched = latency.measure(rows, runs, pct=common._pct, price_mode=frozen.price_mode)
    assert filled == {}
    assert unmatched == {("tavily", "search", "ultra-fast@deep")}


def test_measure_leaves_methods_other_than_search_and_answer_alone() -> None:
    frozen, common = latency._benchmarks()
    runs = {("r", "nimble", "default"): ("run-r", date(2026, 10, 1))}
    rows = [
        _frozen("nimble", "default", 30.0, set_name="r", method="research"),
        _frozen("nimble", "default", 2.0, set_name="r", method="extract"),
    ]
    assert prices.price("nimble", "research") is not None and prices.price("nimble", "extract") is not None
    filled, unmatched = latency.measure(rows, runs, pct=common._pct, price_mode=frozen.price_mode)
    assert filled == {}
    assert unmatched == set()
