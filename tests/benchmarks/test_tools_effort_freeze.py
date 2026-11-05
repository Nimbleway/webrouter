"""The tool and effort set frozen: a run made into the frozen set, the committed set, and its questions joined."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks._tools_effort_fakes import FakeJudge, FakeRouter, _args, _items
from web_router.evals import Question

# Freezing


def test_a_finished_run_freezes_with_the_reused_answers_and_an_unfinished_one_is_refused(
    frozen: ModuleType,
    tools_effort: ModuleType,
    plan_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items(plan_module)
    monkeypatch.setattr(tools_effort, "load_sample", lambda local: items)
    asyncio.run(tools_effort.run(_args(tmp_path / "run", budget=100.0), FakeJudge(), FakeRouter()))
    easy = items[0].qid
    reused = [
        {
            "set": "answer-simpleqa-300",
            "question_id": qid,
            "provider": provider,
            "method": "answer",
            "mode": "default",
            "grade": "correct",
            "latency_s": 1.0,
            "cost_usd": None,
            "error": None,
        }
        for qid in (easy, "someotherquestion")
        for provider in ("perplexity", "you", "parallel", "exa", "linkup")
    ]
    sets = {"mixed-tiny": {"questions": "tools-effort", "method": frozen.MIXED, "runs": ["run"]}}
    monkeypatch.setattr(frozen, "FROZEN_SETS", sets)

    rows, entry = frozen.freeze_set("mixed-tiny", tmp_path, {"answer-simpleqa-300": reused})
    copied = {r["provider"]: r["cost_usd"] for r in rows if r["method"] == "answer" and r["provider"] != "brave"}
    # A copied answer with no stated cost gets its list price, as the set's own rows do; token-billed stays unknown.
    assert copied == {"linkup": 0.006, "you": 0.005, "exa": 0.005, "perplexity": None, "parallel": None}

    assert len(rows) == len(plan_module.plan(items)) + 5
    assert {r["method"] for r in rows} == {"search+extract", "extract", "answer", "research"}
    assert all(tuple(r) == frozen.FIELDS and r["set"] == "mixed-tiny" for r in rows)
    assert all(r["grade"] == "correct" for r in rows)
    assert entry["question_count"] == 4 and entry["method"] == frozen.MIXED
    assert not any("question" in e for e in entry["sample"])
    with pytest.raises(SystemExit, match="planned rows are missing"):
        frozen.freeze_set("mixed-tiny", tmp_path, {"answer-simpleqa-300": reused[5:]})
    stray = {"question_id": items[0].qid, "path": "research", "provider": "nimble", "mode": "max", "grade": "correct"}
    with (tmp_path / "run" / "rows.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stray) + "\n")
    with pytest.raises(SystemExit, match="1 rows are not in the run's plan"):
        frozen.freeze_set("mixed-tiny", tmp_path, {"answer-simpleqa-300": reused})


def test_join_finds_a_mixed_sets_questions_at_their_rows_in_the_public_sets(
    frozen: ModuleType, plan_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web_router.evals import Dataset

    public = Dataset(name="frames", questions=[Question(question=f"q{i}", answer=f"a{i}") for i in range(3)])
    monkeypatch.setattr(Dataset, "for_set", classmethod(lambda cls, name, path=None: public))
    good = plan_module.Item.make("url-given", "frames", 2, public.questions[2], ["https://a.org"]).entry()
    moved = {**plan_module.Item.make("synthesis", "frames", 1, public.questions[0]).entry()}

    index = frozen.sample_index([good, moved], {})

    assert set(index) == {good["question_id"]}, "a question no longer at its row is left unresolved"
    assert index[good["question_id"]].metadata == {"group": "url-given", "source": "frames", "urls": ["https://a.org"]}


def test_the_tool_and_effort_set_holds_every_planned_row_and_its_failures_are_classed(
    frozen: ModuleType, plan_module: ModuleType
) -> None:
    manifest = json.loads((frozen.FROZEN_DIR / frozen.MANIFEST_FILE).read_text(encoding="utf-8"))
    entry = manifest["sets"]["tools-effort-60"]
    rows = frozen.read_rows(frozen.FROZEN_DIR / ("tools-effort-60" + frozen.SUFFIX))
    keys = {(r["question_id"], r["method"], r["provider"], r["mode"]) for r in rows}

    assert keys == plan_module.expected_keys(entry["sample"])
    methods = {m: sum(r["method"] == m for r in rows) for m in ("search+extract", "extract", "answer", "research")}
    assert methods == {"search+extract": 180, "extract": 10, "answer": 360, "research": 600}
    limited = {(r["provider"], r["mode"]) for r in rows if r["error"] == "rate_limit"}
    assert limited <= {("perplexity", "low"), ("perplexity", "medium"), ("brave", "default")}
