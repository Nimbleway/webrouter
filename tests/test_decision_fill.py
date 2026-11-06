"""The decision record filled on every routed or budgeted call: its id, what ran, what it moved past, and no prompt.

Each method's ``auto`` runs over three fake members, ``first`` and ``second``
failing before they send anything and ``third`` answering.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from tests.test_auto_seam import MEMBERS, TOOLS, _member, _Tool
from web_router import AsyncWebRouter, Budget, DecisionHop, ProviderError, RouteDecision, RouteRequest, WebRouter
from web_router.routing.record import error_code

QUERY = "what did the acme board decide on the merger"
URL = "https://alice:hunter2@intranet.example.org/board/minutes-2024.pdf?token=s3cr3t"
ARGS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "search": ((QUERY,), {}),
    "extract": (([URL],), {}),
    "answer": ((QUERY,), {}),
    "research": ((QUERY,), {}),
    "browse": ((QUERY,), {"start_url": URL}),
}
PROMPT = {"search": QUERY, "extract": URL, "answer": QUERY, "research": QUERY, "browse": QUERY}
LEAKS = (QUERY, "acme board", URL, "alice", "hunter2", "/board", "minutes-2024", "token", "s3cr3t")
"""What no record may hold: the prompt, a phrase of it, the URL, or any of its path, query or credentials."""


@pytest.fixture(params=TOOLS, ids=[tool.name for tool in TOOLS])
def tool(request: pytest.FixtureRequest) -> Iterator[_Tool]:
    yield request.param


@pytest.fixture
def called(tool: _Tool) -> Iterator[list[str]]:
    log: list[str] = []
    for name in MEMBERS:
        tool.register(name, _member(tool, name, log, fails=name != "third"))
    yield log
    for name in MEMBERS:
        tool.unregister(name)


def _ids(tool: _Tool, *names: str) -> list[str]:
    return [f"{tool.name}:{name}" for name in names]


def _ranker(*ranking: str, **decision: Any) -> Any:
    def route(req: RouteRequest) -> RouteDecision:
        assert req.pinned_tool is not None
        return RouteDecision(providers={req.pinned_tool: list(ranking)}, reason="ranked", **decision)

    return route


def _wr(cls: type = WebRouter, router: Any = None, **kwargs: Any) -> Any:
    return cls(provider_config={"auto": {"chain": list(MEMBERS)}}, router=router, **kwargs)


def _call(router: WebRouter, tool: _Tool, **kwargs: Any) -> Any:
    args, options = ARGS[tool.name]
    return getattr(router, tool.name)(*args, **options, **kwargs)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _assert_filled(record: Any, tool: _Tool) -> None:
    assert re.fullmatch(r"[0-9a-f]{32}", record.decision_id)
    assert record.prompt_sha256 == _sha(PROMPT[tool.name])
    assert record.router == "_ranker.<locals>.route"
    assert record.offered == _ids(tool, *MEMBERS)
    assert record.merged == _ids(tool, "second", "first", "third")
    assert record.ran == 2, "third, last in merged, answered"
    assert [attempt.id for attempt in record.attempts] == record.merged
    assert record.hops == [DecisionHop(id=cid, error="ConfigurationError") for cid in _ids(tool, "second", "first")]
    assert record.cost_is_estimate is True


def test_every_routed_response_carries_a_filled_record(tool: _Tool, called: list[str]) -> None:
    response = _call(_wr(router=_ranker("second")), tool)
    assert response.provider == "third"
    _assert_filled(response.decision, tool)


async def test_every_routed_async_response_carries_a_filled_record(tool: _Tool, called: list[str]) -> None:
    args, options = ARGS[tool.name]
    response = await getattr(_wr(AsyncWebRouter, router=_ranker("second")), tool.name)(*args, **options)
    _assert_filled(response.decision, tool)


def test_each_call_gets_its_own_decision_id(tool: _Tool, called: list[str]) -> None:
    wr = _wr(router=_ranker("third"))
    first, second = (_call(wr, tool).decision for _ in range(2))
    assert first.decision_id != second.decision_id
    assert first.prompt_sha256 == second.prompt_sha256


def test_a_budgeted_call_with_no_router_is_stamped_too(tool: _Tool, called: list[str]) -> None:
    record = _call(_wr(), tool, budget=Budget()).decision
    assert re.fullmatch(r"[0-9a-f]{32}", record.decision_id)
    assert record.prompt_sha256 == _sha(PROMPT[tool.name])
    assert record.ran is None, "no merged order to place it in"
    assert record.hops == [DecisionHop(id=cid, error="ConfigurationError") for cid in _ids(tool, "first", "second")]


def test_with_no_router_and_no_budget_there_is_no_record(tool: _Tool, called: list[str]) -> None:
    assert _call(_wr(), tool).decision is None


@pytest.fixture
def every_tool() -> Iterator[None]:
    for tool in TOOLS:
        for name in MEMBERS:
            tool.register(name, _member(tool, name, [], fails=name != "third"))
    yield
    for tool in TOOLS:
        for name in MEMBERS:
            tool.unregister(name)


def test_a_plan_carries_the_record_with_or_without_a_router(every_tool: None) -> None:
    for router in (None, _ranker("third")):
        record = _wr(router=router).plan(QUERY, tool="search").decision
        assert re.fullmatch(r"[0-9a-f]{32}", record.decision_id or "")
        assert record.prompt_sha256 == _sha(QUERY)


def _leaks(record: Any) -> list[str]:
    shown = record.model_dump_json()
    return [leak for leak in LEAKS if leak in shown]


def test_no_record_holds_the_prompt_the_task_or_a_url(every_tool: None) -> None:
    """Every method, routed and budgeted, a router that echoes the URL in an exclusion, and a plan and its run."""
    router = _ranker("third", exclude={"search:first": f"slow on {URL}"})
    wr = _wr(router=router, url_policy="public_only")
    records = [_call(wr, tool, budget=Budget(time_limit_s=60.0)).decision for tool in TOOLS]
    plan = wr.plan(f"{QUERY} {URL}")
    records += [plan.decision, wr.run(plan).decision]
    for record in records:
        assert record.prompt_sha256, "the prompt is there as its hash"
        assert _leaks(record) == []


def test_a_url_a_router_supplies_shows_only_its_scheme_and_host(every_tool: None) -> None:
    def route(req: RouteRequest) -> RouteDecision:
        return RouteDecision(tools=["extract"], providers={"extract": ["third"]}, params={"extract": {"urls": [URL]}})

    plan = _wr(router=route, url_policy="public_only").plan(QUERY)
    assert plan.params["urls"] == [URL], "called with the whole URL"
    assert plan.decision.params == {"urls": ["https://intranet.example.org"]}
    assert _leaks(plan.decision) == []


def _failing(tool: _Tool, error: Exception) -> type:
    def entry(self: Any, *args: Any) -> Any:
        raise error

    return type("Failing", (tool.fake,), {"name": "first", tool.entry: entry})


def test_a_failure_is_recorded_by_its_status_never_its_message(tool: _Tool, called: list[str]) -> None:
    refused = httpx.HTTPStatusError("429", request=httpx.Request("GET", URL), response=httpx.Response(429))
    tool.register("first", _failing(tool, ProviderError("first", f"429 on {URL} for {QUERY}", original=refused)))
    record = _call(_wr(router=_ranker("first", "third")), tool).decision
    assert record.hops[0] == DecisionHop(id=f"{tool.name}:first", error="429")
    assert _leaks(record) == []


def test_error_code_reads_the_status_wherever_the_backend_put_it() -> None:
    assert (
        error_code(ProviderError("x", "m", original=SimpleNamespace(response=SimpleNamespace(status_code=503))))
        == "503"
    )  # type: ignore[arg-type]
    assert error_code(ProviderError("x", "m", original=SimpleNamespace(status_code=401))) == "401"  # type: ignore[arg-type]
    assert error_code(ProviderError("x", "m", original=SimpleNamespace(status_code=True))) == "ProviderError"  # type: ignore[arg-type]
    assert error_code(ValueError("secret")) == "ValueError"
