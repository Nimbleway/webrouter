"""router-latency, offline: the command line, the dry run, the refusals and a run's cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.benchmarks._router_latency_fakes import (
    Closing,
    latency_fixture,  # noqa: F401 -- the latency fixture
    module,
    req,
)
from web_router import RouteDecision, RouteRequest


class _LoopBound:
    """An async router holding something of the loop it was first called on, as an SDK's kept-alive connection is."""

    timeout_s = 1.0

    def __init__(self, loops: list[asyncio.AbstractEventLoop], closed: list[asyncio.AbstractEventLoop]) -> None:
        self.loops, self.closed = loops, closed

    async def aroute(self, req: RouteRequest) -> RouteDecision:
        self.loops.append(asyncio.get_running_loop())
        return RouteDecision()

    async def aclose(self) -> None:
        self.closed.append(asyncio.get_running_loop())


def test_an_async_run_calls_and_closes_every_router_on_one_loop(latency: ModuleType) -> None:
    # The LLM example's AsyncOpenAI client printed "RuntimeError: Event loop is closed" at exit when a run's
    # phases each had a loop of their own: its connections, opened on one, were collected after it closed.
    loops: list[asyncio.AbstractEventLoop] = []
    closed: list[asyncio.AbstractEventLoop] = []
    spec = module("router_latency_routers").RouterSpec("bound", "bound", lambda: _LoopBound(loops, closed))
    args = latency.parse_args(["--async", "-n", "2", "--warmup", "1", "--cold", "2", "--e2e", "2"])
    latency.run(args, [spec], [req(), req("who lost")])
    assert len(set(loops)) == 1 and len(loops) > 4
    assert set(closed) == set(loops) and len(closed) == 3  # the shared router, then the two cold builds
    assert loops[0].is_closed()


def test_a_router_named_twice_is_refused(latency: ModuleType) -> None:
    with pytest.raises(SystemExit, match="more than once"):
        latency.main(["--router", "rules", "--router", "rules", "--dry-run"])


def test_the_dry_run_counts_calls_and_prices_the_llm_without_calling(
    latency: ModuleType, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(latency, "ENV_FILE", Path("/nonexistent/.env"))
    latency.main(["--router", "rules", "--router", "llm", "--llm-model", "m", "--llm-price", "1,2", "--dry-run",
                  "--set", "tools-effort-60", "-n", "10", "--warmup", "1", "--cold", "2", "--e2e", "3"])  # fmt: skip
    out = capsys.readouterr().out
    assert "calls to the router: 18" in out  # 1 + 1 + 10 + 2 + 1 + 3
    assert "missing: OPENAI_API_KEY" in out
    assert out.count("$0.0000") == 1 and "unknown" not in out.split("- llm")[1].split("\n")[2]


def test_a_real_run_needs_the_question_text(latency: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"frozen\.py join"):
        latency.requests(["tools-effort-60"], tmp_path, need_prompts=True)


@pytest.mark.parametrize(
    "args",
    [
        ["-n", "-1"],
        ["--llm-price", "-1,1"],
        ["--llm-price", "nan,1"],
        ["--jev-price", "inf"],
        ["--e2e", "0"],
        ["--remote-rtt-ms", "-5"],
        ["--remote-rtt-ms", "inf"],
        ["--deadline-s", "0"],
        ["--deadline-s", "nan"],
        ["--share", "nan"],
        ["--share", "1.5"],
    ],
)
def test_a_negative_count_or_a_bad_price_is_refused(latency: ModuleType, args: list[str]) -> None:
    with pytest.raises(SystemExit):
        latency.parse_args(args)


def test_sets_that_give_no_questions_are_refused(latency: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(latency, "load_set", lambda name, joined: (None, []))
    with pytest.raises(SystemExit, match="no questions"):
        latency.requests(["tools-effort-60"], Path("nowhere"), need_prompts=False)


def test_a_joined_file_missing_questions_is_refused(latency: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "tools-effort-60.jsonl").write_text('{"question_id": "nope", "question": "q"}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="have no text"):
        latency.requests(["tools-effort-60"], tmp_path, need_prompts=True)


def test_a_failing_build_still_closes_the_routers_built_before_it(latency: ModuleType) -> None:
    routers = module("router_latency_routers")
    log: list[str] = []

    def broken() -> Any:
        raise RuntimeError("no key")

    specs = [routers.RouterSpec("a", "a", lambda: Closing(log, "a")), routers.RouterSpec("b", "b", broken)]
    args = latency.parse_args(["-n", "1", "--warmup", "0", "--cold", "0", "--e2e", "1"])
    with pytest.raises(RuntimeError):
        latency.run(args, specs, [req()])
    assert log == ["a"]


@pytest.mark.parametrize("use_async", [False, True])
def test_a_router_that_fails_to_close_still_lets_the_rest_close(latency: ModuleType, use_async: bool) -> None:
    closed: list[str] = []

    class Router:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)
            if self.name == "a":
                raise RuntimeError("close failed")

        async def aclose(self) -> None:
            self.close()

    loop = asyncio.new_event_loop() if use_async else None
    with pytest.raises(RuntimeError, match="close failed"):
        latency._close([Router("a"), Router("b")], loop)
    assert closed == ["a", "b"]
    assert loop is None or loop.is_closed()


def test_the_reported_cost_prices_the_requests_the_run_sent(latency: ModuleType) -> None:
    sent: list[str] = []
    price = {p: 10.0**i for i, p in enumerate("abcdef")}  # six prompts: one seed's 12 would price each twice

    class Recording:
        timeout_s = 1.0

        def route(self, req: RouteRequest) -> RouteDecision:
            sent.append(req.prompt)
            return RouteDecision()

    spec = module("router_latency_routers").RouterSpec("rec", "rec", Recording, cost_usd=lambda r: price[r.prompt])
    args = latency.parse_args(["-n", "3", "--warmup", "1", "--cold", "3", "--e2e", "3", "--seed", "7"])
    report = latency.run(args, [spec], [req(p) for p in price])
    assert len(sent) == 1 + 1 + 3 + 3 + 1 + 3
    assert report["routers"]["rec"]["cost_usd"] == sum(price[p] for p in sent)
