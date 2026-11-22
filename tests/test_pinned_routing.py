"""A pinned mode is priced as that mode and built with its backend's config; and what a backend offers a router."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
import pytest

from web_router import AsyncWebRouter, Budget, WebRouter, prices_modes
from web_router.exceptions import ConfigurationError
from web_router.exceptions_typed import InvalidParameterError
from web_router.modes import Content, Flag, ModeDeclarations, Tier
from web_router.providers._modes import offers
from web_router.providers.auto import AutoProvider
from web_router.routing.attempts import Attempt
from web_router.routing.pricing import attempt_cost, priced_as
from web_router.types import SearchRequest

INSTANT = prices_modes.mode_price("exa", "search", "exa-instant")


def _mocked(router: WebRouter, provider: str) -> None:
    def answer(method: str, path: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json={"results": []}, request=httpx.Request(method, "https://api.exa.ai" + path))

    router.get_provider(provider)._client.request = answer  # type: ignore[attr-defined]


def test_the_mode_has_its_own_price_to_check_against() -> None:
    assert INSTANT is not None and prices_modes.mode_price("exa", "search", None) != INSTANT


def test_a_pinned_response_carries_the_modes_estimated_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "k")
    with WebRouter() as router:
        _mocked(router, "exa:exa-instant")
        response = router.search("q", provider="exa:exa-instant")
    assert (response.provider, response.cost_usd, response.cost_source) == ("exa", INSTANT, "estimated")


def test_a_pinned_id_is_a_routers_search_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "k")
    with WebRouter(provider="exa:exa-instant") as router:
        _mocked(router, "exa:exa-instant")
        assert router.search("q").cost_usd == INSTANT


@pytest.mark.parametrize(
    ("getter", "default", "backend", "env"),
    [
        ("get_extract_provider", "exa:exa-instant", "exa", "EXA_API_KEY"),
        ("get_research_provider", "parallel:parallel-turbo", "parallel", "PARALLEL_API_KEY"),
        # No browser backend declares a mode: the getter takes any pin's backend, which is what is under test.
        ("get_browser_provider", "browserbase:pinned", "browserbase", "BROWSERBASE_API_KEY"),
        ("get_browse_provider", "browserbase:pinned", "browserbase", "BROWSERBASE_API_KEY"),
    ],
)
def test_a_pinned_default_serves_other_methods_through_its_backend(
    monkeypatch: pytest.MonkeyPatch, getter: str, default: str, backend: str, env: str
) -> None:
    monkeypatch.setenv(env, "k")
    with WebRouter(provider=default) as router:
        assert getattr(router, getter)().name == backend


def test_a_budget_charges_the_pinned_modes_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "k")
    with WebRouter() as router:
        _mocked(router, "exa:exa-instant")
        with router.budget(Budget(max_cost_usd=1.0)) as shared:
            shared.search("q", provider="exa:exa-instant")
        assert shared.status().spent_usd == pytest.approx(INSTANT)


@pytest.mark.parametrize("entry", ["exa:exa-instant", "search:exa:exa-instant"])
def test_a_chain_entry_that_pins_a_mode_is_priced_as_that_mode(entry: str) -> None:
    assert attempt_cost(Attempt(tool="search", provider=entry, request=SearchRequest(query="q"))) == INSTANT


@pytest.mark.parametrize(
    ("entry", "depth", "mode"),
    [
        ("exa:exa-highlights", "lite", "exa-highlights"),
        ("you:you-full-page-cache", "deep", "you-full-page-cache"),
        ("exa:exa-instant", "lite", "exa-instant+lite"),
        ("brave:brave-extra-snippets", "lite", "lite+brave-extra-snippets"),
    ],
)
def test_a_chain_entry_with_a_depth_is_priced_as_pinning_would_set_its_mode(entry: str, depth: str, mode: str) -> None:
    attempt = Attempt(tool="search", provider=entry, request=SearchRequest(query="q", search_depth=depth))
    backend = entry.split(":")[0]
    assert priced_as(attempt) == (backend, mode)
    assert attempt_cost(attempt) == prices_modes.mode_price(backend, "search", mode)  # as the same pin, called direct


def test_a_chain_entry_whose_request_is_already_pinned_keeps_that_mode() -> None:
    request = SearchRequest(query="q", mode="exa-instant+lite")
    assert priced_as(Attempt(tool="search", provider="exa:exa-instant", request=request)) == ("exa", "exa-instant+lite")


def test_a_pin_the_price_list_does_not_name_stays_unpriced() -> None:
    attempt = Attempt(tool="search", provider="exa:exa-unlisted", request=SearchRequest(query="q"))
    assert (priced_as(attempt), attempt_cost(attempt)) == (("exa", "exa-unlisted"), None)


CHAIN = {"auto": {"chain": {"search": ["exa:exa-instant", "exa"]}}}


@pytest.fixture
def exa_sent(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every body sent to Exa, sync or async, each answered with no results."""
    monkeypatch.setenv("EXA_API_KEY", "k")
    sent: list[Any] = []

    def answer(method: str, path: str, **kwargs: Any) -> httpx.Response:
        sent.append(kwargs.get("json"))
        return httpx.Response(200, json={"results": []}, request=httpx.Request(method, "https://api.exa.ai" + path))

    async def aanswer(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return answer(method, path, **kwargs)

    monkeypatch.setattr(httpx.Client, "request", lambda client, method, path, **kwargs: answer(method, path, **kwargs))
    monkeypatch.setattr(httpx.AsyncClient, "request", aanswer)
    return sent


def test_a_chain_passes_over_a_pinned_member_the_calls_effort_refuses(exa_sent: list[Any]) -> None:
    with WebRouter(provider="auto", provider_config=CHAIN) as router:
        response = router.search("q", effort="high")
    assert len(exa_sent) == 1 and exa_sent[0]["type"] != "instant" and response.cost_usd != INSTANT  # plain exa


async def test_an_async_chain_passes_over_a_pinned_member_the_calls_effort_refuses(exa_sent: list[Any]) -> None:
    async with AsyncWebRouter(provider="auto", provider_config=CHAIN) as router:
        response = await router.search("q", effort="high")
    assert len(exa_sent) == 1 and exa_sent[0]["type"] != "instant" and response.cost_usd != INSTANT


def test_a_chain_whose_every_member_refuses_raises_the_refusal_and_sends_nothing(exa_sent: list[Any]) -> None:
    chain = {"auto": {"chain": {"search": ["exa:exa-instant", "exa:exa-instant"]}}}
    with (
        WebRouter(provider="auto", provider_config=chain) as router,
        pytest.raises(InvalidParameterError, match="effort"),
    ):
        router.search("q", effort="high")
    assert exa_sent == []


def test_the_same_pin_called_directly_still_refuses_the_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "k")
    with WebRouter() as router, pytest.raises(InvalidParameterError, match="effort"):
        router.search("q", provider="exa:exa-instant", effort="high")


