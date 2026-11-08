"""route-eval's noise floor per method: its own rerun's spread, else search's."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks.conftest import route_row as _row
from tests.benchmarks.conftest import write_frozen_set


def test_a_method_with_a_rerun_frozen_is_ranked_with_its_own_floor(
    route_eval_floor: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = [_row("exa", "correct" if i < 10 else "incorrect", qid=f"q{i}", method="answer") for i in range(20)]
    again = [_row("exa", "correct" if i < 14 else "incorrect", qid=f"q{i}", method="answer") for i in range(20)]
    write_frozen_set(tmp_path, "answers", first)
    write_frozen_set(tmp_path, "answers-twin", again)
    monkeypatch.setattr(route_eval_floor, "RERUNS", {**route_eval_floor.RERUNS, "answer": ("answers", "answers-twin")})

    floor = route_eval_floor.floor_for("answer", tmp_path)
    assert floor.value == pytest.approx(0.2) and floor.measured_on == "answer" and not floor.fallback
    assert floor.as_json()["rerun"] == ["answers", "answers-twin"]
    assert "answer's own rerun" in floor.describe()


def test_a_method_with_no_rerun_falls_back_to_the_search_floor_and_says_so(
    route_eval_floor: ModuleType, route_eval_rank: ModuleType
) -> None:
    search, answer = route_eval_floor.floor_for("search"), route_eval_floor.floor_for("answer")
    assert not search.fallback and search.value == route_eval_rank.noise_floor()[0]
    assert answer.fallback and answer.measured_on == "search" and answer.value == search.value
    assert "no rerun of answer is frozen" in answer.describe()
    assert route_eval_floor.Floor.from_json("answer", answer.as_json()) == answer
