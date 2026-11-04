"""The checks the frozen results run: question ids, and scans that are not one set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks.conftest import frozen_scan as _scan
from tests.benchmarks.conftest import one_frozen_set as _one_set
from web_router.evals import Question


def test_a_question_id_ignores_spacing_and_unicode_form_but_not_wording(frozen_checks: ModuleType) -> None:
    qid = frozen_checks.question_id("Who won the 1998 World Cup?")
    assert qid == frozen_checks.question_id("  Who won the\u00a01998   World Cup?\n")
    assert qid == frozen_checks.question_id("Who won the \uff11998 World Cup?"), "NFKC folds a full-width digit"
    assert qid != frozen_checks.question_id("Who won the 2002 World Cup?")
    assert len(qid) == 16 and set(qid) <= set("0123456789abcdef")


def test_scans_of_other_questions_or_another_judge_are_not_one_set(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "a", ["tavily"], ["q1", "q2"])
    _scan(tmp_path / "b", ["exa"], ["q1", "q3"])
    _scan(tmp_path / "c", ["exa"], ["q1", "q2"], judge="another")
    _scan(tmp_path / "d", ["exa"], ["q1", "q2"], withheld=["other-site.example"])
    _one_set(frozen, monkeypatch, ["a", "b"])
    with pytest.raises(SystemExit, match="asked different questions"):
        frozen.freeze_set("tiny", tmp_path)
    _one_set(frozen, monkeypatch, ["a", "c"])
    with pytest.raises(SystemExit, match="differs on judge"):
        frozen.freeze_set("tiny", tmp_path)
    _one_set(frozen, monkeypatch, ["a", "d"])
    with pytest.raises(SystemExit, match="differs on withheld"):
        frozen.freeze_set("tiny", tmp_path)


def test_scans_graded_on_other_answers_or_cut_short_are_not_one_set(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "a", ["tavily"], ["q1", "q2"])
    _scan(tmp_path / "b", ["exa"], ["q1", "q2"], gold="another gold {q}")
    _one_set(frozen, monkeypatch, ["a", "b"])
    with pytest.raises(SystemExit, match="another gold answer"):
        frozen.freeze_set("tiny", tmp_path)

    _scan(tmp_path / "c", ["exa", "brave"], ["q1", "q2"])
    rows = (tmp_path / "c" / "rows.jsonl").read_text().splitlines()
    (tmp_path / "c" / "rows.jsonl").write_text("\n".join(rows[:-1]) + "\n")  # brave never answered q2
    _one_set(frozen, monkeypatch, ["a", "c"])
    with pytest.raises(SystemExit, match="cut short"):
        frozen.freeze_set("tiny", tmp_path)


def test_a_backend_with_one_question_twice_is_refused(frozen_checks: ModuleType) -> None:
    row = {"provider": "tavily", "mode": "default", "question_id": "abc"}
    with pytest.raises(SystemExit, match="more than once"):
        frozen_checks.check_no_repeats("tiny", [row, {**row}])
    frozen_checks.check_no_repeats("tiny", [row, {**row, "mode": "@deep"}])


def test_one_id_with_two_gold_answers_is_refused(frozen_checks: ModuleType) -> None:
    twice = [Question(question="q1", answer="Paris"), Question(question=" q1 ", answer="Lyon")]
    with pytest.raises(SystemExit, match="share question_id"):
        frozen_checks.question_index(twice, "simpleqa")


def test_a_scan_row_with_no_gold_answer_is_named_as_the_fault(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "a", ["tavily"], ["q1", "q2"], gold="")
    _one_set(frozen, monkeypatch, ["a"])
    with pytest.raises(SystemExit, match="no gold answer"):
        frozen.freeze_set("tiny", tmp_path)


def test_verify_refuses_a_manifest_whose_failed_rows_are_not_the_rows(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "results" / "a", ["tavily", "exa"], ["q1", "q2"])
    rows = [json.loads(line) for line in (tmp_path / "results" / "a" / "rows.jsonl").read_text().splitlines()]
    rows[0] |= {"error": "HTTP 500", "grade": None}
    (tmp_path / "results" / "a" / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    _one_set(frozen, monkeypatch, ["a"])
    manifest = frozen.build(tmp_path / "results", tmp_path / "frozen", {}, check_join=False)
    assert manifest["sets"]["tiny"]["failed_rows"] == {"exa": {"default": 0}, "tavily": {"default": 1}}
    frozen.verify(tmp_path / "frozen")

    manifest["sets"]["tiny"]["failed_rows"]["tavily"]["default"] = 0
    data = (json.dumps(manifest, indent=2) + "\n").encode()
    (tmp_path / "frozen" / frozen.MANIFEST_FILE).write_bytes(data)
    sums = frozen.read_sums(tmp_path / "frozen") | {frozen.MANIFEST_FILE: hashlib.sha256(data).hexdigest()}
    (tmp_path / "frozen" / frozen.SUMS_FILE).write_text("".join(f"{d}  {name}\n" for name, d in sums.items()))
    with pytest.raises(SystemExit, match="failed rows per backend"):
        frozen.verify(tmp_path / "frozen")
