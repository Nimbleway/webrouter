"""A per-call budget's cost cap, on every routable tool: what is skipped, what is charged, and what is raised.

Every rule runs on the static order -- no router attached -- through the
public routers, on an ``auto`` chain of three fake members whose prices the
test sets (see :mod:`tests.budget.conftest`).
"""

from __future__ import annotations

import re

import pytest

from tests.budget.conftest import Chain, Table, acall, call
from web_router import Budget, BudgetExceededError, DecisionHop, WebRouter, prices
from web_router.constants_budget import SKIP_OVER_COST, SKIP_PRICE_UNKNOWN, SKIP_ROUTE_REFUSED, SKIPPED
from web_router.exceptions import ConfigurationError, ProviderError, WebRouterError
from web_router.routing.attempts import Attempt, CallRoute, call_route
from web_router.routing.ledger import attempt_cost, attempt_p90
from web_router.routing.params import measured_efforts
from web_router.types import BrowseRequest, ExtractRequest, ResearchRequest, SearchRequest
from web_router.types_answer import AnswerRequest

CAP = Budget(max_cost_usd=0.01)


def _over(cost: str, left: str) -> str:
    return SKIP_OVER_COST.format(cost=cost, left=left)


def _ids(chain: Chain, *names: str) -> list[str]:
    return [f"{chain.tool.name}:{name}" for name in names]


def test_a_candidate_over_what_is_left_is_skipped_and_the_next_runs(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.05, None), second=(0.005, None), third=(0.0, None))
    response = call(chain, CAP)
    assert (response.provider, chain.called) == ("second", ["second"])
    decision = response.decision
    assert decision.hops == [DecisionHop(id=_ids(chain, "first")[0], error=_over("$0.05", "$0.01"))]
    assert [attempt.id for attempt in decision.attempts] == _ids(chain, "second")
    assert (decision.estimated_cost_usd, decision.cost_is_estimate) == (0.005, True)


async def test_an_async_call_skips_what_does_not_fit_as_a_sync_one_does(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.05, None), second=(0.005, None))
    response = await acall(chain, CAP)
    assert (response.provider, chain.called) == ("second", ["second"])
    assert response.decision.estimated_cost_usd == 0.005


def test_a_failed_attempt_is_charged_its_estimate(chain: Chain, priced: Table) -> None:
    chain.raises["first"] = ConfigurationError("first holds no key")  # fails before anything starts
    priced.update(first=(0.006, None), second=(0.006, None), third=(0.0, None))
    response = call(chain, CAP)
    assert chain.called == ["first", "third"], "second no longer fits once first's estimate is spent"
    assert response.decision.hops == [
        DecisionHop(id=_ids(chain, "first")[0], error="ConfigurationError"),  # its class, never its message
        DecisionHop(id=_ids(chain, "second")[0], error=_over("$0.006", "$0.004")),
    ]
    assert [attempt.id for attempt in response.decision.attempts] == _ids(chain, "first", "third")
    assert response.decision.estimated_cost_usd == 0.006


def test_under_a_cost_cap_an_unknown_price_is_skipped_and_the_order_kept(chain: Chain, priced: Table) -> None:
    priced.update(second=(0.001, None), third=(0.001, None))  # first's price is unknown
    response = call(chain, CAP)
    assert chain.called == ["second"]
    assert response.decision.hops == [DecisionHop(id=_ids(chain, "first")[0], error=SKIP_PRICE_UNKNOWN)]
    assert response.decision.unmeasured == _ids(chain, "first", "second")  # second has no p90


@pytest.mark.parametrize("tool", ["research"], indirect=True)
def test_an_unknown_price_research_call_under_a_ten_cent_cap_is_skipped_and_the_next_runs(
    chain: Chain, priced: Table
) -> None:
    priced.update(second=(0.05, None))
    response = call(chain, Budget(max_cost_usd=0.10))
    assert chain.called == ["second"]
    assert response.decision.hops == [DecisionHop(id="research:first", error=SKIP_PRICE_UNKNOWN)]


