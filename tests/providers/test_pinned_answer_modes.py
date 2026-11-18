"""Pinning an answer mode: each declared part sends its wire, a caller's options survive or are refused, and a
pinned answer is priced and offered as that mode.

Every vendor is mocked at its HTTP transport, so these read the body each backend would send.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import httpx
import pytest

from scripts.contracts_modes import parts
from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.exceptions import WebRouterError
from web_router.exceptions_budget import BudgetExceededError
from web_router.exceptions_typed import InvalidParameterError
from web_router.registry_answer import create_answer_provider, get_answer_provider_class
from web_router.routing.candidates import build_candidates
from web_router.routing.metered import metered
from web_router.types_answer import AnswerRequest

DECLARING = ("exa", "linkup", "perplexity")
PINS = [(name, part) for name in DECLARING for part in parts(get_answer_provider_class(name))]
QUESTION = "Who won the 2022 World Cup final?"
CALLER = {"caller_key": 1}
"""A ``provider_options`` key no mode writes."""

LINKUP_REPLY = {"answer": "Argentina.", "sources": []}
"""Linkup's sourced answer, trimmed: it states no cost."""


def recording(bodies: list[dict[str, Any]], answer: dict[str, Any] | None = None) -> httpx.MockTransport:
    """A transport that records each JSON body sent and answers ``answer``."""

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content or b"{}"))
        return httpx.Response(200, json=answer or {})

    return httpx.MockTransport(handle)


async def sent(pin: str, request: AnswerRequest) -> dict[str, Any]:
    """The one body ``pin`` sends for ``request``; the empty answer it gets back may fail the call."""
    bodies: list[dict[str, Any]] = []
    with contextlib.suppress(WebRouterError):
        await create_answer_provider(pin, api_key="k", transport=recording(bodies)).aanswer(request)
    assert len(bodies) == 1, "one call to the vendor"
    return bodies[0]


@pytest.mark.parametrize(("name", "part"), PINS, ids=[f"{n}:{p}" for n, p in PINS])
async def test_pinning_a_part_sends_exactly_its_wire_beside_the_question_and_the_callers_options(
    name: str, part: str
) -> None:
    request = AnswerRequest(query=QUESTION, provider_options=CALLER)
    plain = await sent(name, request)
    pinned = await sent(f"{name}:{part}", request)

    wire = dict(get_answer_provider_class(name).tiers[part].wire)
    assert {field: value for field, value in pinned.items() if plain.get(field) != value} == wire
    assert pinned.items() >= CALLER.items()
    assert QUESTION in pinned.values(), "the question stays the router's"


@pytest.mark.parametrize(
    ("pin", "options", "named"),
    [
        ("exa:exa-pro", {"provider_options": {"model": "exa"}}, "provider_options"),
        ("linkup:linkup-deep", {"provider_options": {"depth": "fast"}}, "provider_options"),
        ("perplexity:perplexity-high", {"provider_options": {"effort": "fast"}}, "provider_options"),
        ("linkup:linkup-deep+lite", {}, "no option for"),
    ],
)
async def test_a_mode_the_caller_overrides_or_cannot_take_is_refused_before_anything_is_sent(
    pin: str, options: dict[str, Any], named: str
) -> None:
    bodies: list[dict[str, Any]] = []
    with pytest.raises(InvalidParameterError, match=named):
        provider = create_answer_provider(pin, api_key="k", transport=recording(bodies))
        await provider.aanswer(AnswerRequest(query=QUESTION, **options))
    assert bodies == []


def test_a_pin_that_names_another_method_is_refused() -> None:
    with pytest.raises(InvalidParameterError, match="pinned answer backend"):
        get_answer_provider_class("search:linkup:linkup-deep")


@pytest.mark.parametrize(
    ("cap", "offered"), [(1.0, ["linkup:linkup-deep"]), (None, ["linkup:linkup-deep", "exa:exa-pro"])]
)
def test_an_unpriced_tier_is_offered_only_without_a_cost_cap(
    monkeypatch: pytest.MonkeyPatch, cap: float | None, offered: list[str]
) -> None:
    monkeypatch.setenv("LINKUP_API_KEY", "k")
    monkeypatch.setenv("EXA_API_KEY", "k")
    chain = {"answer": ["linkup:linkup-deep", "exa:exa-pro"]}
    router = WebRouter(provider_config={"auto": {"chain": chain}})
    budget = None if cap is None else Budget(max_cost_usd=cap)
    found = build_candidates(router, {"answer": AnswerRequest(query=QUESTION)}, budget=budget)
    assert [candidate.provider for candidate in found.candidates] == offered


@pytest.mark.parametrize(
    ("pin", "reply", "cost", "source"),
    [
        ("linkup:linkup-deep", LINKUP_REPLY, 0.055, "estimated"),
        ("perplexity:perplexity-high", {"status": "completed", "output": []}, None, None),
    ],
)
async def test_a_pinned_answer_is_priced_at_its_mode_and_a_token_billed_one_left_unknown(
    pin: str, reply: dict[str, Any], cost: float | None, source: str | None
) -> None:
    provider = create_answer_provider(pin, api_key="k", transport=recording([], reply))
    response = await provider.aanswer(AnswerRequest(query=QUESTION))
    assert (response.cost_usd, response.cost_source) == (cost, source)
    chain_request = AnswerRequest(query=QUESTION)  # a chain meters with its own request, which names no mode
    assert metered(response, "answer", chain_request, 0.0).cost_usd == cost


@pytest.mark.parametrize(("cap", "sends"), [(0.01, 0), (0.1, 1)])
async def test_a_named_pin_is_priced_at_its_mode_before_the_budget_lets_it_send(cap: float, sends: int) -> None:
    bodies: list[dict[str, Any]] = []
    config = {"linkup": {"api_key": "k", "transport": recording(bodies, LINKUP_REPLY)}}
    router = AsyncWebRouter(provider_config=config)
    refused = pytest.raises(BudgetExceededError) if sends == 0 else contextlib.nullcontext()
    with refused:  # linkup-deep is $0.055 an answer; Linkup's default row, $0.006, would slip under the lower cap
        await router.answer(QUESTION, provider="linkup:linkup-deep", budget=Budget(max_cost_usd=cap))
    assert [body["depth"] for body in bodies] == ["deep"] * sends
