"""The decision-gate question sets: ids resolve, overrides apply, and asking for several sets gives each question once.

``benchmarks/agent/questions.py`` is a script beside the package, imported from its folder as the benchmarks do.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from web_router.evals.datasets import Question

AGENT_BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "agent"

WRITTEN = [
    {
        "id": "written-u01",
        "prompt": "How much is it?",
        "answer": "Asks what 'it' is.",
        "asked_on": "2026-10-15",
        "kind": "unclear",
        "topic": "unclear:missing-referent",
        "labels": {"well_scoped": False, "tool_suggestion": None, "recent": None, "coverage": None},
        "source": "written",
    }
]
SEALQA = [
    Question(question="How many A?", answer="3", metadata={"freshness": "never-changing"}),
    Question(question="How many B?", answer="4", metadata={"freshness": "slow-changing"}),
    Question(question="Price now?", answer="9", metadata={"freshness": "fast-changing"}),
]


@pytest.fixture(scope="module")
def questions() -> Iterator[ModuleType]:
    sys.path.insert(0, str(AGENT_BENCH))
    try:
        import questions

        yield questions
    finally:
        sys.path.remove(str(AGENT_BENCH))
        sys.modules.pop("questions", None)


def test_only_stable_sealqa_rows_join_the_pool(questions: ModuleType) -> None:
    pool = questions.build_pool(WRITTEN, SEALQA, {})
    assert questions.sealqa_id("Price now?") not in pool
    assert {r["source"] for r in pool.values()} == {"written", "sealqa"}
    assert len(pool) == 3


def test_an_override_relabels_a_sealqa_row_once(questions: ModuleType) -> None:
    key = questions.sealqa_id("How many A?")
    pool = questions.build_pool(WRITTEN, SEALQA, {key: {"topic": "aggregation", "tool_tag": "research"}})
    assert pool[key]["topic"] == "aggregation"
    assert pool[key]["tool_tag"] == pool[key]["labels"]["tool_suggestion"] == "research"


def test_several_sets_give_each_question_once_in_order(questions: ModuleType) -> None:
    pool = questions.build_pool(WRITTEN, SEALQA, {})
    a, b = questions.sealqa_id("How many A?"), questions.sealqa_id("How many B?")
    sets = {"first": ["written-u01", a], "second": [a, b], "all": questions.ALL_SEALQA}
    rows = questions.resolve(["first", "second", "all"], sets, pool)
    assert [r["id"] for r in rows] == ["written-u01", a, b]


def test_an_unknown_set_names_the_known_ones(questions: ModuleType) -> None:
    with pytest.raises(SystemExit, match="first"):
        questions.resolve(["nope"], {"first": []}, {})


def test_the_committed_sets_resolve_against_the_committed_rows(questions: ModuleType) -> None:
    """Every written id a set names exists; SealQA ids are checked against the pinned download in a live run."""
    written = {json.loads(line)["id"] for line in questions.WRITTEN.read_text().splitlines() if line.strip()}
    config = json.loads(questions.SETS.read_text())
    named = {i for ids in config["sets"].values() if ids != questions.ALL_SEALQA for i in ids}
    assert {i for i in named if not i.startswith(questions.ID_PREFIX)} <= written
    assert set(config["overrides"]) <= named