def test_with_no_cost_cap_an_unknown_price_runs_and_its_cost_stays_unknown(chain: Chain, priced: Table) -> None:
    response = call(chain, Budget(time_limit_s=60))
    assert chain.called == ["first"]
    decision = response.decision
    assert (decision.estimated_cost_usd, decision.attempts[0].est_cost_usd) == (None, None)
    assert decision.unmeasured == _ids(chain, "first")
    assert decision.cost_is_estimate is True


def test_when_the_budget_skips_every_member_the_call_raises_budget_exceeded(chain: Chain, priced: Table) -> None:
    priced.update(first=(1.0, None), second=(0.02, None))  # third's price is unknown
    with pytest.raises(BudgetExceededError) as raised:
        call(chain, CAP)
    assert chain.called == []
    assert raised.value.skipped == dict(
        zip(
            _ids(chain, "first", "second", "third"),
            [_over("$1.00", "$0.01"), _over("$0.02", "$0.01"), SKIP_PRICE_UNKNOWN],
            strict=True,
        )
    )
    assert SKIPPED.format(id=f"{chain.tool.name}:first", reason=_over("$1.00", "$0.01")) in str(raised.value)


async def test_when_the_budget_skips_every_member_an_async_call_raises_budget_exceeded(
    chain: Chain, priced: Table
) -> None:
    with pytest.raises(BudgetExceededError) as raised:
        await acall(chain, CAP)
    assert chain.called == []
    assert list(raised.value.skipped) == _ids(chain, "first", "second", "third")


def test_a_named_backend_that_does_not_fit_is_never_called(chain: Chain, priced: Table) -> None:
    priced.update(second=(0.05, None))
    with pytest.raises(BudgetExceededError, match=re.escape(_over("$0.05", "$0.01"))):
        call(chain, CAP, provider="second")
    assert chain.called == []


def test_a_named_backend_that_fits_runs_and_its_spend_is_recorded(chain: Chain, priced: Table) -> None:
    priced.update(second=(0.004, 2.0))
    response = call(chain, CAP, provider="second")
    assert chain.called == ["second"]
    assert [attempt.id for attempt in response.decision.attempts] == _ids(chain, "second")
    assert response.decision.estimated_cost_usd == 0.004


def test_budget_exceeded_from_a_member_does_not_move_auto_on(chain: Chain, priced: Table) -> None:
    chain.raises["first"] = BudgetExceededError({})
    priced.update(first=(0.0, None), second=(0.0, None))
    assert not issubclass(BudgetExceededError, ProviderError)
    with pytest.raises(BudgetExceededError):
        call(chain, CAP)
    assert chain.called == ["first"]


def test_a_member_another_hook_refuses_is_neither_charged_nor_recorded(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.001, None), second=(0.001, None), third=(0.001, None))
    order = {chain.tool.name: ["third", "second", "first"]}
    with call_route(CallRoute(order=order, before_attempt=lambda attempt: attempt.provider != "third")):
        response = call(chain, CAP)
    assert chain.called == ["second"]
    assert response.decision.hops is None
    assert response.decision.estimated_cost_usd == 0.001


def test_an_answer_call_with_a_budget_carries_its_estimated_spend(fake_answer_provider: object, priced: Table) -> None:
    priced.update(fake=(0.006, 1.5))
    response = WebRouter().answer("q", provider="fake", budget=CAP)
    assert (response.decision.estimated_cost_usd, response.decision.cost_is_estimate) == (0.006, True)
    assert response.decision.unmeasured is None


def test_the_budget_error_is_a_router_error_and_names_nothing_when_nothing_was_skipped() -> None:
    assert issubclass(BudgetExceededError, WebRouterError)
    assert BudgetExceededError({}).skipped == {}


# -- The estimate, from the shipped price list --------------------------------


