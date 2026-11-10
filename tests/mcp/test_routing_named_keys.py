"""A built-in decider named for ``web_run``: its preset's host shown, and its key from the process environment only."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from web_router.constants_mcp import ENV_MCP_ROUTER
from web_router.constants_mcp_router import ENV_MCP_ROUTER_CONFIG
from web_router.exceptions import ConfigurationError
from web_router.mcp import server
from web_router.mcp.routing_config import McpRouting

SECRET = "sk-process-key"
FROM_FILE = "sk-file-key"


def named(router: str, config: Any, **env: str) -> dict[str, str]:
    return {ENV_MCP_ROUTER: router, ENV_MCP_ROUTER_CONFIG: json.dumps(config), **env}


def sent_key(routing: McpRouting) -> str:
    """The bearer header the built router's decider would send."""
    built = routing.build()
    decider = built._owned_router.decider  # type: ignore[union-attr]
    return str(decider._endpoint.headers["Authorization"])


def test_an_llm_preset_shows_its_host_and_names_itself_as_the_record_does(monkeypatch: pytest.MonkeyPatch) -> None:
    routing = McpRouting.from_environ(named("llm", {"preset": "openai", "model": "m"}, OPENAI_API_KEY=SECRET))
    section = routing.capabilities(None)
    assert section["router"] == "llm(api.openai.com)"
    assert "https://api.openai.com" in section["router_note"] and "https://api.openai.com" in section["prompt_note"]
    monkeypatch.setenv("OPENAI_API_KEY", FROM_FILE)  # what a load_env or a later .env would do
    assert sent_key(routing) == f"Bearer {SECRET}", "the key captured at startup, never re-read"
    assert routing.build()._router.name == section["router"]  # type: ignore[union-attr]
    assert SECRET not in str(section) + repr(routing)


def test_a_systemone_preset_in_a_fallback_is_named_and_hosted_as_built() -> None:
    config = {
        "routers": [{"name": "systemone", "preset": "typesafe"}, {"name": "remote", "url": "https://r.example.com/x"}]
    }
    routing = McpRouting.from_environ(named("fallback", config, TYPESAFE_API_KEY=SECRET))
    section = routing.capabilities(None)
    assert section["router"] == "fallback(systemone(api.typesafe.ai), remote(https://r.example.com))"
    assert routing.build()._router.name == section["router"]  # type: ignore[union-attr]
    for host in ("https://api.typesafe.ai", "https://r.example.com"):
        assert host in section["router_note"]


def test_a_preset_key_unset_in_the_process_environment_stops_the_server() -> None:
    with pytest.raises(ConfigurationError, match=r"DEEPSEEK_API_KEY.*\.env file or load_env never"):
        McpRouting.from_environ(named("llm", {"preset": "deepseek", "model": "m"}))


@pytest.mark.parametrize(
    "config",
    [
        {"name": "remote", "url": "https://evil.example.com/route", "api_key_env": "OPENAI_API_KEY"},
        {"name": "llm", "preset": "deepseek", "model": "m", "api_key_env": "OPENAI_API_KEY"},
    ],
)
def test_a_vendors_variable_is_never_captured_for_another_host(config: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError, match=r"is sent only to api\.openai\.com"):
        McpRouting.from_environ(named("fallback", {"routers": [config]}, OPENAI_API_KEY=SECRET))


def test_the_entry_point_reads_the_router_before_any_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MCP_ROUTER, "llm")
    monkeypatch.setenv(ENV_MCP_ROUTER_CONFIG, json.dumps({"preset": "deepseek", "model": "m"}))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def project_file() -> object:
        os.environ["DEEPSEEK_API_KEY"] = FROM_FILE  # a project's .env filling an unset credential
        return object()

    monkeypatch.setattr(server.EnvSources, "at_startup", staticmethod(project_file))
    monkeypatch.setattr(server.anyio, "run", lambda *args: None)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        server.main()
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    ran: list[Any] = []
    monkeypatch.setattr(server.anyio, "run", lambda call: ran.append(call))
    server.main()
    assert ran[0].keywords["routing"].router_config["api_key"] == SECRET


def test_an_explicit_api_key_wins_over_api_key_env_and_reads_nothing() -> None:
    remote = {"name": "remote", "url": "https://r.example.com/x", "api_key": "explicit", "api_key_env": "ONE_KEY"}
    llm = {"name": "llm", "preset": "deepseek", "model": "m", "api_key": "explicit"}
    routing = McpRouting.from_environ(named("fallback", {"routers": [remote, llm]}, ONE_KEY=SECRET))
    members = routing.router_config["routers"]  # type: ignore[index]
    assert [m["api_key"] for m in members] == ["explicit", "explicit"], "no DEEPSEEK_API_KEY needed or read"
    assert all("api_key_env" not in m for m in members)


def test_a_null_api_key_still_captures_the_preset_key_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"preset": "deepseek", "model": "m", "api_key": None}
    routing = McpRouting.from_environ(named("llm", config, DEEPSEEK_API_KEY=SECRET))
    monkeypatch.setenv("DEEPSEEK_API_KEY", FROM_FILE)
    assert sent_key(routing) == f"Bearer {SECRET}"
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        McpRouting.from_environ(named("llm", config))


def test_a_member_name_with_spaces_is_keyed_named_and_hosted_as_its_router() -> None:
    member = {"name": "llm ", "preset": "deepseek", "model": "m"}
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        McpRouting.from_environ(named("fallback", {"routers": [member]}))
    routing = McpRouting.from_environ(named("fallback", {"routers": [member]}, DEEPSEEK_API_KEY=SECRET))
    section = routing.capabilities(None)
    assert section["router"] == "fallback(llm(api.deepseek.com))"
    assert "https://api.deepseek.com" in section["router_note"]
    assert routing.build()._router.name == section["router"]  # type: ignore[union-attr]
