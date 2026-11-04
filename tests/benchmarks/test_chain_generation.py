"""make chains: the default auto orders ranked from the frozen results, and the committed file kept current."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks.conftest import ROUTE_EVAL_SIBLINGS, load_script

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "chains.py"


@pytest.fixture(scope="module")
def chains() -> Iterator[ModuleType]:
    with load_script(SCRIPT, "chains_under_test", (*ROUTE_EVAL_SIBLINGS, "route_eval")) as module:
        yield module


def _row(provider: str, grade: str | None, qid: str, method: str = "search", error: str | None = None) -> dict:
    return {
        "question_id": qid,
        "provider": provider,
        "method": method,
        "mode": "default",
        "grade": grade,
        "latency_s": 1.0,
        "cost_usd": None,
        "error": error,
    }


def _write_set(folder: Path, name: str, rows: list[dict[str, Any]]) -> None:
    body = "".join(json.dumps({**r, "set": name}) + "\n" for r in rows).encode()
    (folder / f"{name}.jsonl.gz").write_bytes(gzip.compress(body))


def _chain_sets(folder: Path) -> None:
    rows = []
    for i in range(20):
        qid = f"q{i:02d}"
        rows.append(_row("perplexity", "correct" if i % 2 else "incorrect", qid))
        rows.append(_row("nimble", "correct", qid))
        rows.append(_row("you-mcp", None, qid, error="rate_limit"))
    _write_set(folder, "searchy", rows)
    _write_set(folder, "answery", [_row("exa", "correct", f"q{i:02d}", method="answer") for i in range(20)])


def test_the_chain_order_comes_from_the_data_and_unranked_members_keep_their_place(
    chains: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _chain_sets(tmp_path)
    monkeypatch.setattr(chains, "CHAIN_SETS", {"search": "searchy", "answer": "answery"})
    current = {
        "search": {"preferred": ["perplexity", "brave", "nimble"], "appended": ["keenable", "deepseek"]},
        "answer": {"preferred": ["brave", "exa"]},
    }
    out = chains.chain_file(0.01, current, folder=tmp_path)

    assert out["search"]["questions"] == 20, "ranked on every question of the set, not a held-out half"
    assert out["search"]["preferred"] == ["nimble", "perplexity", "brave"], "ranked first, then brave unmeasured"
    assert out["search"]["appended"] == ["keenable", "deepseek"]
    assert out["search"]["unmeasured"] == ["brave", "keenable", "deepseek"]
    assert out["answer"]["preferred"] == ["exa", "brave"]
    shuffled = {**current, "search": {"preferred": ["brave", "perplexity", "nimble"], "appended": ["keenable"]}}
    again = chains.chain_file(0.01, shuffled, folder=tmp_path)
    assert again["search"]["preferred"] == out["search"]["preferred"], "the input's order never decides a rank"


def test_a_backend_outside_the_chain_cannot_shift_its_tiers_and_a_failed_member_is_named(
    chains: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Among the members alone, b (19/20) and a (18/20) share a tier at a 0.06 floor, so the name puts a first.
    # Ranked with the outsider (20/20), the first tier would be outsider and b, pushing a behind b.
    rows = []
    for i in range(20):
        qid = f"q{i:02d}"
        rows.append(_row("outsider", "correct", qid))
        rows.append(_row("b", "correct" if i < 19 else "incorrect", qid))
        rows.append(_row("a", "correct" if i < 18 else "incorrect", qid))
        rows.append(_row("you-mcp", None, qid, error="rate_limit"))
    _write_set(tmp_path, "searchy", rows)
    _write_set(tmp_path, "answery", [_row("exa", "correct", f"q{i:02d}", method="answer") for i in range(20)])
    monkeypatch.setattr(chains, "CHAIN_SETS", {"search": "searchy", "answer": "answery"})
    current = {"search": {"preferred": ["b", "a"], "appended": ["you-mcp"]}, "answer": {"preferred": ["exa"]}}

    out = chains.chain_file(0.06, current, folder=tmp_path)

    assert out["search"]["preferred"] == ["a", "b"], "ranked among the chain's members only"
    assert "outsider" not in out["search"]["measured"]
    assert out["search"]["no_row_served"] == ["you-mcp"], "rows that all failed are not 'unmeasured'"
    assert out["search"]["unmeasured"] == []


def test_the_committed_chains_file_is_current(chains: ModuleType) -> None:
    assert chains.emit_chains(check=True) == 0, "run `make chains` and commit src/web_router/data/chains.json"
