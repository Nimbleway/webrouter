"""make chains: the default auto orders ranked from the frozen results, and the committed file kept current."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks.conftest import CHAINS_SIBLINGS, load_script, route_row
from tests.benchmarks.conftest import write_frozen_set as _write_set

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "chains.py"


@pytest.fixture(scope="module")
def chains() -> Iterator[ModuleType]:
    with load_script(SCRIPT, "chains_under_test", CHAINS_SIBLINGS) as module:
        yield module


def _floors(chains: ModuleType, value: float) -> dict[str, object]:
    return {method: chains.Floor(method, value, None) for method in ("search", "answer")}


def _chain_sets(folder: Path) -> None:
    rows = []
    for i in range(20):
        qid = f"q{i:02d}"
        rows.append(route_row("perplexity", "correct" if i % 2 else "incorrect", qid=qid))
        rows.append(route_row("nimble", "correct", qid=qid))
        rows.append(route_row("you-mcp", None, qid=qid, error="rate_limit"))
    _write_set(folder, "searchy", rows)
    _write_set(folder, "answery", [route_row("exa", "correct", qid=f"q{i:02d}", method="answer") for i in range(20)])


def test_the_chain_order_comes_from_the_data_and_unranked_members_keep_their_place(
    chains: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _chain_sets(tmp_path)
    monkeypatch.setattr(chains, "CHAIN_SETS", {"search": "searchy", "answer": "answery"})
    current = {
        "search": {"preferred": ["perplexity", "brave", "nimble"], "appended": ["keenable", "deepseek"]},
        "answer": {"preferred": ["brave", "exa"]},
    }
    out = chains.chain_file(current, folder=tmp_path, floors=_floors(chains, 0.01))

    assert out["search"]["questions"] == 20, "ranked on every question of the set, not a held-out half"
    assert out["search"]["preferred"] == ["nimble", "perplexity", "brave"], "ranked first, then brave unmeasured"
    assert out["search"]["appended"] == ["keenable", "deepseek"]
    assert out["search"]["unmeasured"] == ["brave", "keenable", "deepseek"]
    assert out["answer"]["preferred"] == ["exa", "brave"]
    shuffled = {**current, "search": {"preferred": ["brave", "perplexity", "nimble"], "appended": ["keenable"]}}
    again = chains.chain_file(shuffled, folder=tmp_path, floors=_floors(chains, 0.01))
    assert again["search"]["preferred"] == out["search"]["preferred"], "the input's order never decides a rank"


def test_a_backend_outside_the_chain_cannot_shift_its_tiers_and_a_failed_member_is_named(
    chains: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Among the members alone, b (19/20) and a (18/20) share a tier at a 0.06 floor, so the name puts a first.
    # Ranked with the outsider (20/20), the first tier would be outsider and b, pushing a behind b.
    rows = []
    for i in range(20):
        qid = f"q{i:02d}"
        rows.append(route_row("outsider", "correct", qid=qid))
        rows.append(route_row("b", "correct" if i < 19 else "incorrect", qid=qid))
        rows.append(route_row("a", "correct" if i < 18 else "incorrect", qid=qid))
        rows.append(route_row("you-mcp", None, qid=qid, error="rate_limit"))
    _write_set(tmp_path, "searchy", rows)
    _write_set(tmp_path, "answery", [route_row("exa", "correct", qid=f"q{i:02d}", method="answer") for i in range(20)])
    monkeypatch.setattr(chains, "CHAIN_SETS", {"search": "searchy", "answer": "answery"})
    current = {"search": {"preferred": ["b", "a"], "appended": ["you-mcp"]}, "answer": {"preferred": ["exa"]}}

    out = chains.chain_file(current, folder=tmp_path, floors=_floors(chains, 0.06))

    assert out["search"]["preferred"] == ["a", "b"], "ranked among the chain's members only"
    assert "outsider" not in out["search"]["measured"]
    assert out["search"]["no_row_served"] == ["you-mcp"], "rows that all failed are not 'unmeasured'"
    assert out["search"]["unmeasured"] == []


def test_a_member_counted_on_too_few_questions_keeps_its_place_and_is_named(
    chains: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = []
    for i in range(20):
        qid = f"q{i:02d}"
        rows.append(route_row("steady", "correct" if i % 2 else "incorrect", qid=qid))
        # Right on the five questions it was counted on; the other fifteen hit a rate limit.
        rows.append(route_row("lucky", "correct" if i < 5 else None, qid=qid, error=None if i < 5 else "rate_limit"))
    _write_set(tmp_path, "searchy", rows)
    _write_set(tmp_path, "answery", [route_row("exa", "correct", qid=f"q{i:02d}", method="answer") for i in range(20)])
    monkeypatch.setattr(chains, "CHAIN_SETS", {"search": "searchy", "answer": "answery"})
    current = {"search": {"preferred": ["lucky", "steady"], "appended": []}, "answer": {"preferred": ["exa"]}}

    out = chains.chain_file(current, folder=tmp_path, floors=_floors(chains, 0.01))

    assert out["search"]["preferred"] == ["steady", "lucky"], "ranked first, then the unranked in their place"
    assert out["search"]["too_few_questions"] == ["lucky"] and out["search"]["no_row_served"] == []
    assert list(out["search"]["measured"]) == ["steady"]
    assert out["min_questions"] == chains.MIN_QUESTIONS
    assert out["answer"]["noise_floor"] == {"value": 0.01, "measured_on": None, "rerun": [], "fallback": False}


def test_the_committed_chains_file_is_current(chains: ModuleType) -> None:
    assert chains.emit_chains(check=True) == 0, "run `make chains` and commit src/web_router/data/chains.json"
