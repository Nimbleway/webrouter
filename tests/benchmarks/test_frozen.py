"""The frozen results: the committed files against their checksums and manifest, and how a scan becomes one."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import frozen_scan as _scan
from tests.benchmarks.conftest import one_frozen_set as _one_set
from web_router.evals import Question

# The committed files


def test_every_committed_file_matches_its_checksum(frozen: ModuleType) -> None:
    sums = frozen.verify(frozen.FROZEN_DIR)
    expected = {name + frozen.SUFFIX for name in frozen.FROZEN_SETS} | {frozen.MANIFEST_FILE}
    assert set(sums) == expected
    manifest = json.loads((frozen.FROZEN_DIR / frozen.MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["sets"]["sealqa-hard-254"]["failed_rows"]["you-mcp"] == {"default": 254}


def test_every_set_has_the_rows_and_questions_its_manifest_names(frozen: ModuleType, frozen_checks: ModuleType) -> None:
    manifest = json.loads((frozen.FROZEN_DIR / frozen.MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == frozen.SCHEMA_VERSION
    assert manifest["fields"] == list(frozen.FIELDS)
    assert set(manifest["sets"]) == set(frozen.FROZEN_SETS)
    for set_name, entry in manifest["sets"].items():
        rows = frozen.read_rows(frozen.FROZEN_DIR / (set_name + frozen.SUFFIX))
        assert len(rows) == entry["row_count"], set_name
        assert len({r["question_id"] for r in rows}) == entry["question_count"] == entry["resolved"], set_name
        assert sorted({r["provider"] for r in rows}) == entry["providers"], set_name
        mixed = entry["method"] == frozen.MIXED
        if not mixed:
            assert entry["row_count"] == entry["question_count"] * len(entry["providers"]), (
                "one row per question and backend"
            )
        else:
            assert len(entry["sample"]) == entry["question_count"], set_name
        for row in rows:
            assert tuple(row) == frozen.FIELDS, "no field beyond the schema: no question, answer or message text"
            assert row["set"] == set_name
            if mixed:
                assert row["method"] in ("search+extract", "extract", "answer", "research")
            else:
                assert row["method"] == entry["method"]
            assert row["grade"] in (*frozen.GRADES, None)
            assert row["error"] in (*frozen_checks.ERROR_NAMES, None)
            assert len(row["question_id"]) == 16


def test_the_sets_route_eval_needs_are_frozen_at_their_sizes(frozen: ModuleType) -> None:
    manifest = json.loads((frozen.FROZEN_DIR / frozen.MANIFEST_FILE).read_text(encoding="utf-8"))
    sizes = {name: (e["question_count"], e["row_count"]) for name, e in manifest["sets"].items()}
    assert sizes == {
        "sealqa-hard-254": (254, 2540),
        "sealqa-hard-254-twin": (254, 508),
        "simpleqa-300-v2": (300, 3600),
        "answer-simpleqa-300": (300, 1500),
        "tools-effort-60": (60, 1150),
    }


def test_a_changed_file_fails_verification(frozen: ModuleType, tmp_path: Path) -> None:
    for path in frozen.FROZEN_DIR.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    target = tmp_path / ("sealqa-hard-254-twin" + frozen.SUFFIX)
    target.write_bytes(target.read_bytes() + b"\0")
    with pytest.raises(SystemExit, match="does not match SHA256SUMS"):
        frozen.verify(tmp_path)
    target.unlink()
    with pytest.raises(SystemExit, match="is in SHA256SUMS but not in"):
        frozen.verify(tmp_path)


# Errors and modes


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"error": None}, None),
        ({"error": "[x] request timed out after 30s"}, "timeout"),
        ({"error": "[x] HTTP 429: Too Many Requests"}, "rate_limit"),
        ({"error": "[x] MCP call failed: Rate limited by the API"}, "rate_limit"),
        ({"error": "[x] HTTP 402: limit exceeded, upgrade at https://example.com"}, "quota"),
        ({"error": "[x] takes a query of at most 128 characters"}, "invalid_request"),
        ({"error": "[x] HTTP 401: invalid key sk-abc"}, "http_4xx"),
        ({"error": "[x] HTTP 502: <html>Server Error</html>"}, "http_5xx"),
        ({"error": "[x] answer failed: "}, "other"),
        ({"error": "judge said no", "failed_at": "judge"}, "judge"),
        ({"error": "HTTP 429 from the judge", "failed_at": "grading"}, "judge"),
        ({"error": "judge timed out", "failed_at": "synthesis"}, "judge"),
        ({"error": None, "leaked": True}, "leaked"),
    ],
)
def test_an_error_keeps_only_its_class(frozen: ModuleType, row: dict[str, Any], expected: str | None) -> None:
    assert frozen.error_class(row) == expected


def test_a_setup_label_splits_into_provider_and_mode(frozen: ModuleType) -> None:
    assert frozen.split_setup("tavily") == ("tavily", "default")
    assert frozen.split_setup("tavily@deep") == ("tavily", "@deep")
    assert frozen.split_setup("tavily:ultra-fast") == ("tavily", "ultra-fast")
    assert frozen.split_setup("nimble:full-content@lite") == ("nimble", "full-content@lite")


# Building from scans


def test_a_build_keeps_no_text_and_writes_the_same_bytes_twice(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "results" / "a", ["tavily", "exa"], ["q1", "q2"])
    _scan(tmp_path / "results" / "b", ["keenable"], ["q2", "q1"], started="later")
    _one_set(frozen, monkeypatch, ["a", "b"])

    manifest = frozen.build(tmp_path / "results", tmp_path / "one", {}, check_join=False)
    frozen.build(tmp_path / "results", tmp_path / "two", {}, check_join=False)

    assert (tmp_path / "one" / "SHA256SUMS").read_text() == (tmp_path / "two" / "SHA256SUMS").read_text()
    frozen.verify(tmp_path / "one")
    entry = manifest["sets"]["tiny"]
    assert (entry["row_count"], entry["question_count"], entry["providers"]) == (6, 2, ["exa", "keenable", "tavily"])
    raw = (tmp_path / "one" / "tiny.jsonl.gz").read_bytes()
    rows = frozen.read_rows(tmp_path / "one" / "tiny.jsonl.gz")
    assert b"secret" not in raw and not any("secret" in json.dumps(r) for r in rows)
    assert rows[0]["latency_s"] == 1.235 and rows[0]["mode"] == "default"


def test_join_puts_the_questions_back_and_refuses_an_id_it_cannot_resolve(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "results" / "a", ["tavily"], ["q1", "q2"])
    _one_set(frozen, monkeypatch, ["a"])
    frozen.build(tmp_path / "results", tmp_path / "frozen", {}, check_join=False)
    both = [
        Question(question=q, answer=f"secret gold {q}", metadata={"metadata": {"topic": "Art"}}) for q in ("q1", "q2")
    ]

    monkeypatch.setattr(frozen, "load_index", lambda questions, local: frozen.question_index(both, questions))
    assert frozen.join(tmp_path / "frozen", tmp_path / "out", {}) == {"tiny": 2}
    joined = [json.loads(line) for line in (tmp_path / "out" / "tiny.jsonl").read_text().splitlines()]
    assert {(r["question"], r["gold_answer"], r["topic"]) for r in joined} == {
        ("q1", "secret gold q1", "Art"),
        ("q2", "secret gold q2", "Art"),
    }

    monkeypatch.setattr(frozen, "load_index", lambda questions, local: frozen.question_index(both[:1], questions))
    with pytest.raises(SystemExit, match="1 question_id"):
        frozen.join(tmp_path / "frozen", tmp_path / "out", {})


def test_join_downloads_each_dataset_once_and_refuses_changed_answers(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "results" / "a", ["tavily"], ["q1", "q2"])
    sets = {
        "one": {"questions": "simpleqa", "method": "search", "runs": ["a"]},
        "two": {"questions": "simpleqa", "method": "search", "runs": ["a"]},
    }
    monkeypatch.setattr(frozen, "FROZEN_SETS", sets)
    frozen.build(tmp_path / "results", tmp_path / "frozen", {}, check_join=False)
    calls: list[str] = []

    def load(questions: str, answers: str) -> dict[str, Any]:
        calls.append(questions)
        both = [Question(question=q, answer=answers.format(q=q)) for q in ("q1", "q2")]
        return frozen.question_index(both, questions)

    monkeypatch.setattr(frozen, "load_index", lambda questions, local: load(questions, "secret gold {q}"))
    assert frozen.join(tmp_path / "frozen", tmp_path / "out", {}) == {"one": 2, "two": 2}
    assert calls == ["simpleqa"]

    monkeypatch.setattr(frozen, "load_index", lambda questions, local: load(questions, "changed {q}"))
    with pytest.raises(SystemExit, match="changed upstream"):
        frozen.join(tmp_path / "frozen", tmp_path / "out", {})


def test_a_join_that_fails_on_a_later_set_writes_no_file(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "results" / "a", ["tavily"], ["q1", "q2"])
    sets = {
        "one": {"questions": "simpleqa", "method": "search", "runs": ["a"]},
        "two": {"questions": "sealqa-hard", "method": "search", "runs": ["a"]},
    }
    monkeypatch.setattr(frozen, "FROZEN_SETS", sets)
    frozen.build(tmp_path / "results", tmp_path / "frozen", {}, check_join=False)

    def load(questions: str, local: Any) -> dict[str, Any]:
        asked = ("q1", "q2") if questions == "simpleqa" else ("q1",)
        return frozen.question_index([Question(question=q, answer=f"secret gold {q}") for q in asked], questions)

    monkeypatch.setattr(frozen, "load_index", load)
    with pytest.raises(SystemExit, match="1 question_id"):
        frozen.join(tmp_path / "frozen", tmp_path / "out", {})
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_build_refuses_a_dataset_whose_answers_changed_under_the_scans(
    frozen: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _scan(tmp_path / "results" / "a", ["tavily"], ["q1", "q2"])
    _one_set(frozen, monkeypatch, ["a"])
    changed = [Question(question=q, answer=f"changed {q}") for q in ("q1", "q2")]
    monkeypatch.setattr(frozen, "load_index", lambda questions, local: frozen.question_index(changed, questions))
    with pytest.raises(SystemExit, match="changed upstream"):
        frozen.build(tmp_path / "results", tmp_path / "frozen", {})
