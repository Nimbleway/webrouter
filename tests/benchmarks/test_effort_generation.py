"""make efforts: the research effort table built from a mixed frozen set, and the committed file kept current."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks.conftest import ROUTE_EVAL_SIBLINGS, load_script, route_row
from tests.benchmarks.conftest import write_frozen_set as _write_set
from web_router.constants_params import EFFORT_RULE

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "efforts.py"


@pytest.fixture(scope="module")
def efforts() -> Iterator[ModuleType]:
    with load_script(SCRIPT, "efforts_under_test", ROUTE_EVAL_SIBLINGS) as module:
        yield module


def _research(provider: str, effort: str, grade: str | None, i: int, **extra: object) -> dict[str, object]:
    return route_row(provider, grade, latency=float(i + 1), qid=f"q{i:02d}", method="research", mode=effort, **extra)


def test_each_effort_is_measured_or_named_apart(
    efforts: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = []
    for i in range(20):
        rows.append(_research("acme", "low", "correct" if i % 2 else "incorrect", i))
        rows.append(_research("acme", "high", "correct", i))
        # Counted on five questions; the other fifteen hit a rate limit.
        rows.append(_research("flaky", "low", "correct" if i < 5 else None, i, error=None if i < 5 else "rate_limit"))
        rows.append(_research("broken", "low", None, i, error="timeout"))
        rows.append(route_row("exa", "correct", qid=f"q{i:02d}", method="answer"))
    _write_set(tmp_path, "mixy", rows)
    monkeypatch.setattr(efforts, "EFFORT_SET", "mixy")

    out = efforts.effort_file(folder=tmp_path, floor=efforts.Floor("mixed", 0.01, None))

    acme = out["research"]["acme"]["measured"]
    assert {e: s["accuracy"] for e, s in acme.items()} == {"low": 0.5, "high": 1.0}
    assert acme["high"]["latency_p90_s"] == 18.0, "nearest rank over latencies 1..20"
    assert acme["high"]["latency_p50_s"] == 10.5
    assert out["research"]["flaky"] == {"measured": {}, "too_few_questions": ["low"], "no_row_served": []}
    assert out["research"]["broken"] == {"measured": {}, "too_few_questions": [], "no_row_served": ["low"]}
    assert "exa" not in out["research"], "only research paths"
    assert out["noise_floor"] == {"value": 0.01, "measured_on": None, "rerun": [], "fallback": False}
    assert (out["questions"], out["min_questions"], out["rule"]) == (20, efforts.MIN_QUESTIONS, EFFORT_RULE)


def test_the_floor_is_the_one_route_eval_reports_for_the_set(efforts: ModuleType) -> None:
    floor = efforts.floor_for("mixed")
    assert efforts.effort_file()["noise_floor"] == floor.as_json(6)


def test_the_committed_efforts_file_is_current(efforts: ModuleType) -> None:
    assert efforts.emit_efforts(check=True) == 0, "run `make efforts` and commit src/web_router/data/efforts.json"
