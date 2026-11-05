"""route-eval end to end: a frozen set replayed, split and reported, and the committed sets -- all offline."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import route_row as _row

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


def test_a_router_that_raises_is_counted_apart_and_its_first_error_printed_once(
    route_eval: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tiny_set(tmp_path / "frozen")
    (tmp_path / "broken_router.py").write_text(
        "def pick(prompt, candidates):\n    raise ValueError('model unreachable')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    summary = route_eval.evaluate_set("tiny", ["broken_router:pick"], floor=0.05, folder=tmp_path / "frozen")
    sys.modules.pop("broken_router", None)

    held_out = summary["routers"]["broken_router:pick"]["held_out"]
    assert held_out["router_failed"] == held_out["questions"] == 10
    err = capsys.readouterr().err
    assert err.count("ValueError: model unreachable") == 1 and "broken_router:pick" in err
    report = route_eval.markdown([summary], 0.05, {})
    assert "Router failed held out" in report and "| 10 | 10 | 0 |" in report


def test_a_cost_unknown_on_every_question_still_shows_how_many(route_eval_report: ModuleType) -> None:
    assert route_eval_report._usd(None, 127) == "unknown (127)"
    assert route_eval_report._usd(0.005, 6) == "$0.0050 (6 unknown)"
    assert route_eval_report._usd(None) == "unknown"


def test_main_writes_the_json_numbers(
    route_eval: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tiny_set(tmp_path / "frozen")
    load_set = route_eval.load_set
    monkeypatch.setattr(route_eval, "load_set", lambda name, _, joined: load_set(name, tmp_path / "frozen", joined))
    out = tmp_path / "numbers.json"
    route_eval.main(["--set", "tiny", "--router", "always:nimble", "--json", str(out)])

    numbers = json.loads(out.read_text(encoding="utf-8"))
    assert numbers["noise_floor"] == route_eval.noise_floor()[0]
    assert [s["set"] for s in numbers["sets"]] == ["tiny"]
    assert list(numbers["sets"][0]["routers"]) == ["static", "oracle", "always:nimble"]
    assert "## `tiny` (search)" in capsys.readouterr().out
