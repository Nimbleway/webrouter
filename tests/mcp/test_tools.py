"""The MCP tool surface, driven through the handlers rather than a subprocess.

No network and no stdio: every test here calls the same functions the server's
handlers call, against the in-memory fakes from `tests/conftest.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeBrowseProvider, FakeResearchProvider
from tests.mcp.conftest import FAKE
from tests.providers._answer_stub import stub_answer_api
from web_router.client import AsyncWebRouter
from web_router.constants import (
    ENV_BRAVE_API_KEY,
    ENV_BROWSER_USE_API_KEY,
    ENV_BROWSERBASE_API_KEY,
    ENV_HYPERBROWSER_API_KEY,
    ENV_JINA_API_KEY,
    ENV_LINKUP_API_KEY,
    ENV_MCP_ENV_FILE,
    ENV_NIMBLE_API_KEY,
    ENV_PERPLEXITY_API_KEY,
    ENV_TAVILY_API_KEY,
    MCP_BROWSER_TOOLS,
    MCP_METHOD_TEMPLATE,
    MCP_REDACTED,
    MCP_RESOURCE_URI,
    MCP_TOOL_ANSWER,
    MCP_TOOL_BROWSE,
    MCP_TOOL_BROWSE_STATUS,
    MCP_TOOL_BROWSER_OPEN,
    MCP_TOOL_EXTRACT,
    MCP_TOOL_FETCH_RAW,
    MCP_TOOL_LOAD_ENV,
    MCP_TOOL_RESEARCH,
    MCP_TOOL_RESEARCH_STATUS,
    MCP_TOOL_RUN_TEMPLATE,
    MCP_TOOL_SEARCH,
    MCP_TOOL_TEMPLATE,
    MCP_TOOL_TEMPLATES,
    PROVIDER_AUTO,
    PROVIDER_BRAVE,
    PROVIDER_BROWSERBASE,
    PROVIDER_EXA_MCP,
    PROVIDER_HYPERBROWSER,
    PROVIDER_JINA,
    PROVIDER_LINKUP,
    PROVIDER_MAX,
    PROVIDER_PARALLEL_MCP,
    PROVIDER_TAVILY,
    PROVIDER_YOU_MCP,
)
from web_router.mcp.env import user_config_dir
from web_router.mcp.routing import ROUTING_TOOLS
from web_router.mcp.server import call_tool, list_resources, list_tools, read_resource
from web_router.mcp.tools import WebRouterTools
from web_router.registry import (
    get_browser_provider_class,
    register_browse_provider,
    register_research_provider,
    unregister_browse_provider,
    unregister_research_provider,
)
from web_router.types import BrowseProgress, BrowseRequest, BrowseStep


def _tool(session: WebRouterTools, name: str) -> Any:
    return next(tool for tool in list_tools(session).tools if tool.name == name)


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    return dict(json.loads(result.content[0].text))


def test_serves_exactly_twenty_four_tools(tools: WebRouterTools) -> None:
    assert [tool.name for tool in list_tools(tools).tools] == [
        MCP_TOOL_SEARCH,
        MCP_TOOL_EXTRACT,
        MCP_TOOL_TEMPLATES,
        MCP_TOOL_TEMPLATE,
        MCP_TOOL_RUN_TEMPLATE,
        MCP_TOOL_ANSWER,
        MCP_TOOL_RESEARCH,
        MCP_TOOL_RESEARCH_STATUS,
        MCP_TOOL_BROWSE,
        MCP_TOOL_BROWSE_STATUS,
        MCP_TOOL_FETCH_RAW,
        MCP_TOOL_LOAD_ENV,
        *ROUTING_TOOLS,
        *MCP_BROWSER_TOOLS,
    ]


def test_provider_enum_offers_only_configured_backends(tools: WebRouterTools, no_keys: None) -> None:
    search = _tool(tools, MCP_TOOL_SEARCH)
    extract = _tool(tools, MCP_TOOL_EXTRACT)

    assert search.input_schema["properties"]["provider"]["enum"] == [
        PROVIDER_AUTO,
        PROVIDER_EXA_MCP,
        PROVIDER_PARALLEL_MCP,
        PROVIDER_YOU_MCP,
    ]
    assert extract.input_schema["properties"]["provider"]["enum"] == [PROVIDER_AUTO, PROVIDER_JINA, PROVIDER_MAX]
    assert PROVIDER_TAVILY not in search.input_schema["properties"]["provider"]["enum"]


def test_the_extract_schema_reads_max_as_the_members_it_would_race(no_keys: None) -> None:
    """``max`` declares every option as ``auto`` does, so it is read as its chain, never as its own class."""
    from web_router.mcp.tools import _serving
    from web_router.providers.max import MaxExtractProvider

    router = AsyncWebRouter(PROVIDER_MAX)
    serving = _serving(MCP_TOOL_EXTRACT, [PROVIDER_MAX], router)

    assert serving, "the keyless members are what max would race"
    assert MaxExtractProvider not in serving
    assert all(cls.name in router.get_extract_provider(PROVIDER_MAX).members for cls in serving)  # type: ignore[attr-defined]


def test_a_router_whose_default_is_max_still_lists_every_tool(no_keys: None) -> None:
    """``max`` is extract's alone: as the default it serves no search or browser, and breaks neither schema."""
    session = WebRouterTools(AsyncWebRouter(PROVIDER_MAX))

    names = {tool.name for tool in list_tools(session).tools}

    assert {MCP_TOOL_SEARCH, MCP_TOOL_BROWSER_OPEN, MCP_TOOL_EXTRACT} <= names
    assert _tool(session, MCP_TOOL_SEARCH).input_schema["properties"]
    assert _tool(session, MCP_TOOL_BROWSER_OPEN).input_schema["properties"]


