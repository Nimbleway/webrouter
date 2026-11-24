"""The agent benchmark's routed arm: web-router's tools on auto, with the data-driven router asked on every search."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from web_router.evals import Dataset, Question

AGENT_BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "agent"
DATASET = Dataset(name="frames", questions=[Question(question="q", answer="a")])


@pytest.fixture(scope="module")
def run() -> Iterator[ModuleType]:
    sys.path.insert(0, str(AGENT_BENCH))
    try:
        import run

        yield run
    finally:
        sys.path.remove(str(AGENT_BENCH))
        for name in ("run", "report", "direct", "routed"):
            sys.modules.pop(name, None)


async def test_the_data_driven_arm_asks_its_router_on_every_search_and_auto_never_does(run: ModuleType) -> None:
    """web-router:auto and web-router:data-driven share the tools; only the routed arm's router is asked on a search."""
    assert run._preset("web-router:data-driven") == run._preset("web-router:data-driven:no-guide") == "data-driven"
    asked = {run._preset(spec) for spec in ("web-router:data-driven", "web-router:auto", "claude-native")}
    own = run.routed_routers(run.WithholdingRouter, DATASET, asked, None)
    assert set(own) == {"data-driven"}, "a router of its own only for the routed arm asked"
    async with own["data-driven"] as routed, run.WithholdingRouter(DATASET) as plain:
        arm = run.build_arm("web-router:data-driven", routed, 5, run.CLAUDE)
        assert arm.executor.provider == "auto" and arm.executor.router is routed
        assert routed._route_tools == frozenset({"search"}) and plain._route_tools == frozenset()
        assert "parallel" in routed._extra_modes, "the measured tiers are shown to the router"
        assert routed.dataset is DATASET, "the routed arm keeps the answer-key pages from the agent too"


def test_no_routed_router_is_built_unless_its_arm_runs(run: ModuleType) -> None:
    assert run.routed_routers(run.WithholdingRouter, DATASET, {None, "auto-tuned"}, None) == {}


async def test_the_routed_router_shares_the_other_arms_config_so_an_outage_reaches_it(run: ModuleType) -> None:
    plain = {"auto": {"chain": {"search": ["keenable", "nimble"], "extract": ["nimble"]}}}
    async with run.routed_routers(run.WithholdingRouter, DATASET, {"data-driven"}, plain)["data-driven"] as routed:
        assert [name for name, _ in routed.get_provider("auto")._chain] == ["keenable", "nimble"]


def test_the_report_names_a_routed_arms_picks_before_autos_order_on_search_alone(run: ModuleType) -> None:
    import report

    chain = {"chain": [{"provider": "perplexity", "body": {}}, {"provider": "nimble", "body": {}}]}
    config = {"search": chain, "extract": chain, **run.routed_record("data-driven")}
    (_, searched, _), (_, read, _) = report.backend_lines(config)
    assert searched == "the data-driven router's picks, then auto: perplexity first, then nimble"
    assert read == "auto: perplexity first, then nimble", "the router is not asked on a page read"
    assert report.backend_lines({"search": chain})[0][1] == "auto: perplexity first, then nimble"


def test_run_json_records_a_routed_arms_router_and_data_release_and_nothing_for_other_arms(run: ModuleType) -> None:
    from web_router.routing.data_driven import DataDrivenRouter

    recorded = run.routed_record("data-driven")["router"]
    assert recorded["router"] == "data-driven" and recorded["route_tools"] == ["search"]
    assert recorded["release"] == DataDrivenRouter().version
    json.dumps(recorded)
    assert run.routed_record("auto-tuned") == run.routed_record(None) == {}
