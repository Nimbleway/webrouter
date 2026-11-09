"""``WEB_ROUTER_MCP_ROUTER=remote``: the operator's router service, read once from the process environment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.mcp.conftest import ok_on, outcome, routed
from tests.routing_remote_helpers import KEY, Recorder, running
from web_router.constants_mcp import ENV_MCP_ROUTER, MCP_TOOL_WEB_RUN
from web_router.constants_remote import ENV_REMOTE_ROUTER_API_KEY, ENV_REMOTE_ROUTER_URL
from web_router.exceptions import ConfigurationError
from web_router.mcp.routing_config import McpRouting
from web_router.mcp.tools import WebRouterTools


def remote_env(url: str, key: str | None = KEY) -> dict[str, str]:
    env = {ENV_MCP_ROUTER: "remote", ENV_REMOTE_ROUTER_URL: url}
    return env if key is None else {**env, ENV_REMOTE_ROUTER_API_KEY: key}


@pytest.mark.parametrize(
    "environ",
    [
        {ENV_MCP_ROUTER: "remote"},
        {ENV_MCP_ROUTER: "remote", ENV_REMOTE_ROUTER_URL: "  "},
    ],
)
def test_remote_without_a_url_stops_the_server_at_startup(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError, match=ENV_REMOTE_ROUTER_URL):
        McpRouting.from_environ(environ)


def test_a_plain_http_url_to_another_host_stops_the_server_when_the_router_is_built() -> None:
    routing = McpRouting.from_environ(remote_env("http://router.example.com/route"))
    with pytest.raises(ConfigurationError, match="must use https"):
        routing.build()


def test_the_capabilities_name_the_service_by_scheme_and_host_and_say_what_it_is_sent() -> None:
    routing = McpRouting.from_environ(remote_env("https://user:pw@router.example.com/v1/route?tenant=acme"))
    section = routing.capabilities(None)
    assert section["router"] == "remote(https://router.example.com)"
    assert section["router_runs"] == "remote"
    assert "https://router.example.com" in section["router_note"] and "candidate ids" in section["router_note"]
    shown = str(section) + repr(routing)
    for hidden in ("user", "pw", "v1", "tenant", "acme", KEY):
        assert hidden not in shown


async def test_web_run_plans_through_the_service_and_its_record_names_it(members: list[str]) -> None:
    recorder = Recorder()
    with running(recorder) as server:
        tools = routed(**remote_env(server.url))
        payload = await ok_on(tools, MCP_TOOL_WEB_RUN, prompt="read https://example.com/a", dry_run=True)
        await tools.router.aclose()
    assert payload["decision"]["router"] == "remote(http://127.0.0.1)"
    assert payload["tool"] == "extract" and recorder.bodies, "the service was asked"


async def test_the_direct_tools_never_ask_the_service(members: list[str]) -> None:
    recorder = Recorder()
    with running(recorder) as server:
        tools = routed(**remote_env(server.url))
        await ok_on(tools, "search", query="solar news")
    assert recorder.bodies == [], "route_tools=(): only web_run plans with the router"


async def test_load_env_never_sets_the_service_url_or_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    monkeypatch.delenv(ENV_MCP_ROUTER, raising=False)
    tools = WebRouterTools()
    env_file = tmp_path / "keys.env"
    env_file.write_text(
        f"{ENV_MCP_ROUTER}=remote\n{ENV_REMOTE_ROUTER_URL}=https://attacker.example/route\n"
        f"{ENV_REMOTE_ROUTER_API_KEY}=k\nTAVILY_API_KEY=t\n"
    )
    await outcome(tools, "load_env", path=str(env_file))
    assert tools.capabilities()["routing"]["router"] == "rules"
    assert ENV_REMOTE_ROUTER_URL not in os.environ and ENV_REMOTE_ROUTER_API_KEY not in os.environ