def test_a_method_with_only_delegating_providers_configured_is_not_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``max`` needs no key of its own any more than ``auto`` does, so neither counts as a backend that can run."""
    from web_router.mcp import tools as module

    monkeypatch.setitem(module._CONFIGURED, MCP_TOOL_EXTRACT, lambda: [PROVIDER_AUTO, PROVIDER_MAX])

    assert not module._usable(MCP_TOOL_EXTRACT)


def test_the_extract_tool_says_what_max_costs(tools: WebRouterTools) -> None:
    description = _tool(tools, MCP_TOOL_EXTRACT).description
    assert "provider='max'" in description and "one fetch per backend" in description


def test_a_key_appears_in_the_enum_for_every_method_it_serves(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_LINKUP_API_KEY, "x")

    for name in (MCP_TOOL_SEARCH, MCP_TOOL_EXTRACT, MCP_TOOL_RESEARCH):
        assert PROVIDER_LINKUP in _tool(tools, name).input_schema["properties"]["provider"]["enum"]


def test_an_option_no_configured_route_maps_is_not_advertised(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Search `auto` declares every filter Nimble maps, but routes each request
    # to a member that maps it, so it is read as the chain it would try: with
    # no key the domain, country and locale filters would each come back as an
    # UnsupportedParameterError from the default route.
    tools = WebRouterTools(AsyncWebRouter(), research_wait=0.0, browse_wait=0.0)
    search = set(_tool(tools, MCP_TOOL_SEARCH).input_schema["properties"])
    research = set(_tool(tools, MCP_TOOL_RESEARCH).input_schema["properties"])

    assert search == {"query", "max_results", "search_depth", "provider"}
    assert {"include_domains", "exclude_domains", "records"}.isdisjoint(research)

    # A backend that maps them brings them back, for the model to name through `provider`.
    monkeypatch.setenv(ENV_NIMBLE_API_KEY, "x")
    assert {"include_domains", "country"} <= set(_tool(tools, MCP_TOOL_SEARCH).input_schema["properties"])
    assert "include_domains" in _tool(tools, MCP_TOOL_RESEARCH).input_schema["properties"]


def test_extract_offers_no_way_to_name_a_saved_profile(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A logged-in identity is set in code, as on browser_open; one sent anyway is dropped before the library."""
    monkeypatch.setenv(ENV_BROWSERBASE_API_KEY, "x")
    tools = WebRouterTools(AsyncWebRouter(), research_wait=0.0, browse_wait=0.0)
    extract = _tool(tools, MCP_TOOL_EXTRACT)

    assert PROVIDER_BROWSERBASE in extract.input_schema["properties"]["provider"]["enum"]
    assert "profile" not in extract.input_schema["properties"]
    assert "profile" not in tools._kwargs(MCP_TOOL_EXTRACT, {"urls": ["https://example.com"], "profile": "ctx-1"})


