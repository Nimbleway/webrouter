"""``router="fallback"``: routers asked in order until one decides something, and the record of each one asked."""

from __future__ import annotations

from typing import Any

import pytest

from tests.routing_fallback_helpers import aanswers, answers, empty, invalid, outcomes, raises, request
from tests.routing_registry_helpers import NAME, Closable, built_fixture  # noqa: F401  (fixture)
from web_router import AsyncWebRouter, CompositeDecision, DecisionMember, RouterError, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.fallback import FallbackRouter


def fallback(*routers: Any, **config: Any) -> WebRouter:
    return WebRouter(router="fallback", router_config={"routers": list(routers), **config})


@pytest.mark.parametrize(
    ("failing", "code"),
    [(raises, "router_error"), (invalid, "router_invalid"), (empty, "router_empty")],
    ids=["raises", "invalid", "empty"],
)
def test_a_member_that_fails_or_answers_empty_passes_to_the_next(failing: Any, code: str) -> None:
    log: list[str] = []
    with fallback(failing, answers("second", log), answers("third", log)) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert log == ["second"], "asked in order, and no further than the first that decides"
    assert plan.decision.reason == "second"
    assert [(m.outcome, m.counted) for m in plan.decision.members or []] == [(code, False), ("ok", True)]
    assert all(m.ms >= 0 for m in plan.decision.members or [])


async def test_async_callers_ask_sync_and_async_members_alike() -> None:
    log: list[str] = []
    config = {"routers": [raises, aanswers("async", log), answers("x")]}
    router = AsyncWebRouter(router="fallback", router_config=config)
    async with router:
        plan = await router.plan("who won the 2022 world cup")
    assert log == ["async"] and plan.decision.reason == "async"
    assert [m.outcome for m in plan.decision.members or []] == ["router_error", "ok"]


def test_none_answering_is_an_empty_decision_recorded_as_router_empty() -> None:
    with fallback(raises, empty) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert plan.decision.reason == "router_empty"
    assert [m.outcome for m in plan.decision.members or []] == ["router_error", "router_empty"]


def test_fail_on_router_error_raises_only_when_no_member_answers() -> None:
    config = {"routers": [raises, empty]}
    with (
        WebRouter(router="fallback", router_config=config, fail_on_router_error=True) as wr,
        pytest.raises(RouterError, match="router_empty"),
    ):
        wr.plan("who won the 2022 world cup")
    config = {"routers": [raises, answers("second")]}
    with WebRouter(router="fallback", router_config=config, fail_on_router_error=True) as wr:
        assert wr.plan("who won the 2022 world cup").decision.reason == "second"


def test_members_are_named_without_paths_and_the_router_after_them(built: list[Closable]) -> None:
    named = answers("named", router_name="my-classifier")
    remote = {"name": "remote", "url": "https://router.example.com:8443/v1/route"}
    with fallback(NAME, {"name": NAME, "threshold": 0.5}, answers("plain"), named, remote) as wr:
        plan = wr.plan("who won the 2022 world cup")
    members = f"{NAME}, {NAME}, custom, my-classifier, remote(https://router.example.com)"
    assert plan.decision.router == f"fallback({members})"
    assert built[1].config == {"threshold": 0.5}, "a mapping's other keys are that router's config"
    assert "8443" not in str(plan.decision) and "v1" not in str(plan.decision)


def test_a_member_that_declares_route_tools_is_asked_only_on_those_tools() -> None:
    log: list[str] = []
    router = FallbackRouter(routers=[answers("research-only", log, route_tools=("research",)), answers("any", log)])
    decision = router.route(request(pinned_tool="search"))
    assert log == ["any"] and outcomes(decision) == [("custom", "ok", True)]
    assert router.route_tools is None, "one member asked on every tool: the router is too"
    narrow = FallbackRouter(routers=[answers("a", route_tools=("research",)), answers("b", route_tools=("browse",))])
    assert narrow.route_tools == ["browse", "research"]


def test_the_decision_keeps_the_answer_and_lists_the_members() -> None:
    decision = FallbackRouter(routers=[empty, answers("b")]).route(request())
    assert isinstance(decision, CompositeDecision)
    assert (decision.tools, decision.reason) == (["search"], "b")
    assert outcomes(decision) == [("custom", "router_empty", False), ("custom", "ok", True)]


@pytest.mark.parametrize("routers", [[], (), "remote", {"name": "remote"}, None])
def test_routers_must_be_a_non_empty_list(routers: Any) -> None:
    with pytest.raises(ConfigurationError, match="non-empty list"):
        WebRouter(router="fallback", router_config={"routers": routers})


def test_routers_is_required() -> None:
    with pytest.raises(ConfigurationError, match="needs the router_config key"):
        WebRouter(router="fallback", router_config={})


@pytest.mark.parametrize(
    ("member", "match"),
    [
        (None, "member 0"),
        (3, "member 0"),
        ({"url": "https://router.example.com"}, "without a string 'name'"),
        ("no-such-router", "names no registered router"),
        (object(), "router= takes"),
    ],
)
def test_a_member_in_no_form_is_refused_at_build(member: Any, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        fallback(member)


def test_its_time_limit_can_be_set() -> None:
    assert FallbackRouter(routers=[answers("a")], timeout_s=2.5).timeout_s == 2.5
    with pytest.raises(ConfigurationError, match="timeout_s"):
        fallback(answers("a"), timeout_s=0)


def test_a_fallback_may_hold_a_fallback() -> None:
    inner = {"name": "fallback", "routers": [raises, answers("inner")]}
    with fallback(empty, inner) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert plan.decision.router == "fallback(custom, fallback(custom, custom))"
    assert plan.decision.reason == "inner"
    assert [(m.name, m.outcome) for m in plan.decision.members or []] == [
        ("custom", "router_empty"),
        ("fallback(custom, custom)", "ok"),
    ]


def test_an_inner_fallback_that_ran_out_of_time_is_recorded_as_a_timeout() -> None:
    def inner(req: Any) -> CompositeDecision:
        return CompositeDecision(timed_out=True)

    with fallback(inner, answers("next")) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert [(m.name, m.outcome) for m in plan.decision.members or []] == [
        ("custom", "router_timeout"),
        ("custom", "ok"),
    ]


def ok(name: str, counted: bool) -> DecisionMember:
    return DecisionMember(name=name, outcome="ok", ms=1.0, counted=counted)


@pytest.mark.parametrize("members", [("custom",), (ok("a", True), ok("b", True))], ids=["not-members", "two-counted"])
def test_a_composite_decision_with_malformed_members_is_invalid(members: Any) -> None:
    def composite(req: Any) -> CompositeDecision:
        return CompositeDecision(tools=["search"], members=members)

    with WebRouter(router=composite) as wr:
        plan = wr.plan("who won the 2022 world cup")
    assert plan.decision.reason == "router_invalid" and plan.decision.members is None
