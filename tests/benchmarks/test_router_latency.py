"""router-latency, offline: the yardstick, the rule, censoring, the timing, the wire client and the dry run."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
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
    assert calls[:2] != calls[2:4] or calls[2:4] != calls[4:6], "the order is shuffled each round"


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


# The wire client


def test_the_wire_client_posts_schema_one_and_reads_the_decision(latency: ModuleType) -> None:
    seen: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"schema": 1, "tools": ["search"], "reason": None, "extra": 1})

    router = _module("router_latency_routers").WireRouter("http://127.0.0.1:1/route", httpx.MockTransport(answer))
    decision = router.route(_req())
    router.close()
    assert seen[0]["schema"] == 1 and seen[0]["prompt"] == "who won"
    assert list(decision.tools or ()) == ["search"]


@pytest.mark.parametrize(
    ("status", "body"), [(500, {"schema": 1}), (200, {"schema": 2, "tools": ["search"]}), (200, [1])]
)
def test_the_wire_client_refuses_another_status_or_schema(latency: ModuleType, status: int, body: Any) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    router = _module("router_latency_routers").WireRouter("http://127.0.0.1:1/route", transport)
    with pytest.raises(ValueError, match="router service"):
        router.route(_req())


def test_a_stated_rtt_adds_a_simulated_remote_beside_loopback(latency: ModuleType) -> None:
    specs = _module("router_latency_routers").router_specs("remote:http://127.0.0.1:1/route", rtts_ms=[40.0])
    assert [s.name for s in specs] == ["remote:http://127.0.0.1:1/route", "remote:http://127.0.0.1:1/route +40ms"]
    assert "simulated 40 ms" in specs[1].describe


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
