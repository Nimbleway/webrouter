"""A scan named before canonical mode ids: its tier labels are frozen as canonical ids, never as depths."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.benchmarks.conftest import frozen_scan, one_frozen_set


@pytest.fixture
def spelling(frozen: ModuleType) -> ModuleType:
    return sys.modules["_frozen_price"]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("tavily:fast", "tavily:tavily-fast"),  # the tier then; a bare `fast` is the depth today
        ("exa:deep", "exa:exa-deep"),
        ("nimble@deep", "nimble:deep"),
        ("tavily:ultra-fast@deep", "tavily:tavily-ultra-fast+deep"),
        ("perplexity", "perplexity"),
    ],
)
def test_a_tier_spelling_scan_is_read_as_canonical_ids(spelling: ModuleType, label: str, expected: str) -> None:
    run = next(iter(spelling.TIER_SPELLING_RUNS))
    assert spelling.canonical_label(run, label) == expected


def test_any_other_scan_keeps_its_labels(spelling: ModuleType) -> None:
    assert spelling.canonical_label("sealqa-hard-254", "tavily:fast") == "tavily:fast"


def test_a_frozen_tier_row_prices_as_the_tier_not_the_depth(
    frozen: ModuleType, spelling: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frozen_scan(tmp_path / "old", ["tavily:fast", "tavily@fast"], ["q1"])
    one_frozen_set(frozen, monkeypatch, ["old"])
    monkeypatch.setattr(spelling, "TIER_SPELLING_RUNS", frozenset({"old"}))

    rows, entry = frozen.freeze_set("tiny", tmp_path)

    assert sorted(r["mode"] for r in rows) == ["fast", "tavily-fast"]
    assert entry["runs"][0]["providers"] == ["tavily:tavily-fast", "tavily:fast"]
    assert {frozen.price_mode("tavily", "search", r["mode"]) for r in rows} == {"fast", "tavily-fast"}
