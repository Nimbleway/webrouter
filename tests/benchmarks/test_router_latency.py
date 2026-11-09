"""router-latency, offline: the yardstick, the rule, censoring, the timing, the remote router and the dry run."""

from __future__ import annotations

import asyncio
import random
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scripts.benchmark_loader import load_script
from web_router import RouteDecision, RouteRequest, registry
from web_router.routing.contract import Candidate

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "providers" / "router_latency.py"
SIBLINGS = (
    "_common",
    "_frozen_checks",
    "_frozen_mixed",
    "_tools_effort_plan",
    "frozen",
    "route_eval_paths",
    "route_eval_sets",
    "route_eval_rank",
    "route_eval_merge",
    "router_latency_tools",
    "router_latency_routers",
    "router_latency_run",
    "router_latency_report",
)


@pytest.fixture(scope="module")
def latency() -> Iterator[ModuleType]:
    with load_script(SCRIPT, "router_latency_under_test", SIBLINGS) as module:
        yield module


def _module(name: str) -> ModuleType:
    return sys.modules[name]


def _req(prompt: str = "who won") -> RouteRequest:
    candidates = (Candidate(tool="search", provider="perplexity"), Candidate(tool="search", provider="nimble"))
    return RouteRequest(prompt=prompt, candidates=candidates, deadline_s=1.0, pinned_tool="search")


# The yardstick and the rule


def test_a_tool_s_p50_is_the_first_backend_of_its_auto_chain(latency: ModuleType) -> None:
    tools = _module("router_latency_tools").tool_latencies("all")
    assert tools["search"].head == "perplexity" and tools["search"].is_measured
    assert tools["search"].range_p50_s is not None and tools["search"].range_p50_s[0] <= tools["search"].p50_s
    assert tools["answer"].is_measured
    assert not any(tools[t].is_measured for t in ("extract", "research", "browse")), "never made up"


def test_the_rule_consults_under_a_quarter_and_never_weighs_what_is_not_measured(latency: ModuleType) -> None:
    mod = _module("router_latency_tools")
    tool = mod.ToolLatency("search", "x", 1.0, 2.0, 10, None, None)
    assert mod.verdict(0.2, tool) == (mod.CONSULT, 0.2)
    assert mod.verdict(0.25, tool)[0] == mod.SKIP
    assert mod.verdict(None, tool) == (mod.UNSURE, None)
    assert mod.verdict(0.01, mod.ToolLatency("browse", "y", None, None, None, None, None)) == (mod.JUDGEMENT, None)


def test_a_timeout_is_censored_never_a_value(latency: ModuleType) -> None:
    run = _module("router_latency_run")
    series = run.Series()
    for ms in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0):
        series.samples_ms.append(ms)
    series.censored = 2
    stats = series.stats()
    assert stats["p50_ms"] == 5.0
    assert stats["p90_ms"] is None, "the 90th rank of 10 is a timeout"
    assert stats["timeout_rate"] == 0.2 and stats["max_ms"] == 8.0


# The timing


def test_routers_are_timed_interleaved_first_call_and_warm_up_apart(latency: ModuleType) -> None:
    calls: list[str] = []

    def fast(req: RouteRequest) -> RouteDecision:
        calls.append("fast")
        return RouteDecision()

    def slow(req: RouteRequest) -> RouteDecision:
        calls.append("slow")
        time.sleep(0.05)
        return RouteDecision()

    plan = [_req()] * 6
    out = _module("router_latency_run").interleaved(
        {"fast": fast, "slow": slow}, plan, warmup=2, deadline_s=0.01, seed=0, use_async=False
    )
    assert out["fast"]["first_call"]["answered"] == 1
    assert out["fast"]["warm"]["calls"] == 3 and out["fast"]["warm"]["fallback_rate"] == 0.0
    assert out["slow"]["warm"]["timeouts"] == 3 and out["slow"]["warm"]["p50_ms"] is None


def test_each_round_is_in_its_own_random_order(latency: ModuleType) -> None:
    rounds = list(_module("router_latency_run")._rounds(random.Random(0), ["a", "b", "c"], 12))
    orders = {tuple(name for i, name in rounds if i == r) for r in range(12)}
    assert len(orders) > 1 and all(sorted(order) == ["a", "b", "c"] for order in orders)


def test_the_added_wall_time_runs_on_stubs_and_puts_the_registry_back(latency: ModuleType) -> None:
    run = _module("router_latency_run")
    out = run.added_wall_time(
        {"none": lambda req: RouteDecision()}, ["q"] * 4, ("perplexity", "nimble"), warmup=1, seed=0, use_async=False
    )
    assert out["none"]["calls"] == 3 and out["none"]["routed_p50_ms"] is not None
    assert "perplexity" not in registry._CUSTOM_PROVIDERS