def test_a_search_is_priced_at_its_depth_and_research_at_its_effort() -> None:
    deep = Attempt(tool="search", provider="nimble", request=SearchRequest(query="q", search_depth="deep"))
    assert attempt_cost(deep) == pytest.approx(0.015)
    low = Attempt(tool="research", provider="parallel", request=ResearchRequest(query="q", effort="low"))
    assert attempt_cost(low) == pytest.approx(0.005)
    unknown = Attempt(tool="research", provider="nimble", request=ResearchRequest(query="q"))
    assert attempt_cost(unknown) is None, "nimble's default effort is priced unknown"


def test_an_extract_is_priced_per_url() -> None:
    two = Attempt(tool="extract", provider="tavily", request=ExtractRequest(urls=["https://a.test", "https://b.test"]))
    assert attempt_cost(two) == pytest.approx(0.016)


def test_the_p90_comes_from_the_measured_latency() -> None:
    measured = Attempt(tool="search", provider="nimble", request=SearchRequest(query="q"))
    assert attempt_p90(measured) == pytest.approx(1.331)
    # Research has no latency in the price list: its p90 is the effort table's, at the default effort when unset.
    default = Attempt(tool="research", provider="parallel", request=ResearchRequest(query="q"))
    assert attempt_p90(default) == measured_efforts("parallel")["medium"]["latency_p90_s"]
    assert attempt_p90(Attempt(tool="research", provider="you", request=ResearchRequest(query="q"))) is None


# -- What makes a price unknown -------------------------------------------------


def test_under_a_cost_cap_provider_options_make_the_price_unknown(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.001, None), second=(0.001, None), third=(0.001, None))
    with pytest.raises(BudgetExceededError) as raised:
        call(chain, CAP, provider_options={"depth": "deep"})
    assert chain.called == []
    assert set(raised.value.skipped.values()) == {SKIP_PRICE_UNKNOWN}, "options can change the mode billed"


def test_with_no_cost_cap_provider_options_run_and_their_cost_stays_unknown(chain: Chain, priced: Table) -> None:
    priced.update(first=(0.001, None))
    response = call(chain, Budget(time_limit_s=60), provider_options={"depth": "deep"})
    assert chain.called == ["first"]
    assert (response.decision.estimated_cost_usd, response.decision.unmeasured) == (None, _ids(chain, "first"))


def test_provider_options_make_the_p90_unknown_as_well_as_the_price() -> None:
    plain = Attempt(tool="search", provider="nimble", request=SearchRequest(query="q"))
    optioned = Attempt(tool="search", provider="nimble", request=SearchRequest(query="q", provider_options={"x": 1}))
    assert attempt_p90(plain) is not None
    assert attempt_p90(optioned) is None


def test_a_row_whose_estimate_is_only_a_floor_has_an_unknown_price() -> None:
    steps = Attempt(tool="browse", provider="browser-use", request=BrowseRequest(task="t"))
    tokens = Attempt(tool="answer", provider="brave", request=AnswerRequest(query="q"))
    assert [attempt_cost(steps), attempt_cost(tokens)] == [None, None], "per-step and token charges come on top"
    assert prices.estimate("browser-use", "browse") == pytest.approx(0.01), "the list still states the floor"


def test_a_route_that_refuses_a_named_backend_stops_the_budgeted_call(chain: Chain, priced: Table) -> None:
    priced.update(second=(0.001, None))
    refuse_second = CallRoute(before_attempt=lambda attempt: attempt.provider != "second")
    with call_route(refuse_second), pytest.raises(BudgetExceededError) as raised:
        call(chain, CAP, provider="second")
    assert chain.called == []
    assert raised.value.skipped == {_ids(chain, "second")[0]: SKIP_ROUTE_REFUSED}


@pytest.mark.parametrize(("cost", "fits"), [(0.01 + 5e-10, True), (0.01 + 1e-6, False)], ids=["in-tolerance", "over"])
def test_the_candidate_builder_and_the_ledger_draw_the_cap_at_one_line(
    chain: Chain, priced: Table, cost: float, fits: bool
) -> None:
    from web_router.routing.candidates import _over_budget

    assert (_over_budget(cost, None, CAP) is None) is fits
    priced.update(first=(cost, None), second=(0.0, None))
    call(chain, CAP)
    assert (chain.called[0] == "first") is fits
