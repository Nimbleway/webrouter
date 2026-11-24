"""``plan()`` and ``run()``: a prompt routed to one method, its options and its backends, then run.

Every method's ``auto`` runs over three fake members, ``first`` and ``second``
failing before they send anything and ``third`` answering, so the members
called show the order the run took.
"""

from __future__ import annotations

import pytest

from tests.plan_helpers import PARAMS, PROMPT, RESPONSE, URL, called_fixture, chooser  # noqa: F401  (fixture)
from tests.routing_helpers import MEMBERS
from tests.routing_helpers import wr as _wr
from web_router import AsyncWebRouter, Budget, Plan
from web_router.types_answer import AnswerResponse

# -- plan ----------------------------------------------------------------------


@pytest.mark.usefixtures("called")
def test_with_no_router_the_plan_is_search_on_the_static_order() -> None:
    plan = _wr().plan(PROMPT)
    assert (plan.tool, dict(plan.params)) == ("search", {"query": PROMPT})
    assert list(plan.providers["search"]) == list(MEMBERS)
    assert plan.decision.router is None and plan.decision.reason is None
    assert plan.decision.merged == [f"search:{name}" for name in MEMBERS]


@pytest.mark.usefixtures("called")
def test_preferred_keeps_a_url_prompt_on_search() -> None:
    with _wr(router="preferred", router_config={"prefer": "cheap"}) as router:
        plan = router.plan("read https://example.com/pricing")
        assert plan.tool == "search"


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize("tool", list(PARAMS))
def test_with_no_router_a_pinned_tool_is_planned_with_its_params(tool: str) -> None:
    plan = _wr().plan(PROMPT, tool=tool)
    assert (plan.tool, dict(plan.params)) == (tool, PARAMS[tool])
    assert list(plan.providers[tool]) == list(MEMBERS)


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize("tool", list(PARAMS))
def test_the_router_picks_each_tool_and_the_plan_carries_its_params(tool: str) -> None:
    router = chooser(tool)
    plan = _wr(router=router).plan(PROMPT)
    assert (plan.tool, dict(plan.params)) == (tool, PARAMS[tool])
    assert plan.providers[tool][0] == "third"
    assert (plan.decision.reason, plan.decision.router) == ("picked", router.__qualname__)
    (req,) = router.seen
    assert req.pinned_tool is None and req.urls == (URL,)
    assert {c.tool for c in req.candidates} == set(PARAMS), "every method is offered"


@pytest.mark.usefixtures("called")
def test_a_budget_maps_research_effort_into_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_router.routing import params
    from web_router.routing.params import EffortChoice

    monkeypatch.setattr(params, "map_effort", lambda *_, **__: EffortChoice(effort="low", source="budget", reason="r"))
    plan = _wr(router=chooser("research")).plan(PROMPT, budget=Budget(time_limit_s=600.0))
    assert dict(plan.params) == {"query": PROMPT, "effort": "low"}
    assert plan.decision.params_source == "budget"


# -- run -----------------------------------------------------------------------


@pytest.mark.parametrize("tool", list(PARAMS))
def test_run_of_a_plan_returns_the_methods_own_response_with_the_record(tool: str, called: list[str]) -> None:
    wr = _wr(router=chooser(tool))
    plan = wr.plan(PROMPT)
    response = wr.run(plan)
    assert isinstance(response, RESPONSE[tool])
    assert called[0] == f"{tool}:third", "run in the plan's order"
    planned = plan.decision.model_dump(exclude_none=True, exclude={"unmeasured"})
    assert response.decision.model_dump(include=set(planned)) == planned, "the plan's record, decision_id and all"
    assert response.decision.ran == 0, "and what the run did: the position that answered"
    assert [attempt.id for attempt in response.decision.attempts] == [f"{tool}:third"]


@pytest.mark.parametrize("tool", list(PARAMS))
def test_run_of_a_prompt_plans_and_runs_in_one_step(tool: str, called: list[str]) -> None:
    response = _wr(router=chooser(tool)).run(PROMPT)
    assert isinstance(response, RESPONSE[tool])
    assert called[0] == f"{tool}:third"
    assert response.decision.reason == "picked"


def test_run_of_a_plan_made_by_hand_uses_its_order(called: list[str]) -> None:
    plan = Plan(prompt="q", tool="answer", params={"query": "q"}, providers={"answer": ["third", "first"]})
    response = _wr().run(plan)
    assert isinstance(response, AnswerResponse)
    assert called == ["answer:third"]


def test_run_with_a_budget_records_the_spend(called: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from web_router import prices

    row = SimpleNamespace(
        unit="per_1k", default=False, estimate_usd=0.001, estimate_is_floor=False, mode=None, results_assumed=None
    )
    monkeypatch.setattr(prices, "price", lambda *_: row)
    monkeypatch.setattr(prices, "estimate_latency", lambda *_: None)
    response = _wr(router=chooser("search")).run(PROMPT, budget=Budget(max_cost_usd=0.01))
    assert called == ["search:third"]
    assert response.decision.reason == "picked" and response.decision.estimated_cost_usd == 0.001


async def test_async_plan_resolves_urls_off_the_event_loop(monkeypatch: pytest.MonkeyPatch, called: list[str]) -> None:
    import socket
    import threading

    loop_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    def fake(host: str, port: object, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        seen.append(threading.current_thread())
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port or 80))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    await _wr(AsyncWebRouter, router=chooser("answer"), url_policy="public_only").plan(PROMPT)
    assert seen and all(thread is not loop_thread for thread in seen)


async def test_async_plan_builds_candidates_on_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, called: list[str]
) -> None:
    import threading
    from typing import Any

    from web_router.routing.candidates import CandidateSet, build_candidates

    loop_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    def recorded(*args: Any, **kwargs: Any) -> CandidateSet:
        seen.append(threading.current_thread())
        return build_candidates(*args, **kwargs)

    monkeypatch.setattr("web_router.client_plan.build_candidates", recorded)
    await _wr(AsyncWebRouter, router=chooser("answer")).plan(PROMPT)
    assert seen and all(thread is loop_thread for thread in seen)


async def test_async_plan_and_run_route_as_the_sync_ones_do(called: list[str]) -> None:
    wr = _wr(AsyncWebRouter, router=chooser("answer"))
    plan = await wr.plan(PROMPT)
    assert plan.tool == "answer"
    response = await wr.run(plan)
    assert isinstance(response, AnswerResponse) and called[0] == "answer:third"
    assert isinstance(await wr.run(PROMPT), AnswerResponse)


def test_run_writes_the_spend_log(called: list[str]) -> None:
    from web_router import SpendEntry

    entries: list[SpendEntry] = []
    wr = _wr(router=chooser("search"))
    wr.log_spend(type("Kept", (), {"record": lambda _self, entry: entries.append(entry)})())
    response = wr.run(PROMPT)
    assert [(entry.method, entry.provider) for entry in entries] == [("search", "third")]
    assert response.elapsed_s is not None and entries[0].elapsed_s == response.elapsed_s, "metered as any call is"


async def test_async_run_writes_the_spend_log(called: list[str]) -> None:
    from web_router import SpendEntry

    entries: list[SpendEntry] = []
    wr = _wr(AsyncWebRouter, router=chooser("answer"))
    wr.log_spend(type("Kept", (), {"record": lambda _self, entry: entries.append(entry)})())
    await wr.run(PROMPT)
    assert [(entry.method, entry.provider) for entry in entries] == [("answer", "third")]
