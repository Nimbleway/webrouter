"""``WEB_ROUTER_MCP_ROUTER=<built-in>`` with ``WEB_ROUTER_MCP_ROUTER_CONFIG``: read once, keys captured at startup."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.mcp.conftest import ok_on, routed
from web_router.constants_mcp import ENV_MCP_ROUTER
from web_router.constants_mcp_router import ENV_MCP_ROUTER_CONFIG
from web_router.exceptions import ConfigurationError
from web_router.mcp.routing_config import McpRouting
from web_router.routing.registry import register_router, unregister_router

SECRET = "s3cret-router-key"


def named(router: str, config: Any, **env: str) -> dict[str, str]:
    return {ENV_MCP_ROUTER: router, ENV_MCP_ROUTER_CONFIG: json.dumps(config), **env}


FALLBACK = {
    "routers": [
        {"name": "remote", "url": "https://one.example.com:8443/v1/route", "api_key_env": "ONE_KEY"},
        {"name": "remote", "url": "https://two.example.com/route", "timeout_s": 0.5},
    ],
    "timeout_s": 2,
}


def test_a_fallback_of_two_services_captures_keys_and_names_each_host() -> None:
    environ = named("fallback", FALLBACK, ONE_KEY=SECRET)
    routing = McpRouting.from_environ(environ)
    first = routing.router_config["routers"][0]  # type: ignore[index]
    assert first["api_key"] == SECRET and "api_key_env" not in first
    environ["ONE_KEY"] = "changed"  # read once: a later change moves nothing
    assert routing.router_config["routers"][0]["api_key"] == SECRET  # type: ignore[index]
    section = routing.capabilities(None)
    assert section["router"] == "fallback(remote(https://one.example.com), remote(https://two.example.com))"
    assert section["router_runs"] == "remote"
    for host in ("https://one.example.com", "https://two.example.com"):
        assert host in section["router_note"] and host in section["prompt_note"]
    assert "one that times out included" in section["prompt_note"]
    shown = str(section) + repr(routing)
    for hidden in (SECRET, "8443", "v1"):
        assert hidden not in shown
    router = routing.build()
    assert router._router is not None and router._router.name == section["router"]
    assert router._router.timeout_s == 2


def test_an_unset_key_variable_is_captured_as_no_key() -> None:
    routing = McpRouting.from_environ(named("remote", {"url": "https://one.example.com", "api_key_env": "UNSET_KEY"}))
    assert routing.router_config == {"url": "https://one.example.com", "api_key": ""}
    assert routing.capabilities(None)["router"] == "remote(https://one.example.com)"


@pytest.mark.parametrize(
    ("raw", "why"),
    [('{"routers": [', "not valid JSON"), ('["remote"]', "not an object"), (f'"{SECRET}"', "not an object")],
)
def test_a_config_that_is_not_a_json_object_stops_the_server_without_echoing_it(raw: str, why: str) -> None:
    with pytest.raises(ConfigurationError, match=why) as refused:
        McpRouting.from_environ({ENV_MCP_ROUTER: "fallback", ENV_MCP_ROUTER_CONFIG: raw})
    assert SECRET not in str(refused.value) and "routers" not in str(refused.value)


def test_a_key_variable_that_is_not_a_name_stops_the_server() -> None:
    with pytest.raises(ConfigurationError, match="api_key_env must name an environment variable"):
        McpRouting.from_environ(named("fallback", {"routers": [{"name": "remote", "api_key_env": 7}]}))


@pytest.mark.parametrize("router", [None, "off", "tests.mcp.test_routing_named:SECRET"])
def test_a_config_for_no_built_in_router_stops_the_server(router: str | None) -> None:
    environ = {ENV_MCP_ROUTER_CONFIG: "{}"} if router is None else {ENV_MCP_ROUTER: router, ENV_MCP_ROUTER_CONFIG: "{}"}
    with pytest.raises(ConfigurationError, match="configures a built-in router"):
        McpRouting.from_environ(environ)


def test_a_built_in_whose_config_names_no_url_runs_here_and_says_what_it_may_send() -> None:
    routing = McpRouting.from_environ(named("fallback", {"routers": ["remote"]}))
    section = routing.capabilities(None)
    assert section["router"] == "fallback(remote)"
    assert section["router_runs"] == "built_in" and "model" in section["router_note"]
    with pytest.raises(ConfigurationError):
        routing.build()  # a remote member with no URL is refused when the router is built


def test_the_data_driven_router_runs_in_process_and_carries_no_note_about_a_model() -> None:
    section = McpRouting.from_environ(named("data-driven", {"browse": True})).capabilities(None)
    assert (section["router"], section["router_runs"]) == ("data-driven", "in_process")
    assert "router_note" not in section


@pytest.mark.parametrize("name", ["data-driven", "rules"])
def test_a_router_registered_over_the_built_in_name_is_not_reported_as_in_process(name: str) -> None:
    register_router(name, lambda **config: lambda req: None)
    try:
        env = {} if name == "data-driven" else {ENV_MCP_ROUTER: name}
        section = McpRouting.from_environ(env).capabilities(None)
    finally:
        unregister_router(name)
    assert (section["router"], section["router_runs"]) == (name, "built_in") and "model" in section["router_note"]
    assert McpRouting.from_environ({}).capabilities(None)["router_runs"] == "in_process"


def test_an_unknown_router_name_stops_the_server_and_lists_the_known_ones() -> None:
    with pytest.raises(ConfigurationError, match=r"names no router.*Known: .*data-driven"):
        McpRouting.from_environ({ENV_MCP_ROUTER: "missing-router"})


async def test_the_mcp_rules_router_builds_and_warns_once(members: list[str]) -> None:
    with pytest.warns(FutureWarning, match="rules.*one release.*data-driven.*router=None") as seen:
        tools = routed(**{ENV_MCP_ROUTER: "rules"})
        try:
            for _ in range(2):
                plan = await ok_on(tools, "web_run", prompt="summarize https://example.com/a", dry_run=True)
                assert (plan["tool"], plan["decision"]["router"]) == ("extract", "rules")
            section = tools.capabilities()["routing"]
            assert (section["router"], section["router_runs"]) == ("rules", "in_process")
        finally:
            await tools.router.aclose()
    assert len(seen) == 1 and members == []
