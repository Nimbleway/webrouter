"""The built-in ``data-driven`` router: the method it ranks first, what it holds back, and its release's checks."""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.plan_helpers import called_fixture  # noqa: F401  (fixture)
from tests.routing_helpers import wr
from web_router import RouteRequest
from web_router.constants_data_driven import (
    MSG_DATA_DRIVEN_FEATURES,
    MSG_DATA_DRIVEN_METHODS,
    MSG_DATA_DRIVEN_ROOT,
    MSG_DATA_DRIVEN_ROW_TOTAL,
    MSG_DATA_DRIVEN_ROW_WIDTH,
    MSG_DATA_DRIVEN_TABLE_DEPTH,
)
from web_router.exceptions import ConfigurationError
from web_router.routing.data_driven import DataDrivenRouter
from web_router.routing.data_driven import table as table_module
from web_router.routing.data_driven.table import ToolTable, shipped_table
from web_router.routing.registry import available_routers

URL = "https://example.com/prices"


def _req(prompt: str, urls: tuple[str, ...] = ()) -> RouteRequest:
    return RouteRequest(prompt=prompt, candidates=(), deadline_s=0.05, urls=urls)


@pytest.mark.parametrize(
    ("prompt", "urls", "tools"),
    [
        ("latest funding round for Acme", (), ["search", "research"]),
        ("compare the pricing plans of the top 5 CRM tools", (), ["research", "search"]),
        (f"extract the table from {URL}", (URL,), ["extract", "search"]),
        (f"extract the table from {URL}", (), ["search"]),
        ("fill the top 10 CRM tools then check out", (), ["search", "research"]),  # an action row, browse held back
    ],
)
def test_it_ranks_the_rows_methods_and_holds_back_browse_and_an_extract_with_no_url(
    prompt: str, urls: tuple[str, ...], tools: list[str]
) -> None:
    assert DataDrivenRouter().route(_req(prompt, urls)).tools == tools


def test_browse_is_ranked_only_by_a_router_built_to() -> None:
    prompt = "log in to gamestop and add a PS5 to the cart"
    opted, held = DataDrivenRouter(browse=True).route(_req(prompt)), DataDrivenRouter().route(_req(prompt))
    assert (opted.tools, opted.confidence) == (["browse", "search"], pytest.approx(27 / 28))
    assert (held.tools, held.confidence) == (["search"], pytest.approx(1 / 28)), "the share of the method ranked first"


@pytest.mark.parametrize("browse", ["false", 1, None])
def test_browse_takes_a_bool_alone(browse: Any) -> None:
    with pytest.raises(ConfigurationError, match="browse=True or browse=False"):
        DataDrivenRouter(browse=browse)


def test_a_search_no_training_request_ran_is_named_the_last_resort() -> None:
    router = DataDrivenRouter()
    decision = router.route(_req(f"extract the table from {URL}"))
    assert (decision.tools, decision.confidence) == (["search"], 0.0)
    assert decision.reason == (
        f"data-driven {router.version}: search, which no training request with verb=extract, url_target=page, "
        "plural=no, kw_research=no, ui_action=no ran, as the last resort"
    )


def test_the_decision_names_the_release_the_row_and_its_share() -> None:
    router = DataDrivenRouter()
    decision = router.route(_req("compare the pricing plans of the top 5 CRM tools"))
    assert decision.reason == f"data-driven {router.version}: research for verb=analyze, url_target=none"
    assert decision.confidence == pytest.approx(34 / 36)


def _table(**changes: Any) -> ToolTable:
    table = {"": [3, 0, 1, 0, 4], "question": [1, 0, 30, 0, 31], "other": [1, 0, 0, 0, 1]}
    fields: dict[str, Any] = {
        "version": "9.9.9",
        "features": ("verb",),
        "labels": ("search", "extract", "research", "browse"),
        "min_count": 20,
        "tables": ({"": table[""]}, {key: row for key, row in table.items() if key}),
    }
    return ToolTable(**{**fields, **changes})


def test_a_row_seen_too_rarely_backs_off_to_the_shorter_key() -> None:
    table = _table()
    assert (table.row("who is it").values, table.row("who is it").counts["research"]) == (("question",), 30)
    rare = table.row("the weather")
    assert (rare.values, rare.counts) == ((), {"search": 3, "extract": 0, "research": 1, "browse": 0})
    assert _table(min_count=1).row("the weather").values == ("other",)


def test_a_request_matching_no_frequent_row_gets_the_overall_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web_router.routing.data_driven.router.shipped_table", _table)
    decision = DataDrivenRouter().route(_req("the weather"))
    assert (decision.tools, decision.reason) == (
        ["search", "research"],
        "data-driven 9.9.9: search, the most common method overall",
    )


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"features": ["verb", "mood"]}, MSG_DATA_DRIVEN_FEATURES),
        ({"labels": ["search", "teleport"]}, MSG_DATA_DRIVEN_METHODS),
        ({"tables": [{"": [1, 1]}]}, MSG_DATA_DRIVEN_TABLE_DEPTH),
        ({"tables": []}, MSG_DATA_DRIVEN_TABLE_DEPTH),
        ({"tables": [{"": [1, 1]}, {"other": [1]}]}, MSG_DATA_DRIVEN_ROW_WIDTH),
        ({"tables": [{}, {}]}, MSG_DATA_DRIVEN_ROOT),
        ({"tables": [{"": [0, 0]}, {}]}, MSG_DATA_DRIVEN_ROOT),
        ({"tables": [{"": [1, 1]}, {"question": [1, 20]}]}, MSG_DATA_DRIVEN_ROW_TOTAL),
    ],
)
def test_a_table_this_version_cannot_use_is_refused(change: dict[str, Any], why: str) -> None:
    data = {"features": ["verb"], "labels": ["search"], "min_count": 1, "tables": [{"": [1, 1]}, {}], **change}
    with pytest.raises(ConfigurationError, match=why):
        table_module.checked_table("1.0.0", data)


def test_a_manifest_with_another_schema_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(table_module, "read_release", lambda name: {"schema": 2, "version": "2.0.0"})
    shipped_table.cache_clear()
    try:
        with pytest.raises(ConfigurationError, match=r"schema 2.*reads schema 1"):
            shipped_table()
    finally:
        shipped_table.cache_clear()


def test_the_patterns_stay_fast_on_a_long_prompt() -> None:
    router, start = DataDrivenRouter(), time.perf_counter()
    openers = ("i want to " * 10_000 + "x", "i need to help me to " * 5_000)
    for prompt in (
        "please " * 50_000 + "x",
        "compare " + "a " * 100_000,
        "https://a.example/" + "x/" * 50_000,
        *openers,
    ):
        router.route(_req(prompt))
    assert time.perf_counter() - start < 1.0


def test_by_name_it_is_asked_by_plan_and_run_alone() -> None:
    assert "data-driven" in available_routers()
    built = DataDrivenRouter()
    assert (built.route_tools, built.route_modes, built.router_name) == ((), False, "data-driven")


@pytest.mark.usefixtures("called")
@pytest.mark.parametrize(
    ("prompt", "config", "tool"),
    [
        ("compare A and B across Europe", {}, "research"),
        ("log in to gamestop and add a PS5 to the cart", {"browse": True}, "browse"),
    ],
)
def test_plan_follows_it(prompt: str, config: dict[str, Any], tool: str) -> None:
    plan = wr(router="data-driven", router_config=config).plan(prompt)
    assert (plan.tool, plan.decision.router) == (tool, "data-driven")
