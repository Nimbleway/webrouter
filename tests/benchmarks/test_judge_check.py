"""A judge that cannot run stops a scan before any backend is asked, in the search scan as in the answer scan."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


class DeadJudge:
    """Its key is set, so check() passes; the model is gone, so the first real call fails."""

    label = "litellm:gemini/retired"
    closed = False

    def check(self) -> None:
        pass

    async def agrade(self, question: str, target: str, predicted: str) -> str:
        raise RuntimeError("404 - This model is no longer available")

    async def aclose(self) -> None:
        type(self).closed = True


def _opened(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a router was opened, so a backend could be searched")


async def test_the_search_scan_stops_on_a_dead_judge_before_any_search(
    scan: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(scan.checked_judge.__globals__, "judge_from", lambda spec: DeadJudge())
    monkeypatch.setattr(scan, "AsyncWebRouter", _opened)
    path = tmp_path / "q.csv"
    path.write_text("problem,answer\nWho?,Alice\n", encoding="utf-8")
    args = scan.parse_args(["--questions", "csv", "--dataset", str(path), "--providers", "tavily"])
    args.out = tmp_path / "out"
    assert isinstance(args, argparse.Namespace)

    with pytest.raises(SystemExit, match="no backend was asked: 404 - This model is no longer available"):
        await scan.run(args, scan.setup_of(args), lambda rows: None)

    assert DeadJudge.closed
