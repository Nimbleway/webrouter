"""The built-in ``gemini`` router's setup: off by default, its key read only from the variable a preset names."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.decider_helpers import TOKEN, gemini, request, serve
from web_router import WebRouter
from web_router.exceptions import ConfigurationError
from web_router.mcp.routing_config import McpRouting
from web_router.routing import decider_gemini
from web_router.routing.decider_gemini import gemini_router


def test_no_router_builds_no_gemini_client_and_reads_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a Gemini decider was built")

    monkeypatch.setattr(decider_gemini.GeminiDecider, "__init__", refuse)
    with WebRouter() as client:
        assert client.plan("who won").tool


async def test_a_base_url_of_your_own_reads_no_variable_it_is_not_told_to(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", TOKEN)
    server = serve(monkeypatch, gemini("search:exa"))
    await gemini_router(base_url="http://localhost:9000", model="m").aroute(request())
    assert "x-goog-api-key" not in server.requests[0].headers and "Authorization" not in server.requests[0].headers


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "GEMINI_API_KEY is not set"),
        ({"base_url": "https://d.example.com", "model": "m"}, "api_key_env"),
        ({"base_url": "https://d.example.com", "model": "m", "api_key_env": "GEMINI_API_KEY"}, "sent only to"),
        ({"base_url": "https://d.example.com", "api_key": TOKEN}, "router_config\\['model'\\]"),
        ({"base_url": "https://d.example.com", "model": "m", "api_key": TOKEN, "path": "/d?k=v"}, "query"),
        ({"preset": "google", "base_url": "https://d.example.com"}, "not both"),
        ({"preset": "vertex"}, "has no preset 'vertex'"),
    ],
)
def test_a_bad_setup_is_refused_when_the_router_is_built(
    config: dict[str, Any], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match=message) as caught:
        gemini_router(**config)
    assert TOKEN not in str(caught.value)


def test_by_name_it_orders_every_tool_but_search_unless_told(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", TOKEN)
    with WebRouter(router="gemini") as client:
        assert client._route_tools == {"answer", "extract", "research", "browse"}
    with WebRouter(router="gemini", router_config={"route_tools": ("search",)}) as client:
        assert client._route_tools == {"search"}
    with pytest.raises(ConfigurationError, match="'bogus'"):
        WebRouter(router="gemini", router_config={"bogus": 1})


def test_named_for_the_mcp_server_it_shows_its_host_and_takes_the_key_from_the_process_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environ = {
        "WEB_ROUTER_MCP_ROUTER": "gemini",
        "WEB_ROUTER_MCP_ROUTER_CONFIG": json.dumps({}),
        "GEMINI_API_KEY": TOKEN,
    }
    routing = McpRouting.from_environ(environ)
    section = routing.capabilities(None)
    assert section["router"] == "gemini(generativelanguage.googleapis.com)"
    monkeypatch.setenv("GEMINI_API_KEY", "from-a-file")
    decider = routing.build()._owned_router.decider  # type: ignore[union-attr]
    assert decider._endpoint.headers["x-goog-api-key"] == TOKEN
    assert TOKEN not in str(section) + repr(routing)


def test_named_for_the_mcp_server_with_no_config_its_key_is_still_taken_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routing = McpRouting.from_environ({"WEB_ROUTER_MCP_ROUTER": "gemini", "GEMINI_API_KEY": TOKEN})
    monkeypatch.setenv("GEMINI_API_KEY", "from-a-file")
    decider = routing.build()._owned_router.decider  # type: ignore[union-attr]
    assert decider._endpoint.headers["x-goog-api-key"] == TOKEN
    assert routing.capabilities(None)["router"] == "gemini(generativelanguage.googleapis.com)"
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        McpRouting.from_environ({"WEB_ROUTER_MCP_ROUTER": "gemini"})


def test_a_fallback_member_named_alone_has_its_key_taken_at_startup() -> None:
    environ = {
        "WEB_ROUTER_MCP_ROUTER": "fallback",
        "WEB_ROUTER_MCP_ROUTER_CONFIG": json.dumps({"routers": ["gemini", "rules"]}),
        "GEMINI_API_KEY": TOKEN,
    }
    config = McpRouting.from_environ(environ).router_config
    assert config is not None and config["routers"] == [{"name": "gemini", "api_key": TOKEN}, "rules"]


def test_a_routers_setting_of_another_router_is_left_as_given() -> None:
    member = {"name": "mine", "routers": ["gemini"]}
    environ = {
        "WEB_ROUTER_MCP_ROUTER": "fallback",
        "WEB_ROUTER_MCP_ROUTER_CONFIG": json.dumps({"routers": [member, {"name": "fallback", "routers": ["gemini"]}]}),
        "GEMINI_API_KEY": TOKEN,
    }
    config = McpRouting.from_environ(environ).router_config
    assert config is not None and config["routers"][0] == member
    assert config["routers"][1]["routers"] == [{"name": "gemini", "api_key": TOKEN}]
