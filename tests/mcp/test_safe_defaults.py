"""What a server nobody configured serves: the slim profile, no ``load_env``, and no private addresses.

The rest of ``tests/mcp`` runs with every switch opened up (``OPENED_UP``); this
module unsets them, so it sees what a fresh install sees.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from tests.mcp.conftest import FAKE
from tests.mcp.env_helpers import OPENED_UP
from web_router.client import AsyncWebRouter
from web_router.constants import ENV_LINKUP_API_KEY, MCP_BROWSER_TOOLS, MCP_TOOL_LOAD_ENV, MCP_TOOL_RESEARCH
from web_router.constants_mcp_context import ENV_MCP_TOOLS, MCP_PROFILE_SLIM_TOOLS
from web_router.constants_mcp_env import (
    ENV_MCP_LOAD_ENV,
    ENV_MCP_PRIVATE_URLS,
    ENV_MCP_PROFILE,
    MCP_LOAD_ENV_LOAD,
    MCP_PROFILE_FULL,
)
from web_router.constants_vendors_mcp import MCP_TOOL_CALL_VENDOR
from web_router.exceptions import ConfigurationError
from web_router.mcp import allowlist
from web_router.mcp.env_credentials import credential_variables
from web_router.mcp.navigation import NavigationPolicy
from web_router.mcp.tools import WebRouterTools
from web_router.mcp.urls import screen

SLIM_TOOL_LIST_BUDGET = 10_800
"""Bytes of compact JSON the slim list may take with every key set, about 2.7k tokens: 10,702 when set."""

METADATA = "http://169.254.169.254/latest/meta-data/"
CDP = "ws://127.0.0.1:9222/devtools"


@pytest.fixture(autouse=True)
def defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*OPENED_UP, ENV_MCP_TOOLS):
        monkeypatch.delenv(name, raising=False)


def _served() -> frozenset[str]:
    return WebRouterTools(AsyncWebRouter(FAKE)).served


def test_the_default_profile_serves_neither_browser_nor_vendor_api_nor_load_env(fake_provider: Any) -> None:
    served = _served()

    assert served <= MCP_PROFILE_SLIM_TOOLS
    assert served.isdisjoint({*MCP_BROWSER_TOOLS, MCP_TOOL_CALL_VENDOR, MCP_TOOL_LOAD_ENV})


def test_the_full_profile_serves_the_browser_and_vendor_tools(
    fake_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_MCP_PROFILE, MCP_PROFILE_FULL)

    assert {*MCP_BROWSER_TOOLS, MCP_TOOL_CALL_VENDOR} <= _served()


def test_an_allowlist_wins_over_the_profile(fake_provider: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MCP_TOOLS, "search,browser_*")

    assert _served() == {"search", *MCP_BROWSER_TOOLS}


def test_load_env_is_served_once_its_switch_turns_it_on(fake_provider: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MCP_LOAD_ENV, MCP_LOAD_ENV_LOAD)

    assert MCP_TOOL_LOAD_ENV in _served()


def test_a_profile_that_is_not_one_stops_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MCP_PROFILE, "everything")

    with pytest.raises(ConfigurationError, match=ENV_MCP_PROFILE):
        allowlist.allowed({"search": 1})


def test_the_capabilities_resource_names_the_switches(tools: WebRouterTools) -> None:
    report = tools.capabilities()["tools"]

    assert report["profile"] == "slim" and set(MCP_BROWSER_TOOLS) <= set(report["off"])
    assert ENV_MCP_PROFILE in report["switch"] and ENV_MCP_TOOLS in report["switch"]
    assert f"{ENV_MCP_LOAD_ENV}={MCP_LOAD_ENV_LOAD}" in report[MCP_TOOL_LOAD_ENV]


def test_the_startup_log_names_the_switch_only_under_the_slim_profile(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with caplog.at_level(logging.WARNING):
        allowlist.log_profile()
        monkeypatch.setenv(ENV_MCP_PROFILE, MCP_PROFILE_FULL)
        allowlist.log_profile()

    assert [ENV_MCP_PROFILE in record.getMessage() for record in caplog.records] == [True]


def test_a_tool_with_no_backend_is_hidden_and_its_key_still_named(
    no_keys: None, fake_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = WebRouterTools(AsyncWebRouter(FAKE))

    assert MCP_TOOL_RESEARCH not in {tool.name for tool in tools.list_tools()}
    assert ENV_LINKUP_API_KEY in json.dumps(tools.capabilities()["unlock"])

    monkeypatch.setenv(ENV_LINKUP_API_KEY, "x")
    assert MCP_TOOL_RESEARCH in {tool.name for tool in tools.list_tools()}


@pytest.mark.parametrize("url", ["http://10.0.0.5/", "http://127.0.0.1:8080/", "http://192.168.1.1/"])
def test_the_browser_refuses_private_addresses_and_names_the_switch(url: str) -> None:
    reason = NavigationPolicy.for_session(CDP).refusal(url)

    assert reason is not None and f"{ENV_MCP_PRIVATE_URLS}=on" in reason


def test_cloud_metadata_is_refused_by_the_browser_and_extract_even_with_the_switch_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_MCP_PRIVATE_URLS, "on")

    assert NavigationPolicy.for_session(CDP).refusal(METADATA) is not None
    with pytest.raises(ValueError, match=r"169\.254\.169\.254"):
        screen("extract", [METADATA])


def test_the_slim_tool_list_stays_inside_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in credential_variables():
        monkeypatch.setenv(env_var, "fake")

    listed = [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in WebRouterTools().list_tools()]

    assert len(json.dumps(listed, separators=(",", ":"))) <= SLIM_TOOL_LIST_BUDGET
