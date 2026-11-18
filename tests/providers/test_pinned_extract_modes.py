"""Pinning an extract mode: each declared part sends its wire, a caller's options survive or are refused, and a
pinned chain entry is priced, retried and offered as that mode.

Every vendor is mocked at its HTTP transport, so these read the body each backend would send.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import httpx
import pytest
from nimble_python import AsyncNimble

from scripts.contracts_modes import parts
from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.exceptions import WebRouterError
from web_router.exceptions_budget import BudgetExceededError
from web_router.exceptions_typed import InvalidParameterError
from web_router.providers.auto import AutoExtractProvider
from web_router.registry import create_extract_provider, get_extract_provider_class
from web_router.routing.candidates import build_candidates
from web_router.routing.metered import combined_cost, metered
from web_router.types import ExtractRequest

DECLARING = ("tavily", "linkup", "oxylabs", "nimble", "exa", "browserbase")
PINS = [(name, part) for name in DECLARING for part in parts(get_extract_provider_class(name))]
URL = "https://a.example/page"
CALLER = {"caller_key": 1}
"""A ``provider_options`` key no mode writes; Nimble's SDK takes only its own fields, so it gets ``country``."""
NIMBLE_CALLER = {"country": "US"}


NIMBLE_REPLY = {"task_id": "t", "url": URL, "status": "success", "status_code": 200, "data": {"markdown": "# Page"}}
"""One page Nimble's realtime extract read."""


def recording(bodies: list[dict[str, Any]], answer: dict[str, Any] | None = None) -> httpx.MockTransport:
    """A transport that records each JSON body sent and answers ``answer``."""

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=answer or {})

    return httpx.MockTransport(handle)


def built(pin: str, bodies: list[dict[str, Any]], answer: dict[str, Any] | None = None) -> Any:
    """``pin`` built against a mock transport that records each JSON body and answers ``answer``."""

    transport = recording(bodies, answer)
    name = pin.split(":")[0]
    if name == "tavily":
        return create_extract_provider(pin, api_key="k", client=httpx.AsyncClient(transport=transport))
    if name == "nimble":
        client = AsyncNimble(api_key="k", http_client=httpx.AsyncClient(transport=transport))
        return create_extract_provider(pin, async_client=client)
    provider = create_extract_provider(pin, api_key="k", **({"username": "u"} if name == "oxylabs" else {}))
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return provider


async def sent(pin: str, request: ExtractRequest) -> dict[str, Any]:
    """The one body ``pin`` sends for ``request``; the empty answer it gets back may fail the call."""
    bodies: list[dict[str, Any]] = []
    with contextlib.suppress(WebRouterError):
        await built(pin, bodies).aextract(request)
    assert len(bodies) == 1, "one call to the vendor"
    return bodies[0]


def _wire(name: str, part: str) -> dict[str, Any]:
    cls = get_extract_provider_class(name)
    return dict((cls.tiers.get(part) or cls.flags[part]).wire)


@pytest.mark.parametrize(("name", "part"), PINS, ids=[f"{n}:{p}" for n, p in PINS])
async def test_pinning_a_part_sends_exactly_its_wire_beside_the_callers_other_options(name: str, part: str) -> None:
    caller = NIMBLE_CALLER if name == "nimble" else CALLER
    request = ExtractRequest(urls=[URL], provider_options=caller)
    plain = await sent(name, request)
    pinned = await sent(f"{name}:{part}", request)

    assert {field: value for field, value in pinned.items() if plain.get(field) != value} == _wire(name, part)
    assert pinned.items() >= caller.items()