def test_browser_open_offers_only_the_options_and_levels_its_members_serve(
    no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Browser `auto` declares every option, since it routes each request to a
    # member that serves it, so it is read as the chain it would try: keyless,
    # that is `local` alone, with no stealth, proxy or CAPTCHA solving.
    tools = WebRouterTools(AsyncWebRouter(), research_wait=0.0, browse_wait=0.0)
    offered = set(_tool(tools, MCP_TOOL_BROWSER_OPEN).input_schema["properties"])

    assert {"stealth", "proxy_country", "solve_captchas", "idle_timeout"}.isdisjoint(offered)
    assert {"headless", "timeout", "viewport"} <= offered

    # A vendor with two levels brings `stealth` back with those two, and not the third.
    hyperbrowser = get_browser_provider_class(PROVIDER_HYPERBROWSER)
    monkeypatch.setattr(hyperbrowser, "supported_parameters", hyperbrowser.supported_parameters | {"stealth"})
    monkeypatch.setattr(hyperbrowser, "stealth_levels", frozenset({"high", "low"}))
    monkeypatch.setenv(ENV_HYPERBROWSER_API_KEY, "x")

    # A new router, as a key set after one resolved its chain would not reach its requests either.
    tools = WebRouterTools(AsyncWebRouter(), research_wait=0.0, browse_wait=0.0)
    stealth = _tool(tools, MCP_TOOL_BROWSER_OPEN).input_schema["properties"]["stealth"]
    assert stealth["enum"] == ["low", "high"]


def test_search_reads_the_auto_chain_the_router_was_given(no_keys: None) -> None:
    # A keyed chain is what search tries, so its filters are offered with no key in the environment.
    keyed = AsyncWebRouter(provider_config={"auto": {"chain": {"search": [("nimble", {"api_key": "x"})]}}})
    offered = set(_tool(WebRouterTools(keyed), MCP_TOOL_SEARCH).input_schema["properties"])
    assert {"include_domains", "country", "locale"} <= offered


def test_browser_open_reads_the_auto_chain_the_router_was_given(no_keys: None) -> None:
    # A `chain` in provider_config is what open_browser tries, so it is what the
    # schema offers -- not the keyless default the environment would have chosen.
    cloud = AsyncWebRouter(provider_config={"auto": {"chain": (("hyperbrowser", {"api_key": "x"}),)}})
    offered = set(_tool(WebRouterTools(cloud), MCP_TOOL_BROWSER_OPEN).input_schema["properties"])
    assert {"stealth", "proxy_country", "solve_captchas"} <= offered


def test_research_says_so_when_nothing_can_serve_it(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every research backend bills for the run, so with no key at all the tool
    # is dead and its description has to admit it.
    assert "NOTHING IS CONFIGURED" in _tool(tools, MCP_TOOL_RESEARCH).description
    assert ENV_LINKUP_API_KEY in _tool(tools, MCP_TOOL_RESEARCH).description

    monkeypatch.setenv(ENV_LINKUP_API_KEY, "x")
    assert "NOTHING IS CONFIGURED" not in _tool(tools, MCP_TOOL_RESEARCH).description
    # Search always has a keyless backend, so it is never dead.
    assert "NOTHING IS CONFIGURED" not in _tool(tools, MCP_TOOL_SEARCH).description


def test_the_wait_budget_is_in_the_research_description(tools: WebRouterTools) -> None:
    assert "waits 0s" in _tool(tools, MCP_TOOL_RESEARCH).description


async def test_search_round_trip(tools: WebRouterTools) -> None:
    payload = _payload(await call_tool(tools, MCP_TOOL_SEARCH, {"query": "hello", "max_results": 3}))

    assert payload["provider"] == FAKE
    assert payload["query"] == "hello"
    assert payload["results"][0]["url"] == "https://example.com"
    assert payload["handle"].startswith(MCP_TOOL_SEARCH)
    assert payload["truncated"] is False


async def test_arguments_reach_the_library_unchanged(tools: WebRouterTools, fake_provider: Any) -> None:
    await call_tool(
        tools,
        MCP_TOOL_SEARCH,
        {"query": "hello", "max_results": 2, "search_depth": "deep", "include_domains": ["example.com"]},
    )

    request = tools.router.get_provider(FAKE).requests[-1]  # type: ignore[attr-defined]
    assert (request.max_results, request.search_depth, request.include_domains) == (2, "deep", ["example.com"])
    # Unset stays unset, as everywhere else in the package.
    assert request.country is None


async def test_extract_drops_raw_and_fetch_raw_returns_it(tools: WebRouterTools) -> None:
    shaped = _payload(await call_tool(tools, MCP_TOOL_EXTRACT, {"urls": ["https://example.com"], "format": "text"}))

    assert "raw" not in shaped
    assert shaped["documents"][0]["format"] == "text"

    raw = _payload(await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": shaped["handle"]}))
    assert raw["raw"] == [{"urls": ["https://example.com"]}]


async def test_templates_lists_the_catalogue_and_template_describes_one(tools: WebRouterTools) -> None:
    listed = _payload(await call_tool(tools, MCP_TOOL_TEMPLATES, {"limit": 10, "provider": FAKE}))

    assert listed["provider"] == FAKE
    assert [row["name"] for row in listed["templates"]] == ["books_pdp", "jobs_listing"]
    assert listed["total"] == 2 and listed["limit"] == 10
    # A listing is the few words a template is picked by, plus what a run must pass; the schemas are `template`'s.
    assert "input_schema" not in listed["templates"][0]
    assert listed["templates"][0]["required_inputs"] == ["url"]
    assert listed["templates"][0]["sample_inputs"] == {"url": "https://example.com/p/1"}
    assert listed["handle"].startswith(MCP_TOOL_TEMPLATES)

    described = _payload(await call_tool(tools, MCP_TOOL_TEMPLATE, {"name": "books_pdp", "provider": FAKE}))

    assert described["template"]["name"] == "books_pdp"
    assert described["template"]["input_schema"]["required"] == ["url"]
    assert described["template"]["output_schema"]["properties"]["title"] == {"type": "string"}
    assert described["template"]["samples"] == [{"input": {"url": "https://example.com/p/1"}, "output": {"title": "A"}}]
    assert tools.router.get_template_provider(FAKE).listed == [(10, None)]  # type: ignore[attr-defined]


async def test_run_template_passes_the_inputs_through_and_returns_the_record(tools: WebRouterTools) -> None:
    inputs = {"url": "https://example.com/page"}
    shaped = _payload(
        await call_tool(tools, MCP_TOOL_RUN_TEMPLATE, {"template": "books_pdp", "inputs": inputs, "provider": FAKE})
    )

    assert shaped["provider"] == FAKE
    assert shaped["template"] == "books_pdp"
    assert shaped["data"] == {"title": "Example", "inputs": inputs}
    assert shaped["document"] is None, "no format asked for means no page beside the record"
    assert "raw" not in shaped and "inputs" not in shaped

    request = tools.router.get_template_provider(FAKE).requests[-1]  # type: ignore[attr-defined]
    assert request.inputs == inputs and request.format is None

    raw = _payload(await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": shaped["handle"]}))
    assert raw["raw"] == {"template": "books_pdp", "inputs": inputs}


async def test_run_template_with_a_format_returns_the_page_beside_the_record(tools: WebRouterTools) -> None:
    shaped = _payload(
        await call_tool(
            tools,
            MCP_TOOL_RUN_TEMPLATE,
            {"template": "books_pdp", "inputs": {}, "format": "markdown", "provider": FAKE},
        )
    )

    assert shaped["document"]["format"] == "markdown"
    assert shaped["document"]["content"] == "# Page"


async def test_run_template_without_the_template_names_it(tools: WebRouterTools) -> None:
    result = await call_tool(tools, MCP_TOOL_RUN_TEMPLATE, {"inputs": {"url": "https://example.com"}, "provider": FAKE})

    assert result.is_error
    assert "'template'" in result.content[0].text


async def test_the_template_tools_require_the_provider(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A template's name belongs to one vendor, so these three tools alone make `provider` required."""
    # A key, so the schema has a `provider` to require; with none the tool is dead and lists no provider at all.
    monkeypatch.setenv(ENV_NIMBLE_API_KEY, "x")
    for name, arguments in (
        (MCP_TOOL_TEMPLATES, {}),
        (MCP_TOOL_TEMPLATE, {"name": "books_pdp"}),
        (MCP_TOOL_RUN_TEMPLATE, {"template": "books_pdp"}),
    ):
        result = await call_tool(tools, name, arguments)
        assert result.is_error and "'provider'" in result.content[0].text, name
        assert "provider" in _tool(tools, name).input_schema["required"]
    assert "provider" not in _tool(tools, MCP_TOOL_EXTRACT).input_schema["required"]


def test_the_template_tools_say_so_when_nothing_can_serve_them(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A template run fetches on the backend's account, so with no key the
    # three tools are dead and say so, naming the variable that would fix it.
    for name in (MCP_TOOL_TEMPLATES, MCP_TOOL_TEMPLATE, MCP_TOOL_RUN_TEMPLATE):
        assert "NOTHING IS CONFIGURED" in _tool(tools, name).description
        assert ENV_NIMBLE_API_KEY in _tool(tools, name).description
        schema = _tool(tools, name).input_schema
        assert "provider" not in schema["properties"] and "provider" not in schema["required"]

    monkeypatch.setenv(ENV_NIMBLE_API_KEY, "x")
    for name in (MCP_TOOL_TEMPLATES, MCP_TOOL_TEMPLATE, MCP_TOOL_RUN_TEMPLATE):
        assert "NOTHING IS CONFIGURED" not in _tool(tools, name).description
        assert _tool(tools, name).input_schema["properties"]["provider"]["enum"] == ["nimble"]


def test_the_capabilities_key_the_template_tools_method_once(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.loads(read_resource(tools, MCP_RESOURCE_URI).contents[0].text)

    assert body[MCP_METHOD_TEMPLATE] == [], "no keyless tier and no auto"
    assert MCP_TOOL_TEMPLATES not in body and MCP_TOOL_RUN_TEMPLATE not in body
    assert body["unlock"][MCP_METHOD_TEMPLATE] == {"nimble": [ENV_NIMBLE_API_KEY]}

    monkeypatch.setenv(ENV_NIMBLE_API_KEY, "x")
    assert MCP_METHOD_TEMPLATE not in tools.capabilities()["unlock"]


async def test_fetch_raw_refuses_a_handle_it_never_issued(tools: WebRouterTools) -> None:
    result = await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": "search-9999"})

    assert result.is_error
    assert "Unknown handle" in result.content[0].text


async def test_research_that_finishes_inside_the_budget_answers_outright(tools: WebRouterTools) -> None:
    payload = _payload(await call_tool(tools, MCP_TOOL_RESEARCH, {"query": "why", "effort": "low"}))

    assert payload["state"] == "completed"
    assert payload["answer"] == "the answer"
    assert payload["citations"][0]["url"] == "https://example.com"


async def test_a_slow_research_run_comes_back_as_a_handle_to_poll(tools: WebRouterTools) -> None:
    class SlowResearchProvider(FakeResearchProvider):
        polls_before_done = 2

    register_research_provider(FAKE, SlowResearchProvider)
    try:
        pending = _payload(await call_tool(tools, MCP_TOOL_RESEARCH, {"query": "why"}))

        assert pending["state"] == "running"
        assert "research_status" in pending["note"]
        assert "answer" not in pending

        answered = _payload(await call_tool(tools, MCP_TOOL_RESEARCH_STATUS, {"handle": pending["handle"]}))
        assert answered["state"] == "completed"
        assert answered["answer"] == "the answer"
        # The poll's payload is kept too, so the handle keeps working afterwards.
        assert (await call_tool(tools, MCP_TOOL_RESEARCH_STATUS, {"handle": pending["handle"]})).is_error is False
    finally:
        unregister_research_provider(FAKE)


async def test_research_status_reports_a_failed_run_as_its_state(tools: WebRouterTools) -> None:
    class StuckResearchProvider(FakeResearchProvider):
        polls_before_done = 2

    register_research_provider(FAKE, StuckResearchProvider)
    try:
        pending = _payload(await call_tool(tools, MCP_TOOL_RESEARCH, {"query": "why"}))
        StuckResearchProvider.fails_with = "the backend gave up"

        payload = _payload(await call_tool(tools, MCP_TOOL_RESEARCH_STATUS, {"handle": pending["handle"]}))
        assert payload["state"] == "failed"
        assert payload["detail"] == "the backend gave up"
    finally:
        StuckResearchProvider.fails_with = None
        unregister_research_provider(FAKE)


async def test_a_library_error_comes_back_as_a_tool_error_with_its_own_message(
    tools: WebRouterTools, no_keys: None
) -> None:
    result = await call_tool(tools, MCP_TOOL_SEARCH, {"query": "hello", "provider": PROVIDER_TAVILY})

    assert result.is_error
    # The library already names the variable to set; this layer must not rewrite it.
    assert ENV_TAVILY_API_KEY in result.content[0].text


async def test_an_unknown_tool_is_an_error_result_not_an_exception(tools: WebRouterTools) -> None:
    result = await call_tool(tools, "teleport", {})

    assert result.is_error
    assert "Unknown tool" in result.content[0].text


async def test_a_missing_required_argument_names_it(tools: WebRouterTools) -> None:
    result = await call_tool(tools, MCP_TOOL_SEARCH, {"max_results": 3})

    assert result.is_error
    assert "'query'" in result.content[0].text


def test_the_capabilities_resource_lists_what_this_install_serves(tools: WebRouterTools, no_keys: None) -> None:
    assert [resource.uri for resource in list_resources().resources] == [MCP_RESOURCE_URI]

    body = json.loads(read_resource(tools, MCP_RESOURCE_URI).contents[0].text)
    assert body[MCP_TOOL_SEARCH] == [PROVIDER_AUTO, PROVIDER_EXA_MCP, PROVIDER_PARALLEL_MCP, PROVIDER_YOU_MCP]
    assert body[MCP_TOOL_RESEARCH] == [PROVIDER_AUTO]
    assert body[MCP_TOOL_ANSWER] == [], "no keyless tier and no auto"
    # Every browse backend bills, so with no key there is no keyless tier to list.
    assert set(body[MCP_TOOL_BROWSE]) <= {PROVIDER_AUTO}
    assert body["default_provider"] == FAKE


def test_with_no_key_the_capabilities_say_how_to_add_one(tools: WebRouterTools, no_keys: None) -> None:
    env = tools.capabilities()["env"]

    assert ENV_MCP_ENV_FILE in env["hint"]
    assert str(user_config_dir()) in env["hint"]
    assert MCP_TOOL_LOAD_ENV in env["hint"]


def test_a_key_takes_the_hint_away(tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "x")

    assert "hint" not in tools.capabilities()["env"]


def test_a_keyless_backends_optional_key_is_not_a_configured_key(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Jina answers without one; a key only raises its rate limit, and configures nothing new.
    monkeypatch.setenv(ENV_JINA_API_KEY, "x")

    assert tools.keyed() is False


def test_with_no_key_search_and_the_instructions_say_so(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = _tool(tools, MCP_TOOL_SEARCH).description
    assert "No API keys were found" in search and MCP_TOOL_LOAD_ENV in search
    assert "No API keys were found" in tools.instructions()

    monkeypatch.setenv(ENV_BRAVE_API_KEY, "x")

    assert "No API keys were found" not in _tool(tools, MCP_TOOL_SEARCH).description
    assert "No API keys were found" not in tools.instructions()


def _env_file(directory: Path, text: str) -> Path:
    path = directory / "keys.env"
    path.write_text(text, encoding="utf-8")
    return path


async def test_load_env_configures_a_backend_without_a_restart(isolated_home: Path) -> None:
    session = WebRouterTools()
    assert PROVIDER_BRAVE not in session.router.get_provider(PROVIDER_AUTO).members  # type: ignore[attr-defined]
    path = _env_file(isolated_home, f"{ENV_BRAVE_API_KEY}=not-a-real-key-0123\n")

    result = await call_tool(session, MCP_TOOL_LOAD_ENV, {"path": str(path)})
    payload = _payload(result)

    assert payload["file"] == str(path)
    assert payload["loaded"] == [ENV_BRAVE_API_KEY]
    assert PROVIDER_BRAVE in payload["configured"][MCP_TOOL_SEARCH]
    assert "not-a-real-key-0123" not in result.content[0].text
    # The next call is served by a router that resolved `auto` again, from the environment as it now is.
    assert PROVIDER_BRAVE in session.router.get_provider(PROVIDER_AUTO).members  # type: ignore[attr-defined]
    assert PROVIDER_BRAVE in _tool(session, MCP_TOOL_SEARCH).input_schema["properties"]["provider"]["enum"]
    assert session.tools_changed() is True
    assert session.tools_changed() is False, "the client is told once"
    await session.aclose()


async def test_load_env_that_sets_nothing_new_changes_nothing(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "exported")
    session = WebRouterTools()
    router = session.router
    path = _env_file(isolated_home, f"{ENV_BRAVE_API_KEY}=not-a-real-key-0123\n")

    payload = _payload(await call_tool(session, MCP_TOOL_LOAD_ENV, {"path": str(path)}))

    assert (payload["loaded"], payload["already_set"]) == ([], [ENV_BRAVE_API_KEY])
    assert "already set" in payload["note"]
    assert session.router is router and session.tools_changed() is False
    await session.aclose()


async def test_load_env_remembers_the_path_when_asked(isolated_home: Path) -> None:
    session = WebRouterTools()
    path = _env_file(isolated_home, f"{ENV_BRAVE_API_KEY}=not-a-real-key-0123\n")

    payload = _payload(await call_tool(session, MCP_TOOL_LOAD_ENV, {"path": str(path), "remember": True}))

    assert str(user_config_dir()) in payload["note"]
    await session.aclose()


@pytest.mark.parametrize("arguments", [{"path": 3}, {"path": "x", "remember": "true"}])
async def test_load_env_refuses_arguments_of_the_wrong_type(
    tools: WebRouterTools, isolated_home: Path, arguments: dict[str, Any]
) -> None:
    result = await call_tool(tools, MCP_TOOL_LOAD_ENV, arguments)

    assert result.is_error
    assert "`remember` as a boolean" in result.content[0].text


async def test_load_env_reports_a_bad_path_as_a_tool_error_the_model_can_relay(
    tools: WebRouterTools, isolated_home: Path
) -> None:
    missing = isolated_home / "nope.env"
    unrelated = _env_file(isolated_home, "HOSTNAME=example\nSECRET_THING=do-not-echo\n")

    gone = await call_tool(tools, MCP_TOOL_LOAD_ENV, {"path": str(missing)})
    empty = await call_tool(tools, MCP_TOOL_LOAD_ENV, {"path": str(unrelated)})

    assert gone.is_error and str(missing) in gone.content[0].text
    assert empty.is_error and "none of the provider credentials" in empty.content[0].text
    assert "do-not-echo" not in empty.content[0].text
    assert tools.tools_changed() is False


_SECRET = "SEKRIT"


class LeakyBrowseProvider(FakeBrowseProvider):
    """A backend whose finished task hands back every way into its session a real vendor has been seen to."""

    def _progress(self, request: BrowseRequest) -> BrowseProgress:
        progress = super()._progress(request)
        if progress.response is None:
            return progress
        response = progress.response.model_copy(
            update={
                "recording_url": f"https://recordings.example.test/task-1.mp4?apiKey={_SECRET}",
                "steps": [BrowseStep(action="navigate", url=f"https://example.com/?jwt={_SECRET}", detail="opened")],
                "raw": [
                    {
                        "status": "FINISHED",
                        "liveViewUrl": f"https://live.example.test/view/{_SECRET}",
                        "debuggerFullscreenUrl": f"https://debug.example.test/{_SECRET}",
                        "session": {
                            "connectUrl": f"https://connect.example.test/{_SECRET}",
                            "wsEndpoint": f"wss://cdp.example.test/{_SECRET}",
                            "signingKey": _SECRET,
                        },
                        "log": [f"attached via https://cdp.example.test/devtools?token={_SECRET} and continued"],
                        "pages": ["https://example.com/results"],
                    }
                ],
            }
        )
        return progress.model_copy(update={"response": response})


def test_browse_takes_exactly_the_routers_arguments(tools: WebRouterTools, no_keys: None) -> None:
    schema = _tool(tools, MCP_TOOL_BROWSE).input_schema

    # `provider` is there with no key at all because `auto` is, as for research.
    assert set(schema["properties"]) == {
        "task",
        "start_url",
        "output_schema",
        "max_steps",
        "allowed_domains",
        "provider",
    }
    assert schema["properties"]["provider"]["enum"] == [PROVIDER_AUTO]
    assert schema["required"] == ["task"]
    assert "waits 0s" in _tool(tools, MCP_TOOL_BROWSE).description


def test_browse_says_so_when_nothing_can_serve_it(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every browse backend bills, so with no key the tool is dead, as research is.
    assert "NOTHING IS CONFIGURED" in _tool(tools, MCP_TOOL_BROWSE).description
    assert ENV_BROWSER_USE_API_KEY in _tool(tools, MCP_TOOL_BROWSE).description

    monkeypatch.setenv(ENV_BROWSER_USE_API_KEY, "x")
    assert "NOTHING IS CONFIGURED" not in _tool(tools, MCP_TOOL_BROWSE).description


async def test_browse_that_finishes_inside_the_budget_answers_outright(tools: WebRouterTools) -> None:
    payload = _payload(
        await call_tool(
            tools,
            MCP_TOOL_BROWSE,
            {
                "task": "find the price",
                "start_url": "https://example.com",
                "output_schema": {"type": "object"},
                "max_steps": 5,
                "allowed_domains": ["example.com"],
            },
        )
    )

    assert payload["state"] == "completed"
    assert payload["answer"] == "the answer"
    assert payload["structured"] == {"ok": True}
    assert payload["steps"] == [
        {"action": "navigate", "url": "https://example.com", "detail": None, "screenshot_url": None}
    ]
    assert payload["handle"].startswith(MCP_TOOL_BROWSE)
    assert "raw" not in payload
    request = tools.router.get_browse_provider(FAKE).requests[-1]  # type: ignore[attr-defined]
    assert (request.start_url, request.max_steps, request.allowed_domains) == (
        "https://example.com",
        5,
        ["example.com"],
    )


async def test_a_slow_browse_task_comes_back_as_a_handle_to_poll(tools: WebRouterTools) -> None:
    class SlowBrowseProvider(FakeBrowseProvider):
        polls_before_done = 2

    register_browse_provider(FAKE, SlowBrowseProvider)
    try:
        pending = _payload(await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"}))

        assert pending["state"] == "running"
        assert "browse_status" in pending["note"]
        assert "answer" not in pending

        answered = _payload(await call_tool(tools, MCP_TOOL_BROWSE_STATUS, {"handle": pending["handle"]}))
        assert answered["state"] == "completed"
        assert answered["answer"] == "the answer"
    finally:
        unregister_browse_provider(FAKE)


async def test_browse_status_reports_a_failed_task_as_its_state(tools: WebRouterTools) -> None:
    class StuckBrowseProvider(FakeBrowseProvider):
        polls_before_done = 2

    register_browse_provider(FAKE, StuckBrowseProvider)
    try:
        pending = _payload(await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"}))
        StuckBrowseProvider.reported = "FAILED"

        payload = _payload(await call_tool(tools, MCP_TOOL_BROWSE_STATUS, {"handle": pending["handle"]}))
        assert payload["state"] == "failed"
        assert payload["detail"] == "the agent is failed"
    finally:
        StuckBrowseProvider.reported = None
        unregister_browse_provider(FAKE)


async def test_each_status_tool_polls_only_its_own_jobs(tools: WebRouterTools) -> None:
    browsed = _payload(await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"}))
    researched = _payload(await call_tool(tools, MCP_TOOL_RESEARCH, {"query": "why"}))

    wrong_way = await call_tool(tools, MCP_TOOL_RESEARCH_STATUS, {"handle": browsed["handle"]})
    other_way = await call_tool(tools, MCP_TOOL_BROWSE_STATUS, {"handle": researched["handle"]})

    assert wrong_way.is_error and "'browse'" in wrong_way.content[0].text
    assert other_way.is_error and "'research'" in other_way.content[0].text


async def test_no_browse_output_carries_a_session_url_or_a_credential(tools: WebRouterTools) -> None:
    register_browse_provider(FAKE, LeakyBrowseProvider)
    try:
        shaped = await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"})
        raw = await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": _payload(shaped)["handle"]})
    finally:
        unregister_browse_provider(FAKE)

    for result in (shaped, raw):
        assert not result.is_error
        assert _SECRET not in result.content[0].text
    body = _payload(raw)["raw"][0]
    assert body["liveViewUrl"] == body["debuggerFullscreenUrl"] == MCP_REDACTED
    assert body["session"] == {"connectUrl": MCP_REDACTED, "wsEndpoint": MCP_REDACTED, "signingKey": MCP_REDACTED}
    # Only the URL is taken out of a longer string, and a URL with nothing to hide is left alone.
    assert body["log"] == [f"attached via {MCP_REDACTED} and continued"]
    assert body["pages"] == ["https://example.com/results"]
    assert body["status"] == "FINISHED"
    assert "redacted" in _payload(raw)["note"]
    # The shaped result says so too: the recording URL and the step URL were replaced.
    assert "2 value(s)" in _payload(shaped)["note"]


class BrowserbaseStyleBrowseProvider(FakeBrowseProvider):
    """A finished run shaped like Browserbase's: its live session's id in plain text beside the run's own."""

    def _progress(self, request: BrowseRequest) -> BrowseProgress:
        progress = super()._progress(request)
        if progress.response is None:
            return progress
        raw = [
            {
                "runId": "run-7",
                "status": "COMPLETED",
                "sessionId": "0f6a1c2e-5b7d-4e3a-9c1f-2d8b7a6e5f40",
                "session": {"browserSessionId": "bb-session-42", "session_ids": ["bb-1", 7]},
                "result": "the price is 9.99",
            }
        ]
        return progress.model_copy(update={"response": progress.response.model_copy(update={"raw": raw})})


async def test_fetch_raw_redacts_a_plain_session_id_that_looks_nothing_like_a_url(tools: WebRouterTools) -> None:
    register_browse_provider(FAKE, BrowserbaseStyleBrowseProvider)
    try:
        shaped = _payload(await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"}))
        raw = _payload(await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": shaped["handle"]}))
    finally:
        unregister_browse_provider(FAKE)

    body = raw["raw"][0]
    assert body["sessionId"] == MCP_REDACTED
    assert body["session"] == {"browserSessionId": MCP_REDACTED, "session_ids": [MCP_REDACTED, MCP_REDACTED]}
    # What identifies the run rather than the browser is not a way into anything, and is kept.
    assert (body["runId"], body["status"], body["result"]) == ("run-7", "COMPLETED", "the price is 9.99")
    assert "4 value(s)" in raw["note"]


async def test_the_default_fakes_session_url_never_reaches_the_model(tools: WebRouterTools) -> None:
    shaped = _payload(await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"}))
    raw = _payload(await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": shaped["handle"]}))

    assert raw["raw"] == [{"status": "FINISHED", "sessionUrl": MCP_REDACTED}]


async def test_fetch_raw_is_still_verbatim_for_everything_but_browse(tools: WebRouterTools) -> None:
    # A research payload is data about sources, not a way into a browser, so
    # the redaction browse needs is not applied to it.
    untouched = [{"source": f"https://example.com/?token={_SECRET}"}]

    class RawResearchProvider(FakeResearchProvider):
        def _progress(self, request: Any) -> Any:
            progress = super()._progress(request)
            return progress.model_copy(update={"response": progress.response.model_copy(update={"raw": untouched})})

    register_research_provider(FAKE, RawResearchProvider)
    try:
        researched = _payload(await call_tool(tools, MCP_TOOL_RESEARCH, {"query": "why"}))
        raw = _payload(await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": researched["handle"]}))
    finally:
        unregister_research_provider(FAKE)

    assert raw["raw"] == untouched
    assert "note" not in raw


class FailingBrowseProvider(FakeBrowseProvider):
    """A backend whose task fails with its own message quoting the session it ran in."""

    polls_before_done = 3
    fail_after = 0

    def _progress(self, request: BrowseRequest) -> BrowseProgress:
        progress = super()._progress(request)
        if self.polls < self.fail_after:
            return progress
        return BrowseProgress(job_id="task-1", state="failed", detail=f"lost wss://cdp.example.test/?token={_SECRET}")


@pytest.mark.parametrize("fail_after", [0, 2], ids=["browse", "browse_status"])
async def test_a_browse_error_never_carries_a_session_url(tools: WebRouterTools, fail_after: int) -> None:
    FailingBrowseProvider.fail_after = fail_after
    register_browse_provider(FAKE, FailingBrowseProvider)
    try:
        result = await call_tool(tools, MCP_TOOL_BROWSE, {"task": "find the price"})
        if fail_after:
            assert not result.is_error
            result = await call_tool(tools, MCP_TOOL_BROWSE_STATUS, {"handle": _payload(result)["handle"]})
    finally:
        unregister_browse_provider(FAKE)

    text = result.content[0].text
    if not result.is_error:
        # A failed poll is a state rather than an error; its detail is redacted all the same.
        assert _payload(result)["state"] == "failed"
    assert _SECRET not in text
    assert MCP_REDACTED in text


def test_the_server_steers_an_interactive_page_to_a_browser_the_user_agrees_to(tools: WebRouterTools) -> None:
    """A login-gated answer: suggest a browser, ask first, and keep the user's credentials out of the conversation."""
    instructions = tools.instructions()
    assert "ask before opening one" in instructions
    assert "provider='local' and headless=false" in instructions
    assert "never ask for a password" in instructions
    described = {tool.name: tool.description for tool in tools.list_tools()}
    assert "browser_open" in described["search"] and "browser_open" in described["extract"]
    opener = described.get("browser_open")
    if opener is not None:  # listed only where Playwright imports
        assert "logins, forms, multi-step portals" in opener and "headless=false" in opener
        assert "BILLS FOR EVERY MINUTE" in opener  # the billing warning stays


def test_the_capabilities_say_which_keys_would_unlock_more(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "set")
    unlock = tools.capabilities()["unlock"]

    assert unlock["search"]["perplexity"] == ["PERPLEXITY_API_KEY"]
    assert "tavily" not in unlock["search"], "a backend with its key is not listed"
    assert unlock["extract"]["oxylabs"] == ["OXYLABS_USERNAME", "OXYLABS_PASSWORD"]
    assert "steel" in unlock["browser"] and "local" not in unlock["browser"]
    assert "every capability is available" in tools.instructions()


# -- answer ------------------------------------------------------------------


async def test_answer_round_trip_and_fetch_raw(tools: WebRouterTools, fake_answer_provider: Any) -> None:
    payload = _payload(
        await call_tool(tools, MCP_TOOL_ANSWER, {"query": "who won", "recency": "week", "country": "US"})
    )

    assert payload["provider"] == FAKE
    assert payload["answer"] == "the answer"
    assert payload["outcome"] == "complete"
    assert payload["citation_count"] == 1
    assert payload["citations"][0]["url"] == "https://example.com"
    assert payload["handle"].startswith(MCP_TOOL_ANSWER)
    request = tools.router.get_answer_provider(FAKE).requests[-1]  # type: ignore[attr-defined]
    assert (request.recency, request.country, request.locale) == ("week", "US", None)

    raw = _payload(await call_tool(tools, MCP_TOOL_FETCH_RAW, {"handle": payload["handle"]}))
    assert raw["raw"] == [{"answer": "the answer"}]


async def test_answer_needs_no_provider_argument(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """With one answer backend configured, `provider` is offered but not required, and a bare query goes to it:
    unnamed, the router's `auto` serves the call, and its only member is that backend."""
    monkeypatch.setenv(ENV_LINKUP_API_KEY, "x")
    session = WebRouterTools(AsyncWebRouter())
    schema = _tool(session, MCP_TOOL_ANSWER).input_schema
    assert schema["properties"]["provider"]["enum"] == [PROVIDER_LINKUP]
    assert "provider" not in schema["required"]

    auto = session.router.get_answer_provider()
    assert auto.members == (PROVIDER_LINKUP,)  # type: ignore[attr-defined]
    linkup = auto._member(0)  # type: ignore[attr-defined]
    api = stub_answer_api(linkup, {"answer": "Argentina.[1]", "sources": [{"url": "https://example.com", "name": "x"}]})
    payload = _payload(await call_tool(session, MCP_TOOL_ANSWER, {"query": "who won"}))

    assert payload["provider"] == PROVIDER_LINKUP
    assert len(api.calls) == 1


def test_answer_says_so_when_nothing_can_serve_it(
    tools: WebRouterTools, no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    described = _tool(tools, MCP_TOOL_ANSWER).description
    assert "NOTHING IS CONFIGURED" in described
    assert ENV_LINKUP_API_KEY in described

    monkeypatch.setenv(ENV_LINKUP_API_KEY, "x")
    schema = _tool(tools, MCP_TOOL_ANSWER).input_schema
    assert "NOTHING IS CONFIGURED" not in _tool(tools, MCP_TOOL_ANSWER).description
    assert schema["properties"]["provider"]["enum"] == [PROVIDER_LINKUP]


def test_answer_offers_only_the_options_a_configured_backend_maps(
    no_keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linkup has no country or locale; Perplexity has country. Neither has locale."""
    monkeypatch.setenv(ENV_LINKUP_API_KEY, "x")
    linkup_only = set(_tool(WebRouterTools(AsyncWebRouter()), MCP_TOOL_ANSWER).input_schema["properties"])
    assert {"include_domains", "exclude_domains", "recency"} <= linkup_only
    assert {"country", "locale"}.isdisjoint(linkup_only)

    monkeypatch.setenv(ENV_PERPLEXITY_API_KEY, "x")
    both = set(_tool(WebRouterTools(AsyncWebRouter()), MCP_TOOL_ANSWER).input_schema["properties"])
    assert "country" in both
    assert "locale" not in both