def test_the_async_timing_runs_from_an_event_loop(latency: ModuleType) -> None:
    async def aroute(req: RouteRequest) -> RouteDecision:
        return RouteDecision()

    run = _module("router_latency_run")
    out = run.interleaved({"a": aroute}, [_req()] * 3, warmup=1, deadline_s=1.0, seed=0, use_async=True)
    assert out["a"]["warm"]["answered"] == 1


# The remote router


def test_remote_is_the_library_s_own_remote_router(latency: ModuleType) -> None:
    from web_router.routing.remote import RemoteRouter

    routers = _module("router_latency_routers")
    router = routers.router_specs("remote:http://127.0.0.1:1/route")[0].build()
    try:
        assert isinstance(router, RemoteRouter)
    finally:
        routers.close(router)


def test_a_stated_rtt_adds_a_simulated_remote_beside_loopback(latency: ModuleType) -> None:
    specs = _module("router_latency_routers").router_specs("remote:http://u:pw@127.0.0.1:1/r?token=s", rtts_ms=[40.0])
    assert [s.name for s in specs] == ["remote:http://127.0.0.1", "remote:http://127.0.0.1 +40ms"]
    assert "simulated 40 ms" in specs[1].describe
    assert not any(secret in s.name + s.describe for s in specs for secret in ("pw", "token", "/r"))


def test_a_simulated_link_keeps_the_call_within_its_deadline(latency: ModuleType) -> None:
    routers = _module("router_latency_routers")
    seen: list[float] = []

    class Inner:
        timeout_s = 1.0

        async def aroute(self, req: RouteRequest) -> RouteDecision:
            seen.append(req.deadline_s)
            return RouteDecision()

    asyncio.run(routers.DelayedRouter(Inner(), 10.0).aroute(_req()))  # first call: three round trips, 30 ms of 1 s
    assert seen and seen[0] == pytest.approx(0.97)
    with pytest.raises(TimeoutError):
        asyncio.run(routers.DelayedRouter(Inner(), 400.0).aroute(_req()))


def test_a_router_named_twice_is_refused(latency: ModuleType) -> None:
    with pytest.raises(SystemExit, match="more than once"):
        latency.main(["--router", "rules", "--router", "rules", "--dry-run"])


def test_stubbing_puts_a_backend_of_your_own_back(latency: ModuleType) -> None:
    run = _module("router_latency_run")
    mine = type("Mine", (run._Stub,), {"name": "mine"})
    registry.register_provider("perplexity", mine)
    try:
        with run.stubbed(("perplexity",)):
            assert registry._CUSTOM_PROVIDERS["perplexity"] is not mine
        assert registry._CUSTOM_PROVIDERS["perplexity"] is mine
    finally:
        registry.unregister_provider("perplexity")


# The dry run


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


# Refusals and cleanup


def test_an_error_ranks_above_every_answer_like_a_timeout(latency: ModuleType) -> None:
    series = _module("router_latency_run").Series(samples_ms=[1.0], errors={"router_error": 3})
    assert series.stats()["p50_ms"] is None, "one fast answer among failures is no p50"


@pytest.mark.parametrize(
    "args", [["-n", "-1"], ["--llm-price", "-1,1"], ["--llm-price", "nan,1"], ["--jev-price", "inf"]]
)
def test_a_negative_count_or_a_bad_price_is_refused(latency: ModuleType, args: list[str]) -> None:
    with pytest.raises(SystemExit):
        latency.parse_args(args)


def test_a_joined_file_missing_questions_is_refused(latency: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "tools-effort-60.jsonl").write_text('{"question_id": "nope", "question": "q"}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="have no text"):
        latency.requests(["tools-effort-60"], tmp_path, need_prompts=True)


class _Closing:
    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    def route(self, req: RouteRequest) -> RouteDecision:
        return RouteDecision()

    def close(self) -> None:
        self.log.append(self.name)


def test_a_failing_build_still_closes_the_routers_built_before_it(latency: ModuleType) -> None:
    routers = _module("router_latency_routers")
    log: list[str] = []

    def broken() -> Any:
        raise RuntimeError("no key")

    specs = [routers.RouterSpec("a", "a", lambda: _Closing(log, "a")), routers.RouterSpec("b", "b", broken)]
    args = latency.parse_args(["-n", "1", "--warmup", "0", "--cold", "0", "--e2e", "0"])
    with pytest.raises(RuntimeError):
        latency.run(args, specs, [_req()])
    assert log == ["a"]


def test_a_freshly_built_router_the_library_refuses_is_closed(latency: ModuleType) -> None:
    log: list[str] = []

    def refused() -> Any:
        router = _Closing(log, "bad")
        router.timeout_s = -1  # type: ignore[attr-defined]
        return router

    with pytest.raises(Exception, match="timeout_s"):
        _module("router_latency_run").cold_builds({"bad": refused}, [_req()], deadline_s=None, seed=0, use_async=False)
    assert log == ["bad"]
