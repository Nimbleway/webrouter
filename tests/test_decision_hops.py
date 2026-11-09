"""The decision record's hops: every member ``auto`` moved past, in the order it did, and why.

A member that answers empty, one that fails, one that runs into the deadline
and one that refuses a parameter each move a routed call on; the record names
each, in order, by its error's class or status.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.conftest import FakeAnswerProvider, FakeProvider
from web_router import (
    AsyncWebRouter,
    Budget,
    ConfigurationError,
    DeadlineExceededError,
    DecisionHop,
    Plan,
    RouteDecision,
    RouteRequest,
    UnsupportedParameterError,
    WebRouter,
    registry,
    registry_answer,
)
from web_router.providers._chain import _empty_response
from web_router.types import ExtractedDocument, ExtractResponse, SearchRequest, SearchResponse
from web_router.types_answer import AnswerResponse

NAMES = ("first", "second", "third")


def _empty(self: Any, request: SearchRequest) -> SearchResponse:
    return SearchResponse(provider=self.name, query=request.query, results=[])


def _raising(error: Exception) -> Callable[..., Any]:
    def entry(self: Any, *_: Any) -> Any:
        raise error

    return entry


@pytest.fixture
def search_members() -> Iterator[Callable[..., None]]:
    """Register ``first``, ``second`` and ``third`` as search members, each answering by the entry given."""

    def register(**entries: Callable[..., Any]) -> None:
        for name in NAMES:
            body = {"name": name} | ({"_respond": entries[name]} if name in entries else {})
            registry.register_provider(name, type(f"Search_{name}", (FakeProvider,), body))

    yield register
    for name in NAMES:
        registry.unregister_provider(name)


def _wr(cls: type = WebRouter) -> Any:
    def route(req: RouteRequest) -> RouteDecision:
        return RouteDecision(providers={"search": list(NAMES), "answer": list(NAMES)})

    return cls(provider_config={"auto": {"chain": list(NAMES)}}, router=route)


def test_hops_keep_the_order_the_call_moved_past_them(search_members: Callable[..., None]) -> None:
    search_members(first=_empty, second=_raising(ConfigurationError("second holds no key")))
    record = _wr().search("q").decision
    assert record.hops == [DecisionHop(id="search:first"), DecisionHop(id="search:second", error="ConfigurationError")]
    assert record.ran == 2


async def test_a_member_that_runs_into_the_deadline_is_a_hop_with_its_error(
    search_members: Callable[..., None],
) -> None:
    search_members(first=_empty, second=_raising(DeadlineExceededError("second", 1.0)))
    response = await _wr(AsyncWebRouter).search("q")
    assert response.provider == "first", "the empty answer held before the deadline"
    assert response.decision.ran == 0
    assert response.decision.hops == [DecisionHop(id="search:second", error="DeadlineExceededError")]


@pytest.fixture
def answer_members() -> Iterator[None]:
    refusing = type("Refusing", (FakeAnswerProvider,), {"name": "first"})
    refusing.raises = UnsupportedParameterError("first", ["recency"])
    registry_answer.register_answer_provider("first", refusing)
    for name in NAMES[1:]:
        registry_answer.register_answer_provider(name, type(f"Answer_{name}", (FakeAnswerProvider,), {"name": name}))
    yield
    for name in NAMES:
        registry_answer.unregister_answer_provider(name)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_a_member_that_refuses_a_parameter_is_a_hop_with_its_error(
    answer_members: None, asynchronous: bool
) -> None:
    if asynchronous:
        response = await _wr(AsyncWebRouter).answer("q")
    else:
        response = _wr().answer("q")
    assert response.provider == "second"
    assert response.decision.hops == [DecisionHop(id="answer:first", error="UnsupportedParameterError")]


def test_a_backend_a_chain_names_twice_keeps_both_hops(search_members: Callable[..., None]) -> None:
    search_members(first=_raising(ConfigurationError("first holds no key")), second=_empty)
    wr = WebRouter(provider_config={"auto": {"chain": ["first", "second", "first", "third"]}})
    record = wr.search("q", budget=Budget()).decision
    assert record.hops == [
        DecisionHop(id="search:first", error="ConfigurationError"),
        DecisionHop(id="search:second"),
        DecisionHop(id="search:first", error="ConfigurationError"),
    ]
    assert [attempt.id for attempt in record.attempts] == [
        "search:first",
        "search:second",
        "search:first",
        "search:third",
    ]


_NO_KEY = ConfigurationError("no key")
_FIRST, _FAILED = DecisionHop(id="search:first"), DecisionHop(id="search:first", error="ConfigurationError")
_SECOND = DecisionHop(id="search:second", error="ConfigurationError")


@pytest.mark.parametrize(
    ("chain", "outcomes", "hops"),
    [
        (["first", "first"], [None, _NO_KEY], [_FAILED]),
        (["first", "first"], [_NO_KEY, None], [_FAILED]),
        (["first", "second", "first"], [None, None], [_SECOND, _FIRST]),
        (["first", "second", "first"], [None, "full"], [_FIRST, _SECOND]),
        (["first", "second", "first", "first"], [None, None, _NO_KEY], [_SECOND, _FIRST, _FAILED]),
    ],
    ids=["empty then failed", "failed then empty", "empty twice", "empty then full", "the first empty is held"],
)
def test_a_backend_named_twice_is_recorded_by_the_attempt_whose_answer_was_returned(
    search_members: Callable[..., None], chain: list[str], outcomes: list[Any], hops: list[DecisionHop]
) -> None:
    """``auto`` returns a full answer at once and holds the first empty one; the other attempt is the hop."""
    script = iter(outcomes)

    def scripted(self: Any, request: SearchRequest) -> SearchResponse:
        if isinstance(outcome := next(script), Exception):
            raise outcome
        return FakeProvider._respond(self, request) if outcome else _empty(self, request)

    search_members(first=scripted, second=_raising(_NO_KEY))
    record = WebRouter(provider_config={"auto": {"chain": chain}}).search("q", budget=Budget()).decision
    assert record.hops == hops


@pytest.mark.parametrize(
    ("response", "empty"),
    [
        (ExtractResponse(provider="p", documents=[ExtractedDocument(url="https://e.com", content=" ")]), True),
        (ExtractResponse(provider="p", documents=[ExtractedDocument(url="https://e.com", content="text")]), False),
        (AnswerResponse(provider="p", query="q", answer=" "), True),
        (AnswerResponse(provider="p", query="q", answer="42"), False),
    ],
)
def test_an_extract_or_answer_is_held_as_auto_holds_it(response: Any, empty: bool) -> None:
    assert _empty_response(response) is empty


def test_a_last_empty_answer_is_a_hop_when_an_earlier_one_is_returned(search_members: Callable[..., None]) -> None:
    search_members(first=_empty, second=_empty, third=_empty)
    response = _wr().search("q")
    assert response.provider == "first", "auto returns the first empty answer it held"
    assert response.decision.ran == 0
    assert response.decision.hops == [DecisionHop(id="search:second"), DecisionHop(id="search:third")]


def test_a_last_empty_answer_that_is_returned_is_the_answer_not_a_hop(search_members: Callable[..., None]) -> None:
    failing = _raising(ConfigurationError("no key"))
    search_members(first=failing, second=failing, third=_empty)
    response = _wr().search("q")
    assert response.provider == "third"
    assert response.decision.ran == 2
    assert [hop.id for hop in response.decision.hops] == ["search:first", "search:second"]


@pytest.mark.parametrize("budget", [None, Budget()], ids=["no budget", "budget"])
def test_a_plan_made_by_hand_runs_with_a_decision_id(
    search_members: Callable[..., None], budget: Budget | None
) -> None:
    search_members()
    plan = Plan(prompt="q", tool="search", params={"query": "q"}, providers={"search": ["third"]})
    record = WebRouter(provider_config={"auto": {"chain": list(NAMES)}}).run(plan, budget=budget).decision
    assert re.fullmatch(r"[0-9a-f]{32}", record.decision_id or "")
    assert record.prompt_sha256 == hashlib.sha256(b"q").hexdigest()