@pytest.mark.parametrize(
    ("pin", "options", "named"),
    [
        ("tavily:tavily-advanced", {"provider_options": {"extract_depth": "basic"}}, "provider_options"),
        ("linkup:linkup-render-js", {"provider_options": {"renderJs": False}}, "provider_options"),
        ("tavily:tavily-advanced+lite", {}, "no option for"),
    ],
)
async def test_a_mode_the_caller_overrides_or_cannot_take_is_refused_before_anything_is_sent(
    pin: str, options: dict[str, Any], named: str
) -> None:
    bodies: list[dict[str, Any]] = []
    with pytest.raises(InvalidParameterError, match=named):
        await built(pin, bodies).aextract(ExtractRequest(urls=[URL], **options))
    assert bodies == []


def test_a_pin_that_names_another_method_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="pinned extract backend"):
        get_extract_provider_class("search:tavily:tavily-advanced")


async def test_the_urls_one_member_failed_are_retried_in_the_next_members_mode() -> None:
    other = "https://b.example/page"
    answered = {
        "results": [{"url": URL, "text": "body"}],
        "statuses": [{"id": URL, "status": "success"}, {"id": other, "status": "error", "error": "blocked"}],
    }
    tavily: list[dict[str, Any]] = []
    members = [built("exa", [], answered), built("tavily:tavily-advanced", tavily)]
    auto = AutoExtractProvider(chain=["exa", "tavily:tavily-advanced"])
    auto._member = members.__getitem__  # type: ignore[method-assign]

    await auto.aextract(ExtractRequest(urls=[URL, other], format="text"))  # Exa returns text only
    assert [(body["urls"], body["extract_depth"]) for body in tavily] == [([other], "advanced")]


@pytest.mark.parametrize(
    ("cap", "offered"), [(1.0, ["nimble:nimble-max"]), (None, ["nimble:nimble-max", "nimble:nimble-vx8"])]
)
def test_an_unpriced_tier_is_offered_only_without_a_cost_cap(
    monkeypatch: pytest.MonkeyPatch, cap: float | None, offered: list[str]
) -> None:
    monkeypatch.setenv("NIMBLE_API_KEY", "k")
    chain = {"extract": ["nimble:nimble-max", "nimble:nimble-vx8"]}
    router = WebRouter(provider_config={"auto": {"chain": chain}})
    budget = None if cap is None else Budget(max_cost_usd=cap)
    found = build_candidates(router, {"extract": ExtractRequest(urls=[URL])}, budget=budget)
    assert [candidate.provider for candidate in found.candidates] == offered


@pytest.mark.parametrize(
    ("pin", "cost", "source"), [("nimble:nimble-vx8", None, None), ("nimble:nimble-max", 0.0025, "estimated")]
)
async def test_a_chain_prices_a_pinned_answer_at_its_mode_and_leaves_an_unpriced_one_unknown(
    pin: str, cost: float | None, source: str | None
) -> None:
    response = await built(pin, [], NIMBLE_REPLY).aextract(ExtractRequest(urls=[URL]))
    assert (response.cost_usd, response.cost_source) == (cost, source)
    chain_request = ExtractRequest(urls=[URL])  # a chain meters with its own request, which names no mode
    assert metered(response, "extract", chain_request, 0.0).cost_usd == cost
    assert combined_cost("extract", [(response, chain_request)])["cost_usd"] == cost


@pytest.mark.parametrize(("cap", "sends"), [(0.001, 0), (0.01, 1)])
async def test_a_named_pin_is_priced_at_its_mode_before_the_budget_lets_it_send(cap: float, sends: int) -> None:
    bodies: list[dict[str, Any]] = []
    client = AsyncNimble(api_key="k", http_client=httpx.AsyncClient(transport=recording(bodies, NIMBLE_REPLY)))
    router = AsyncWebRouter(provider_config={"nimble": {"async_client": client}})
    refused = pytest.raises(BudgetExceededError) if sends == 0 else contextlib.nullcontext()
    with refused:  # nimble-max is $0.0025 a page; Nimble's default row, $0.001, would slip under the lower cap
        await router.extract(URL, provider="nimble:nimble-max", budget=Budget(max_cost_usd=cap))
    assert len(bodies) == sends