def test_a_pinned_backend_is_built_with_its_backends_config() -> None:
    with WebRouter(provider_config={"exa": {"api_key": "from-config"}}) as router:
        assert router.get_provider("exa:exa-instant").api_key == "from-config"  # type: ignore[attr-defined]


class Acme(ModeDeclarations):
    name = "acme"
    supported_parameters: ClassVar[frozenset[str]] = frozenset({"search_depth"})
    depth_levels: ClassVar[frozenset[str]] = frozenset({"lite", "deep"})
    tiers: ClassVar[Mapping[str, Tier]] = {
        "acme-pro": Tier(wire={"tier": "pro"}),
        "acme-turbo": Tier(wire={"tier": "turbo"}),
    }
    contents: ClassVar[Mapping[str, Content]] = {"acme-snips": Content(as_depth="lite", wire={"body": "snips"})}
    flags: ClassVar[Mapping[str, Flag]] = {"acme-extra": Flag(wire={"extra": True})}


def test_offers_are_each_tier_at_the_default_depth_then_each_depth_and_content_then_each_flag() -> None:
    assert offers(Acme) == ("acme-pro", "acme-turbo", "lite", "deep", "acme-snips", "acme-extra")


def test_a_tier_that_is_the_default_is_not_offered_twice_and_a_class_may_set_its_own() -> None:
    assert "acme-pro" not in offers(type("Pro", (Acme,), {"default_tier": "acme-pro"}))
    assert offers(type("Own", (Acme,), {"offers": ("acme-turbo+deep",)})) == ("acme-turbo+deep",)


def test_a_pinned_chain_entry_is_held_to_its_backends_client_rule_at_construction() -> None:
    with pytest.raises(ConfigurationError, match="not both"):
        AutoProvider(chain=["nimble:lite"], members_config={"nimble": {"async_client": object(), "api_key": "k"}})
