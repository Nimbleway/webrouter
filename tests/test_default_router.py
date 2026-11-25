"""The routers' default router: ``data-driven``, attached for plan and run alone, and what it plans for a prompt."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from tests.plan_helpers import called_fixture  # noqa: F401  (fixture)
from tests.routing_helpers import MEMBERS, wr
from web_router import AsyncWebRouter, WebRouter
from web_router.constants_routing import MSG_RULES_DEPRECATED
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


@pytest.mark.usefixtures("called")
def test_the_deprecated_rules_router_warns_once_and_keeps_its_behavior() -> None:
    assert "rules" in available_routers()
    with (
        pytest.warns(FutureWarning, match="rules.*one release.*following release.*data-driven.*router=None") as seen,
        WebRouter(router="rules", provider_config={"auto": {"chain": list(MEMBERS)}}) as router,
    ):
        for prompt, tool in [
            ("why is the sky blue", "search"),
            ("compare https://doi.org/10.1038/nphys1170 and heat pumps", "extract"),
            ("compare heat pumps and gas boilers", "research"),
            ("What is market research?", "search"),
        ]:
            plan = router.plan(prompt)
            assert (plan.tool, plan.decision.router) == (tool, "rules")
    assert len(seen) == 1, "warn when built, not on each plan"


def test_the_rules_warning_is_visible_with_default_python_filters(tmp_path: Path) -> None:
    script = tmp_path / "rules.py"
    script.write_text("from web_router import WebRouter\nwith WebRouter(router='rules'):\n    pass\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", str(script)],
        env={
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "APPDATA": str(tmp_path / "appdata"),
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert result.stderr.count(f"FutureWarning: {MSG_RULES_DEPRECATED}") == 1


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
