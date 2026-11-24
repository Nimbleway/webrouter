"""The routers' default router: ``data-driven``, attached for plan and run alone, and what it plans for a prompt."""

from __future__ import annotations

import asyncio

import pytest

from tests.plan_helpers import called_fixture  # noqa: F401  (fixture)
from tests.routing_helpers import MEMBERS, wr
from web_router import AsyncWebRouter, WebRouter
from web_router.exceptions import ConfigurationError
from web_router.routing.registry import available_routers


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
def test_the_routers_attach_data_driven_by_default_and_none_attaches_nothing(cls: type) -> None:
    default, bare = cls(), cls(router=None)
    try:
        assert default._router is not None and default._router.name == "data-driven"
        assert default._route_tools == frozenset(), "search(), extract() and the rest never ask it"
        assert bare._router is None
    finally:
        for router in (default, bare):
            router.close() if cls is WebRouter else asyncio.run(router.aclose())


def test_the_removed_rules_router_is_refused_by_name() -> None:
    assert "rules" not in available_routers()
    with pytest.raises(ConfigurationError, match=r"names no registered router.*data-driven"):
        WebRouter(router="rules")


def _default(cls: type = WebRouter) -> WebRouter:
    """A router built with no ``router=``, so the default is what plans."""
    return cls(provider_config={"auto": {"chain": list(MEMBERS)}})


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize(
    ("prompt", "tool"),
    [
        ("who won the 2022 world cup", "search"),
        ("best laptops for students", "search"),
        ("Quel est le prix du bitcoin ?", "search"),  # the expressions read English
        ("why is the sky blue", "research"),  # a research word alone is enough: billed, so documented
        ("read https://example.com/a", "extract"),
        ("summarize https://example.com/a", "research"),  # a URL does not always mean extract
    ],
)
def test_what_the_default_plans_for_a_prompt(prompt: str, tool: str) -> None:
    with _default() as router:
        plan = router.plan(prompt)
    assert (plan.tool, plan.decision.router) == (tool, "data-driven")


@pytest.mark.usefixtures("called")
def test_a_pinned_method_and_no_router_keep_a_plan_off_research() -> None:
    assert _default().plan("why is the sky blue", tool="search").tool == "search"
    assert wr().plan("why is the sky blue").tool == "search"
    assert wr().plan("read https://example.com/a").tool == "search", "with no router a URL is not read"


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize("scheme", ["HTTP", "HTTPS", "hTtPs"])
def test_plan_extracts_a_url_with_a_mixed_case_scheme(scheme: str) -> None:
    url = f"{scheme}://example.com/Report?Token=AbC"
    with _default() as router:
        plan = router.plan(f"read {url}")
        assert plan.tool == "extract"
        assert plan.params["urls"] == [url]
        assert router.plan(f"read {url}", tool="extract").params["urls"] == [url]
