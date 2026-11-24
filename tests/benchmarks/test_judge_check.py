"""A judge that cannot run stops a scan before any backend is asked; a judge host's one blip does not."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


class FlakyJudge:
    """Its key is set, so check() passes; each test grade fails until ``fails`` runs out."""

    label = "litellm:gemini/flaky"

    def __init__(self, fails: int, setup_error: Exception | None = None) -> None:
        self.fails, self.setup_error, self.grades, self.closed = fails, setup_error, 0, False

    def check(self) -> None:
        if self.setup_error:
            raise self.setup_error

    async def agrade(self, question: str, target: str, predicted: str) -> str:
        self.grades += 1
        if self.grades <= self.fails:
            raise RuntimeError("404 - This model is no longer available")
        return "correct"

    async def aclose(self) -> None:
        self.closed = True


def _opened(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a router was opened, so a backend could be asked")


def _wire(module: ModuleType, monkeypatch: pytest.MonkeyPatch, judge: FlakyJudge) -> None:
    monkeypatch.setitem(module.checked_judge.__globals__, "judge_from", lambda spec: judge)
    monkeypatch.setitem(module.checked_judge.__globals__, "PROBE_RETRY_SECONDS", 0)
    monkeypatch.setattr(module, "AsyncWebRouter", _opened)


@pytest.mark.parametrize("which", ["search", "answer"])
async def test_a_scan_stops_on_a_dead_judge_before_any_backend_is_asked(
    which: str, scan: ModuleType, answer_scan: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = scan if which == "search" else answer_scan
    judge = FlakyJudge(fails=2)
    _wire(module, monkeypatch, judge)
    path = tmp_path / "q.csv"
    path.write_text("problem,answer\nWho?,Alice\n", encoding="utf-8")
    args = module.parse_args(["--questions", "csv", "--dataset", str(path), "--providers", "tavily"])
    args.out = tmp_path / "out"

    with pytest.raises(SystemExit, match="no backend was asked: 404 - This model is no longer available"):
        await module.run(args, module.setup_of(args), lambda rows: None)

    assert judge.grades == 2, "tried once more before giving up"
    assert judge.closed


async def test_one_failed_test_grade_is_retried_and_the_judge_kept(
    scan: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = FlakyJudge(fails=1)
    _wire(scan, monkeypatch, judge)
    assert await scan.checked_judge("litellm:gemini/flaky") is judge
    assert judge.grades == 2 and not judge.closed


async def test_a_setup_failure_stops_at_once_with_no_test_grade(
    scan: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = FlakyJudge(fails=0, setup_error=RuntimeError("No API key for provider 'openai'"))
    _wire(scan, monkeypatch, judge)
    with pytest.raises(SystemExit, match="No API key"):
        await scan.checked_judge("openai")
    assert judge.grades == 0 and judge.closed


async def test_a_cancelled_check_closes_the_judge_and_stays_cancelled(
    scan: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = FlakyJudge(fails=0)

    async def cancelled(*args: Any) -> str:
        raise asyncio.CancelledError

    monkeypatch.setattr(judge, "agrade", cancelled)
    _wire(scan, monkeypatch, judge)
    with pytest.raises(asyncio.CancelledError):
        await scan.checked_judge("openai")
    assert judge.closed


@pytest.mark.parametrize("failure", [RuntimeError("original setup failure"), asyncio.CancelledError()])
async def test_cleanup_cannot_replace_the_preflight_failure(
    scan: ModuleType, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    judge = FlakyJudge(fails=0)

    def failed_setup() -> None:
        raise failure

    async def failed_cleanup() -> None:
        raise RuntimeError("cleanup failure")

    monkeypatch.setattr(judge, "check", failed_setup)
    monkeypatch.setattr(judge, "aclose", failed_cleanup)
    _wire(scan, monkeypatch, judge)
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await scan.checked_judge("openai")
    else:
        with pytest.raises(SystemExit, match="original setup failure"):
            await scan.checked_judge("openai")
