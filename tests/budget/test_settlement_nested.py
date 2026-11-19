"""Settling a nested call's attempt settles it in the enclosing call's cap too, and never touches the enclosing
call's own attempt.

``wrapper`` ($0.02) makes the inner calls a test lists, one after another, on the
same router; ``a`` and ``b`` cost $0.01 each. The enclosing call's cap is $0.035:
room for one inner call at its estimate, not two.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import suppress
from typing import Any

import httpx
import pytest

from tests.conftest import FakeProvider
from tests.routing_helpers import Table
from web_router import Budget, WebRouter
from web_router.constants_cost import COST_REPORTED
from web_router.exceptions import ProviderError, WebRouterError
from web_router.providers.auto import AutoProvider
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse

OUTER_CAP = Budget(max_cost_usd=0.035)
_REQUEST = httpx.Request("GET", "https://api.test")
_REFUSED = httpx.HTTPStatusError("refused", request=_REQUEST, response=httpx.Response(401, request=_REQUEST))

Behaviour = Callable[[Any, SearchRequest], SearchResponse]


def _refuses(self: Any, search: SearchRequest) -> SearchResponse:
    raise ProviderError(self.name, "refused", original=_REFUSED)


def _reports_less(self: Any, search: SearchRequest) -> SearchResponse:
    answered = FakeProvider._respond(self, search)
    return answered.model_copy(update={"cost_usd": 0.001, "cost_source": COST_REPORTED})


@pytest.fixture
def nested(priced: Table) -> Iterator[tuple[WebRouter, dict[str, Behaviour], list[str]]]:
    """The router, how ``a`` answers (set by the test), and the backends that ran, in order."""
    router, behaviour, ran = WebRouter(), {}, []

    def wrapper(self: Any, search: SearchRequest) -> SearchResponse:
        for provider in ("a", "b"):
            with suppress(WebRouterError):
                router.search("inner", provider=provider)
        return FakeProvider._respond(self, search)

    def logged(self: Any, search: SearchRequest) -> SearchResponse:
        ran.append(self.name)
        return behaviour.get(self.name, FakeProvider._respond)(self, search)

    names = {"wrapper": wrapper, "a": logged, "b": logged}
    for name, search in names.items():
        register_provider(name, type(name.title(), (FakeProvider,), {"name": name, "_search": search}))
    priced.update(wrapper=(0.02, None), a=(0.01, None), b=(0.01, None))
    yield router, behaviour, ran
    for name in names:
        unregister_provider(name)


@pytest.mark.parametrize("answers", [_refuses, _reports_less], ids=["refused", "reported-less"])
def test_a_nested_attempt_settled_below_its_estimate_leaves_room_in_the_enclosing_cap(
    nested: Any, answers: Behaviour
) -> None:
    router, behaviour, ran = nested
    behaviour["a"] = answers
    response = router.search("q", provider="wrapper", budget=OUTER_CAP)
    assert ran == ["a", "b"], "a's settled charge, not its $0.01 estimate, counts toward the outer cap"
    # A refused a never ran, so the outer record lists it at $0; an answered one keeps its estimate there.
    assert response.decision.estimated_cost_usd == pytest.approx(0.03 if answers is _refuses else 0.04)


def test_a_nested_refusal_never_frees_the_enclosing_calls_own_attempt_of_the_same_name(priced: Table) -> None:
    router, depth = WebRouter(), []

    def wrapper(self: Any, search: SearchRequest) -> SearchResponse:
        if depth:
            return _refuses(self, search)
        depth.append(1)
        with suppress(WebRouterError):
            # Through `auto`, whose failure hook runs the enclosing call's too; the same id as its open attempt.
            router.search("inner", provider=AutoProvider(chain=[("wrapper", {})]))
        return FakeProvider._respond(self, search)

    register_provider("wrapper", type("Wrapper", (FakeProvider,), {"name": "wrapper", "_search": wrapper}))
    priced.update(wrapper=(0.02, None))
    try:
        with router.budget(Budget(max_cost_usd=1.0)) as shared:
            router.search("q", provider="wrapper")
    finally:
        unregister_provider("wrapper")
    assert (shared.status().spent_usd, shared.status().reserved_usd) == (0.02, 0.0), "only the inner one refunded"
