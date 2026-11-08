"""``url_policy`` on a router and in a plan: the URLs a prompt names and a router supplies, held to it."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.routing_helpers import MEMBERS, TOOLS, wr
from tests.routing_helpers import member as _member
from web_router import RouteDecision, RouteRequest, WebRouter
from web_router.exceptions import ConfigurationError, MissingParameterError


def test_an_unknown_policy_on_a_router_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="url_policy"):
        WebRouter(url_policy="everything")  # type: ignore[arg-type]


@pytest.fixture
def members() -> Iterator[None]:
    for tool in TOOLS:
        for name in MEMBERS:
            tool.register(name, _member(tool, name, [], fails=False))
    yield
    for tool in TOOLS:
        for name in MEMBERS:
            tool.unregister(name)


PRIVATE_PROMPT = "read http://169.254.169.254/latest and https://example.com/a"


@pytest.mark.usefixtures("members")
def test_with_the_policy_off_a_private_url_is_planned_as_any_other() -> None:
    plan = wr().plan(PRIVATE_PROMPT, tool="extract")
    assert plan.params["urls"] == ["http://169.254.169.254/latest", "https://example.com/a"]
    assert plan.decision.dropped is None


@pytest.mark.usefixtures("members")
def test_public_only_refuses_a_private_url_before_any_candidate_and_records_why() -> None:
    seen: list[RouteRequest] = []

    def router(req: RouteRequest) -> RouteDecision:
        seen.append(req)
        return RouteDecision(tools=["extract"])

    plan = wr(router=router, url_policy="public_only").plan(PRIVATE_PROMPT)
    assert seen[0].urls == ("https://example.com/a",), "the router never sees the refused URL"
    assert plan.params["urls"] == ["https://example.com/a"]
    assert plan.decision.dropped == {"url:http://169.254.169.254": "the host is a cloud metadata endpoint"}


@pytest.mark.usefixtures("members")
def test_when_every_url_is_refused_a_pinned_extract_is_refused_and_browse_has_no_start() -> None:
    router = wr(url_policy="public_only")
    with pytest.raises(MissingParameterError, match=r"url_policy refuses .*url:http://10\.0\.0\.1"):
        router.plan("read http://10.0.0.1/x", tool="extract")
    assert router.plan("open http://10.0.0.1/x", tool="browse").params["start_url"] is None


@pytest.mark.usefixtures("members")
def test_when_every_url_is_refused_an_unpinned_plan_never_picks_extract() -> None:
    picks_extract = wr(router=lambda req: RouteDecision(tools=["extract"]), url_policy="public_only")
    plan = picks_extract.plan("read http://10.0.0.1/x")
    assert plan.tool == "search" and "url:http://10.0.0.1" in (plan.decision.dropped or {})


def _url_router(url: str) -> Any:
    return lambda req: RouteDecision(tools=["extract"], params={"extract": {"urls": [url]}})


@pytest.mark.usefixtures("members")
@pytest.mark.parametrize(("policy", "kept"), [(None, True), ("public_only", False)])
def test_urls_a_router_supplies_go_through_the_policy(policy: str | None, kept: bool) -> None:
    plan = wr(router=_url_router("http://192.168.0.10/admin"), url_policy=policy).plan("summarize the admin page")
    assert (plan.tool == "extract") is kept
    if kept:
        assert plan.params["urls"] == ["http://192.168.0.10/admin"]


@pytest.mark.usefixtures("members")
def test_a_public_url_a_router_supplies_is_planned_under_public_only() -> None:
    plan = wr(router=_url_router("https://example.com/a"), url_policy="public_only").plan("summarize it")
    assert (plan.tool, plan.params["urls"]) == ("extract", ["https://example.com/a"])


@pytest.mark.usefixtures("members")
def test_public_only_refuses_a_bracketed_loopback_the_finder_now_reads_whole() -> None:
    with pytest.raises(MissingParameterError, match="the host is loopback"):
        wr(url_policy="public_only").plan("open http://[::1]/admin", tool="extract")
